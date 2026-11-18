"""下载任务队列管理器（线程安全版）。

基于标准库 queue.Queue 与 threading.Thread 实现任务排队、并发控制、下载进度更新与状态流转，
完全解耦运行态事件循环，可在任意同步/异步线程中安全初始化与调用。
"""

from __future__ import annotations

import json
import queue
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from app.log import logger

from .db import MusicDatabase
from .downloader import MusicDownloader
from .organizer import transfer_to_destination
from .zh_metadata import enrich_track_zh_metadata


@dataclass
class DownloadJob:
    """下载任务封装。"""
    task_id: str
    track_info: Dict[str, Any]
    subscription_id: Optional[int] = None
    playlist_name: Optional[str] = None


class DownloadQueueManager:
    """管理下载队列与工作线程池。"""

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
        generate_m3u: bool = False,
        zh_metadata_match: bool = True,
        proxy: Optional[str] = None,
        on_task_completed: Optional[Callable[[Dict[str, Any], Path], None]] = None,
    ) -> None:
        self.db = db
        self.temp_dir = Path(temp_dir)
        self.target_dir = Path(target_dir)
        self.template = template
        self.audio_format = audio_format
        self.audio_bitrate = audio_bitrate
        self.max_parallel = max(1, int(max_parallel))
        self.download_lyrics = download_lyrics
        self.generate_m3u = generate_m3u
        self.zh_metadata_match = zh_metadata_match
        self.proxy = proxy
        self.on_task_completed = on_task_completed

        self._queue: queue.Queue[Optional[DownloadJob]] = queue.Queue()
        self._semaphore = threading.Semaphore(self.max_parallel)
        self._workers: list[threading.Thread] = []
        self._scheduler_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        self._lock = threading.Lock()
        self._cancelled_tasks: set[str] = set()
        self._enqueued_tasks: set[str] = set()
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
        generate_m3u: Optional[bool] = None,
        zh_metadata_match: Optional[bool] = None,
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
        if generate_m3u is not None:
            self.generate_m3u = generate_m3u
        if zh_metadata_match is not None:
            self.zh_metadata_match = zh_metadata_match
        if proxy is not None:
            self.proxy = proxy or None
        if max_parallel and max_parallel != self.max_parallel:
            self.max_parallel = max(1, int(max_parallel))
            self._semaphore = threading.Semaphore(self.max_parallel)

        self._downloader = self._build_downloader()

    def _enqueue_job(self, job: DownloadJob) -> bool:
        """线程安全地将任务推入内存队列，自动拦截已在队列或执行中的重复任务。"""
        with self._lock:
            if job.task_id in self._enqueued_tasks:
                return False
            self._enqueued_tasks.add(job.task_id)
        self._queue.put(job)
        return True

    def recover_unfinished_tasks(self, reset_interrupted: bool = False) -> int:
        """
        从数据库恢复因容器重启、插件更新或重载而滞留在数据库中的未完成任务（pending 及中断的进行中任务）。
        """
        tasks = (
            self.db.list_unfinished_tasks()
            if reset_interrupted
            else self.db.list_tasks(status="pending", limit=500)
        )
        if not reset_interrupted:
            tasks = list(reversed(tasks))

        recovered = 0
        for task in tasks:
            task_id = task.get("id")
            if not task_id:
                continue
            with self._lock:
                if task_id in self._cancelled_tasks or task_id in self._enqueued_tasks:
                    continue
            st = task.get("status")
            if reset_interrupted and st in ("matching", "downloading", "processing", "tagging"):
                self.db.update_task_progress(task_id, status="pending", progress=0.0, speed="", error_msg="")
            job = DownloadJob(
                task_id=task_id,
                track_info=self._restore_track_payload(task),
                subscription_id=task.get("subscription_id"),
                playlist_name=task.get("playlist_name"),
            )
            if self._enqueue_job(job):
                recovered += 1

        if recovered:
            logger.info(f"SpotifyMusic 已从数据库自动恢复并唤醒 {recovered} 个未完成排队任务")
        return recovered

    def start(self, num_workers: Optional[int] = None) -> None:
        """启动后台工作线程并自动恢复数据库中滞留的未完成任务。"""
        if self._running:
            return
        worker_count = max(2, int(num_workers or self.max_parallel))
        self._running = True
        self._stop_event.clear()
        for i in range(worker_count):
            t = threading.Thread(
                target=self._worker_loop,
                args=(i,),
                daemon=True,
                name=f"SpotifyMusicWorker-{i}",
            )
            t.start()
            self._workers.append(t)
        try:
            self.recover_unfinished_tasks(reset_interrupted=True)
        except Exception as e:
            logger.error(f"启动恢复未完成任务异常: {e}")
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            daemon=True,
            name="SpotifyMusicDelayScheduler",
        )
        self._scheduler_thread.start()
        logger.info(f"SpotifyMusic 任务队列已启动，工作线程数: {worker_count}，最大并发数: {self.max_parallel}")

    def stop(self) -> None:
        """停止队列并清理工作线程。"""
        self._running = False
        self._stop_event.set()
        for _ in self._workers:
            self._queue.put(None)
        for t in self._workers:
            t.join(timeout=2.0)
        self._workers.clear()
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            self._scheduler_thread.join(timeout=2.0)
        self._scheduler_thread = None
        with self._lock:
            self._enqueued_tasks.clear()
        logger.info("SpotifyMusic 任务队列已停止")

    def submit_track(
        self,
        track_info: Dict[str, Any],
        subscription_id: Optional[int] = None,
        playlist_name: Optional[str] = None,
        delay_hours: float = 0.0,
    ) -> str:
        """提交单曲到下载队列（支持按小时延迟执行）。"""
        task_id = str(uuid.uuid4())
        title = track_info.get("title") or track_info.get("name") or "Unknown"
        artists = track_info.get("artists") or []
        artist = track_info.get("artist") or ", ".join(artists) or "Unknown"
        album = track_info.get("album") or ""
        cover_url = track_info.get("cover_url") or ""
        spotify_id = track_info.get("spotify_id")

        delay_val = max(0.0, float(delay_hours or 0.0))
        is_delayed = delay_val > 0
        execute_after = (
            (datetime.now(timezone.utc) + timedelta(hours=delay_val)).isoformat()
            if is_delayed
            else None
        )
        status = "delayed" if is_delayed else "pending"

        # 写入数据库记录（持久化保存完整 track_payload 以便延迟到期或重启后恢复）
        self.db.create_task(
            task_id=task_id,
            title=title,
            artist=artist,
            album=album,
            cover_url=cover_url,
            spotify_id=spotify_id,
            subscription_id=subscription_id,
            status=status,
            execute_after=execute_after,
            track_payload=track_info,
            playlist_name=playlist_name,
        )

        if not is_delayed:
            job = DownloadJob(
                task_id=task_id,
                track_info=track_info,
                subscription_id=subscription_id,
                playlist_name=playlist_name,
            )
            self._enqueue_job(job)
        return task_id

    @staticmethod
    def _restore_track_payload(task: Dict[str, Any]) -> Dict[str, Any]:
        """从数据库任务记录中恢复完整 track_info 字典。"""
        raw_payload = task.get("track_payload")
        if raw_payload:
            try:
                parsed = json.loads(raw_payload)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
        return {
            "title": task.get("title"),
            "artist": task.get("artist"),
            "album": task.get("album"),
            "cover_url": task.get("cover_url"),
            "spotify_id": task.get("spotify_id"),
        }

    def run_delayed_task_now(self, task_id: str) -> bool:
        """提前立即执行指定延迟任务，或手动重新唤醒滞留的 pending 任务。"""
        task = self.db.get_task(task_id)
        if not task:
            return False
        st = task.get("status")
        if st == "delayed":
            if not self.db.mark_delayed_task_pending(task_id):
                return False
        elif st != "pending":
            return False

        with self._lock:
            self._cancelled_tasks.discard(task_id)
        job = DownloadJob(
            task_id=task_id,
            track_info=self._restore_track_payload(task),
            subscription_id=task.get("subscription_id"),
            playlist_name=task.get("playlist_name"),
        )
        self._enqueue_job(job)
        return True

    def _dispatch_due_delayed_tasks(self) -> int:
        """扫描并派发所有已到达执行时间的延迟任务。"""
        due_tasks = self.db.list_due_delayed_tasks()
        dispatched = 0
        for task in due_tasks:
            task_id = task.get("id")
            if not task_id:
                continue
            with self._lock:
                if task_id in self._cancelled_tasks:
                    continue
            if self.db.mark_delayed_task_pending(task_id):
                job = DownloadJob(
                    task_id=task_id,
                    track_info=self._restore_track_payload(task),
                    subscription_id=task.get("subscription_id"),
                    playlist_name=task.get("playlist_name"),
                )
                if self._enqueue_job(job):
                    dispatched += 1
        if dispatched:
            logger.info(f"SpotifyMusic 延迟调度器已唤醒 {dispatched} 个到期任务进入下载队列")
        return dispatched

    def _scheduler_loop(self) -> None:
        """延迟任务定时唤醒与滞留 pending 任务自愈循环。"""
        while self._running and not self._stop_event.is_set():
            try:
                self._dispatch_due_delayed_tasks()
                self.recover_unfinished_tasks(reset_interrupted=False)
            except Exception as e:
                logger.debug(f"延迟调度器检查异常: {e}")
            if self._stop_event.wait(30.0):
                break

    def cancel_task(self, task_id: str) -> bool:
        """取消下载队列中的任务并从数据库删除。"""
        with self._lock:
            self._cancelled_tasks.add(task_id)
            self._enqueued_tasks.discard(task_id)
        return self.db.delete_task(task_id)

    def requeue_task(self, task_id: str) -> bool:
        """重新将现有数据库中的任务排入执行队列（避免重复生成记录）。"""
        task = self.db.get_task(task_id)
        if not task:
            return False
        with self._lock:
            self._cancelled_tasks.discard(task_id)
        self.db.update_task_progress(task_id, status="pending", progress=0.0, speed="", error_msg="")
        track_info = self._restore_track_payload(task)
        job = DownloadJob(
            task_id=task_id,
            track_info=track_info,
            subscription_id=task.get("subscription_id"),
            playlist_name=task.get("playlist_name"),
        )
        self._enqueue_job(job)
        return True

    def _worker_loop(self, worker_id: int) -> None:
        """消费者循环。"""
        while self._running and not self._stop_event.is_set():
            try:
                try:
                    job = self._queue.get(timeout=1.0)
                except queue.Empty:
                    continue

                if job is None:
                    break

                try:
                    with self._lock:
                        if job.task_id in self._cancelled_tasks:
                            self._cancelled_tasks.discard(job.task_id)
                            continue

                    # 检查数据库中任务是否已被删除
                    task = self.db.get_task(job.task_id)
                    if not task:
                        continue

                    with self._semaphore:
                        # 再次校验是否在等待信号量期间被取消
                        with self._lock:
                            if job.task_id in self._cancelled_tasks:
                                self._cancelled_tasks.discard(job.task_id)
                                continue
                        task = self.db.get_task(job.task_id)
                        if not task:
                            continue

                        self._process_job(job)
                finally:
                    with self._lock:
                        self._enqueued_tasks.discard(job.task_id)
                    self._queue.task_done()
            except Exception as e:
                logger.error(f"工作线程 #{worker_id} 处理异常: {e}")

    def _process_job(self, job: DownloadJob) -> None:
        """执行单个下载与归档任务。"""
        task_id = job.task_id
        track = dict(job.track_info)
        if self.zh_metadata_match:
            try:
                enrich_track_zh_metadata(track, proxy=self.proxy)
            except Exception as e:
                logger.debug(f"中文元数据交叉匹配异常: {e}")

        title = track.get("title") or track.get("name") or "Unknown"
        artist = track.get("artist") or "Unknown"

        self.db.update_task_progress(task_id, status="downloading", progress=5.0)

        def progress_cb(pct: float, text: str) -> None:
            self.db.update_task_progress(task_id, status="downloading", progress=pct, speed=text)

        try:
            # 执行密集 I/O 与音轨转码
            audio_temp, lrc_temp = self._downloader.download_and_tag(
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
                generate_m3u=self.generate_m3u,
            )

            # 更新任务完成状态
            self.db.update_task_progress(
                task_id,
                status="completed",
                progress=100.0,
                file_path=str(final_audio),
            )

            # 如果归属于订阅，安全记录历史去重与统计
            if job.subscription_id and track.get("spotify_id"):
                try:
                    self.db.record_track_history(
                        sub_id=job.subscription_id,
                        track_spotify_id=track["spotify_id"],
                        track_name=title,
                        artist_name=artist,
                        album_name=track.get("album", ""),
                        status="downloaded",
                        file_path=str(final_audio),
                    )
                    self.db.update_subscription_stats(job.subscription_id, downloaded_increment=1)
                except Exception as ex:
                    logger.debug(f"记录订阅历史与更新统计异常: {ex}")

            # 触发完成回调 (如系统通知或媒体库刷新)
            if self.on_task_completed:
                try:
                    self.on_task_completed(track, final_audio)
                except Exception as ex:
                    logger.debug(f"执行任务完成回调异常: {ex}")

        except Exception as err:
            logger.error(f"下载任务 [{title} - {artist}] 失败: {err}")
            self.db.update_task_progress(task_id, status="failed", error_msg=str(err))

