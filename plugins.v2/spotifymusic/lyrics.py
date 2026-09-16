"""歌词抓取模块，基于 LRCLIB API 获取纯文本与时间轴同步歌词。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests
from app.log import logger

LRCLIB_BASE = 'https://lrclib.net/api'
_USER_AGENT = 'MoviePilot-SpotifyMusic (https://github.com/HushIky/mp-plugin)'


@dataclass
class LyricsResult:
    """歌词结果封装。"""
    plain: Optional[str] = None
    synced: Optional[str] = None

    def has_any(self) -> bool:
        """判断是否有任意可用歌词。"""
        return bool(self.plain or self.synced)


def fetch_lyrics(
    title: str,
    artist: str,
    album: Optional[str] = None,
    duration: Optional[int] = None,
    proxy: Optional[str] = None,
) -> Optional[LyricsResult]:
    """从 LRCLIB 检索匹配歌词。"""
    title = (title or '').strip()
    artist = (artist or '').strip()
    if not title or not artist:
        return None

    params: Dict[str, Any] = {
        'track_name': title,
        'artist_name': artist,
    }
    if album:
        params['album_name'] = album.strip()
    if duration and duration > 0:
        params['duration'] = int(duration)

    proxies = {'http': proxy, 'https': proxy} if proxy else None

    try:
        response = requests.get(
            f'{LRCLIB_BASE}/get',
            params=params,
            headers={'User-Agent': _USER_AGENT},
            proxies=proxies,
            timeout=8,
        )
        if response.status_code == 200:
            data = response.json()
            plain = (data.get('plainLyrics') or '').strip() or None
            synced = (data.get('syncedLyrics') or '').strip() or None
            if plain or synced:
                return LyricsResult(plain=plain, synced=synced)
    except Exception as e:
        logger.debug(f"LRCLIB 精准匹配歌词失败: {e}")

    # 尝试模糊搜索
    try:
        search_res = requests.get(
            f'{LRCLIB_BASE}/search',
            params={'q': f'{artist} {title}'},
            headers={'User-Agent': _USER_AGENT},
            proxies=proxies,
            timeout=8,
        )
        if search_res.status_code == 200:
            items = search_res.json()
            if isinstance(items, list) and items:
                best = items[0]
                plain = (best.get('plainLyrics') or '').strip() or None
                synced = (best.get('syncedLyrics') or '').strip() or None
                if plain or synced:
                    return LyricsResult(plain=plain, synced=synced)
    except Exception as e:
        logger.debug(f"LRCLIB 模糊搜索歌词失败: {e}")

    return None
