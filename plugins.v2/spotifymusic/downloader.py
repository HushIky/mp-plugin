"""音乐下载与元数据标签加工引擎。

基于 yt-dlp 抓取音轨并转码，随后使用 mutagen 进行全套元数据打标、封面内嵌与歌词注入。
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import requests
from app.log import logger

try:
    import yt_dlp
    _HAS_YTDLP = True
except ImportError:
    yt_dlp = None
    _HAS_YTDLP = False

# Mutagen 标签处理器
try:
    from mutagen.flac import FLAC, Picture
    from mutagen.id3 import (
        APIC,
        ID3,
        TALB,
        TCON,
        TDRC,
        TIT2,
        TPE1,
        TPE2,
        TPOS,
        TRCK,
        USLT,
    )
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4, MP4Cover
    from mutagen.oggopus import OggOpus
    from mutagen.oggvorbis import OggVorbis
    _HAS_MUTAGEN = True
except ImportError:
    _HAS_MUTAGEN = False

from .lyrics import fetch_lyrics
from .matcher import match_youtube_track

_INVALID_FS_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_DEFAULT_YT_CLIENTS = ['ios', 'android', 'web_embedded', 'mweb', 'tv']


def sanitize_filename(text: str) -> str:
    """清理文件名中的非法字符。"""
    safe = _INVALID_FS_CHARS.sub('', text or '').strip().strip('.')
    return safe or 'unknown'


class MusicDownloader:
    """封装 yt-dlp 下载与 mutagen 标签加工的下载引擎。"""

    def __init__(
        self,
        temp_dir: Path | str,
        audio_format: str = 'mp3',
        audio_bitrate: str = '320',
        download_lyrics: bool = True,
        embed_lyrics: bool = True,
        save_lrc_file: bool = True,
        proxy: Optional[str] = None,
        cookie_file: Optional[str] = None,
        po_token: Optional[str] = None,
    ):
        self.temp_dir = Path(temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.audio_format = audio_format.lower()
        self.audio_bitrate = str(audio_bitrate)
        self.download_lyrics = download_lyrics
        self.embed_lyrics = embed_lyrics
        self.save_lrc_file = save_lrc_file
        self.proxy = proxy
        self.cookie_file = cookie_file
        self.po_token = po_token

    def download_and_tag(
        self,
        track_info: Dict[str, Any],
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> Tuple[Path, Optional[Path]]:
        """
        下载单曲并完成元数据打标。

        :param track_info: 单曲元数据字典
        :param progress_callback: 进度回调 (percentage, status_text)
        :return: (音频文件路径, 歌词文件路径)
        """
        title = track_info.get("title") or track_info.get("name") or "Unknown Title"
        artist = track_info.get("artist") or ", ".join(track_info.get("artists") or []) or "Unknown Artist"
        album = track_info.get("album") or ""
        duration = int(track_info.get("duration") or 0)
        cover_url = track_info.get("cover_url") or ""
        youtube_id = track_info.get("youtube_id")

        if progress_callback:
            progress_callback(5.0, "正在匹配音源...")

        # 1. 匹配 YouTube 音源
        if not youtube_id:
            matched = match_youtube_track(
                title=title,
                artist=artist,
                album=album,
                duration=duration,
                proxy=self.proxy,
            )
            if not matched:
                raise RuntimeError(f"未能匹配到可用的 YouTube 音源: {artist} - {title}")
            youtube_id, _ = matched

        video_url = f"https://www.youtube.com/watch?v={youtube_id}"
        base_name = sanitize_filename(f"{artist} - {title}")
        temp_out_template = str(self.temp_dir / f"{base_name}_%(id)s.%(ext)s")

        # 2. 配置 yt-dlp 选项
        def ytdl_hook(d: Dict[str, Any]) -> None:
            if not progress_callback:
                return
            status = d.get("status")
            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes") or 0
                if total:
                    pct = min(90.0, 10.0 + (downloaded / total) * 80.0)
                    speed = d.get("speed") or 0
                    speed_str = f" ({speed / 1024 / 1024:.1f} MB/s)" if speed else ""
                    progress_callback(pct, f"下载中 {pct:.0f}%{speed_str}")
            elif status == "finished":
                progress_callback(92.0, "正在转码音频...")

        ydl_opts: Dict[str, Any] = {
            'format': 'bestaudio/best',
            'outtmpl': temp_out_template,
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'nocheckcertificate': True,
            'overwrites': True,
            'progress_hooks': [ytdl_hook],
            'retries': 5,
            'socket_timeout': 30,
            'extractor_args': {
                'youtube': {'player_client': _DEFAULT_YT_CLIENTS}
            },
            'postprocessors': [
                {
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': self.audio_format,
                    'preferredquality': self.audio_bitrate,
                }
            ],
        }
        if self.proxy:
            ydl_opts['proxy'] = self.proxy
        if self.cookie_file and Path(self.cookie_file).exists():
            ydl_opts['cookiefile'] = self.cookie_file

        # 3. 执行音频下载与转码
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])
        except Exception as err:
            raise RuntimeError(f"yt-dlp 下载音频失败: {err}") from err

        # 查找转码生成的音频文件
        audio_file = None
        for candidate in self.temp_dir.glob(f"{base_name}_{youtube_id}.*"):
            if candidate.is_file() and candidate.suffix.lower().lstrip('.') == self.audio_format:
                audio_file = candidate
                break
        if not audio_file:
            # 兜底查找
            for candidate in self.temp_dir.glob(f"{base_name}_{youtube_id}.*"):
                if candidate.is_file():
                    audio_file = candidate
                    break

        if not audio_file or not audio_file.exists():
            raise FileNotFoundError(f"未找到下载生成的音频文件 ({base_name})")

        if progress_callback:
            progress_callback(95.0, "正在写入元数据与封面歌词...")

        # 4. 下载高清封面与抓取歌词
        cover_bytes = None
        if cover_url:
            try:
                proxies = {'http': self.proxy, 'https': self.proxy} if self.proxy else None
                resp = requests.get(cover_url, proxies=proxies, timeout=10)
                if resp.status_code == 200:
                    cover_bytes = resp.content
            except Exception as e:
                logger.debug(f"下载专辑封面失败: {e}")

        lyrics_res = None
        lrc_file = None
        if self.download_lyrics:
            lyrics_res = fetch_lyrics(
                title=title,
                artist=artist,
                album=album,
                duration=duration,
                proxy=self.proxy,
            )
            # 生成本地独立 .lrc 歌词文件
            if self.save_lrc_file and lyrics_res and lyrics_res.synced:
                lrc_path = audio_file.with_suffix('.lrc')
                try:
                    lrc_path.write_text(lyrics_res.synced, encoding='utf-8')
                    lrc_file = lrc_path
                except Exception as e:
                    logger.debug(f"保存 .lrc 文件失败: {e}")

        # 5. 使用 mutagen 注入 ID3 / FLAC / MP4 标签
        self._tag_audio_file(
            audio_path=audio_file,
            track_info=track_info,
            cover_bytes=cover_bytes,
            lyrics_res=lyrics_res,
        )

        if progress_callback:
            progress_callback(99.0, "预处理完成")

        return audio_file, lrc_file

    def _tag_audio_file(
        self,
        audio_path: Path,
        track_info: Dict[str, Any],
        cover_bytes: Optional[bytes] = None,
        lyrics_res: Optional[Any] = None,
    ) -> None:
        """根据音频格式执行具体的元数据打标。"""
        ext = audio_path.suffix.lower()
        title = str(track_info.get("title") or track_info.get("name") or "")
        artists = track_info.get("artists") or []
        artist = str(track_info.get("artist") or ", ".join(artists) or "")
        album = str(track_info.get("album") or "")
        track_num = int(track_info.get("track_number") or 1)
        disc_num = int(track_info.get("disc_number") or 1)
        release_date = str(track_info.get("release_date") or "")
        year = release_date[:4] if len(release_date) >= 4 else ""

        lyrics_text = ""
        if self.embed_lyrics and lyrics_res:
            lyrics_text = lyrics_res.synced or lyrics_res.plain or ""

        try:
            if ext == '.mp3':
                self._tag_mp3(
                    audio_path, title, artist, album, track_num, disc_num, year, release_date,
                    cover_bytes, lyrics_text
                )
            elif ext == '.flac':
                self._tag_flac(
                    audio_path, title, artist, album, track_num, disc_num, year, release_date,
                    cover_bytes, lyrics_text
                )
            elif ext in ('.m4a', '.mp4', '.aac'):
                self._tag_mp4(
                    audio_path, title, artist, album, track_num, disc_num, year,
                    cover_bytes, lyrics_text
                )
            elif ext in ('.opus', '.ogg'):
                self._tag_ogg(
                    audio_path, title, artist, album, track_num, disc_num, year,
                    cover_bytes, lyrics_text
                )
        except Exception as e:
            logger.warning(f"为文件 {audio_path.name} 打标失败: {e}")

    @staticmethod
    def _tag_mp3(path: Path, title: str, artist: str, album: str, track: int, disc: int,
                 year: str, date: str, cover: Optional[bytes], lyrics: str) -> None:
        audio = MP3(str(path))
        if audio.tags is None:
            audio.add_tags()
        tags: ID3 = audio.tags

        tags.add(TIT2(encoding=3, text=title))
        tags.add(TPE1(encoding=3, text=artist))
        tags.add(TPE2(encoding=3, text=artist))
        if album:
            tags.add(TALB(encoding=3, text=album))
        tags.add(TRCK(encoding=3, text=f"{track}"))
        tags.add(TPOS(encoding=3, text=f"{disc}"))
        if year:
            tags.add(TDRC(encoding=3, text=date or year))
        if cover:
            tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=cover))
        if lyrics:
            tags.add(USLT(encoding=3, lang='eng', desc='', text=lyrics))
        audio.save()

    @staticmethod
    def _tag_flac(path: Path, title: str, artist: str, album: str, track: int, disc: int,
                  year: str, date: str, cover: Optional[bytes], lyrics: str) -> None:
        audio = FLAC(str(path))
        audio['title'] = title
        audio['artist'] = artist
        audio['albumartist'] = artist
        if album:
            audio['album'] = album
        audio['tracknumber'] = str(track)
        audio['discnumber'] = str(disc)
        if date or year:
            audio['date'] = date or year
        if lyrics:
            audio['lyrics'] = lyrics
        if cover:
            pic = Picture()
            pic.type = 3
            pic.mime = 'image/jpeg'
            pic.desc = 'Cover'
            pic.data = cover
            audio.clear_pictures()
            audio.add_picture(pic)
        audio.save()

    @staticmethod
    def _tag_mp4(path: Path, title: str, artist: str, album: str, track: int, disc: int,
                 year: str, cover: Optional[bytes], lyrics: str) -> None:
        audio = MP4(str(path))
        audio['\xa9nam'] = [title]
        audio['\xa9ART'] = [artist]
        audio['aART'] = [artist]
        if album:
            audio['\xa9alb'] = [album]
        audio['trkn'] = [(track, 0)]
        audio['disk'] = [(disc, 0)]
        if year:
            audio['\xa9day'] = [year]
        if lyrics:
            audio['\xa9lyr'] = [lyrics]
        if cover:
            audio['covr'] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
        audio.save()

    @staticmethod
    def _tag_ogg(path: Path, title: str, artist: str, album: str, track: int, disc: int,
                 year: str, cover: Optional[bytes], lyrics: str) -> None:
        try:
            audio = OggOpus(str(path))
        except Exception:
            audio = OggVorbis(str(path))
        audio['title'] = title
        audio['artist'] = artist
        audio['albumartist'] = artist
        if album:
            audio['album'] = album
        audio['tracknumber'] = str(track)
        audio['discnumber'] = str(disc)
        if year:
            audio['date'] = year
        if lyrics:
            audio['lyrics'] = lyrics
        audio.save()
