"""异步下载任务队列管理器。

实现并发限流、状态流转、实时进度更新、失败隔离与下载历史联动。
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from app.log import logger

from .db import MusicDatabase
from .downloader import MusicDownloader
from .organizer import transfer_to_destination


@dataclass
class DownloadJob:
    """下载任务封装。"""
    task_id: str
    track_info: Dict[str, Any]
    subscription_id: Optional[int] = None
    playlist_name: Optional[str] = None


class DownloadQueueManager:
    """管理异步下载队列与工作线程池。"""

    def __init__(
        self,
        db: MusicDatabase,
        temp_dir: Path,
        target_dir: Path,
        template: str,
        audio_format: str = 'mp3',
        audio_bitrate: str = '320',
        max_parallel: int = 3,
        download_lyrics: bool = True,
        proxy: Optional[str] = None,
        on_task_completed: Optional[Callable[[Dict[str, Any], Path], None]] = None,
    ) -> None:
        self.db = db
        self.temp_dir = Path(temp_dir)
        self.target_dir = Path(target_dir)
        self.template = template
        self.audio_format = audio_format
        self.audio_bitrate = audio_bitrate
        self.max_parallel = max_parallel
        self.download_lyrics = download_lyrics
        self.proxy = proxy
        self.on_task_completed = on_task_completed

        self._queue: asyncio.Queue[DownloadJob] = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(max_parallel)
        self._workers: list[asyncio.Task] = []
        self._running = False
        self._downloader = self._build_downloader()

    def _build_downloader(self) -> MusicDownloader:
        """根据当前配置构建底层下载引擎。"""
        return MusicDownloader(
            temp_dir=self.temp_dir,
            audio_format=self.audio_format,
            audio_bitrate=self.audio_bitrate,
            download_lyrics=self.download_lyrics,
            proxy=self.proxy,
        )

    def update_settings(
        self,
        target_dir: Optional[Path] = None,
        template: Optional[str] = None,
        audio_format: Optional[str] = None,
        audio_bitrate: Optional[str] = None,
        max_parallel: Optional[int] = None,
        download_lyrics: Optional[bool] = None,
        proxy: Optional[str] = None,
    ) -> None:
        """动态更新队列运行参数。"""
        if target_dir:
            self.target_dir = Path(target_dir)
        if template:
            self.template = template
        if audio_format:
            self.audio_format = audio_format
        if audio_bitrate:
            self.audio_bitrate = str(audio_bitrate)
        if download_lyrics is not None:
            self.download_lyrics = download_lyrics
        if proxy is not None:
            self.proxy = proxy or None
        if max_parallel and max_parallel != self.max_parallel:
            self.max_parallel = max_parallel
            self._semaphore = asyncio.Semaphore(max_parallel)

        self._downloader = self._build_downloader()

    def start(self, num_workers: int = 2) -> None:
        """启动后台工作协程。"""
        if self._running:
            return
        self._running = True
        for i in range(num_workers):
            task = asyncio.create_task(self._worker_loop(i))
            self._workers.append(task)
        logger.info(f"SpotifyMusic 任务队列已启动，工作协程数: {num_workers}，最大并发数: {self.max_parallel}")

    def stop(self) -> None:
        """停止队列并清理工作协程。"""
        self._running = False
        for t in self._workers:
            t.cancel()
        self._workers.clear()
        logger.info("SpotifyMusic 任务队列已停止")

    def submit_track(
        self,
        track_info: Dict[str, Any],
        subscription_id: Optional[int] = None,
        playlist_name: Optional[str] = None,
    ) -> str:
        """提交单曲到下载队列。"""
        task_id = str(uuid.uuid4())
        title = track_info.get("title") or track_info.get("name") or "Unknown"
        artists = track_info.get("artists") or []
        artist = track_info.get("artist") or ", ".join(artists) or "Unknown"
        album = track_info.get("album") or ""
        cover_url = track_info.get("cover_url") or ""
        spotify_id = track_info.get("spotify_id")

        # 写入数据库记录
        self.db.create_task(
            task_id=task_id,
            title=title,
            artist=artist,
            album=album,
            cover_url=cover_url,
            spotify_id=spotify_id,
            subscription_id=subscription_id,
        )

        job = DownloadJob(
            task_id=task_id,
            track_info=track_info,
            subscription_id=subscription_id,
            playlist_name=playlist_name,
        )
        self._queue.put_nowait(job)
        return task_id

    async def _worker_loop(self, worker_id: int) -> None:
        """消费者循环。"""
        while self._running:
            try:
                job = await self._queue.get()
                async with self._semaphore:
                    await self._process_job(job)
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"工作协程 #{worker_id} 处理异常: {e}")
                await asyncio.sleep(1)

    async def _process_job(self, job: DownloadJob) -> None:
        """执行单个下载与归档任务。"""
        task_id = job.task_id
        track = job.track_info
        title = track.get("title") or track.get("name") or "Unknown"
        artist = track.get("artist") or "Unknown"

        self.db.update_task_progress(task_id, status="downloading", progress=5.0)

        def progress_cb(pct: float, text: str) -> None:
            self.db.update_task_progress(task_id, status="downloading", progress=pct, speed=text)

        try:
            # 在独立线程池中执行密集 I/O 与音轨转码
            audio_temp, lrc_temp = await asyncio.to_thread(
                self._downloader.download_and_tag,
                track_info=track,
                progress_callback=progress_cb,
            )

            # 转移整理到最终目录
            self.db.update_task_progress(task_id, status="processing", progress=96.0)
            final_audio, final_lrc = transfer_to_destination(
                audio_temp_path=audio_temp,
                lrc_temp_path=lrc_temp,
                destination_root=self.target_dir,
                template=self.template,
                track_info=track,
                playlist_name=job.playlist_name,
            )

            # 更新任务完成状态
            self.db.update_task_progress(
                task_id,
                status="completed",
                progress=100.0,
                file_path=str(final_audio),
            )

            # 如果归属于订阅，记录历史去重与统计
            if job.subscription_id and track.get("spotify_id"):
                self.db.record_track_history(
                    sub_id=job.subscription_id,
                    track_spotify_id=track["spotify_id"],
                    track_name=title,
                    artist_name=artist,
                    album_name=track.get("album", ""),
                    status="downloaded",
                    file_path=str(final_audio),
                )
                self.db.update_subscription_stats(job.subscription_id, total_tracks=0, downloaded_increment=1)

            # 触发完成回调 (如系统通知或媒体库刷新)
            if self.on_task_completed:
                try:
                    self.on_task_completed(track, final_audio)
                except Exception as ex:
                    logger.debug(f"执行任务完成回调异常: {ex}")

        except Exception as err:
            logger.error(f"下载任务 [{title} - {artist}] 失败: {err}")
            self.db.update_task_progress(task_id, status="failed", error_msg=str(err))
