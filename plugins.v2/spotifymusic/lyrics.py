"""歌词抓取模块，支持网易云精准歌词与 LRCLIB 同步/纯文本歌词检索。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests
from app.log import logger

LRCLIB_BASE = 'https://lrclib.net/api'
_NETEASE_LYRIC_URL = 'https://music.163.com/api/song/lyric'
_USER_AGENT = 'MoviePilot-SpotifyMusic (https://github.com/HushIky/mp-plugin)'
_NETEASE_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    ),
    'Referer': 'https://music.163.com/',
}
_LRC_TIME_RE = re.compile(r'\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]')


@dataclass
class LyricsResult:
    """歌词结果封装。"""
    plain: Optional[str] = None
    synced: Optional[str] = None

    def has_any(self) -> bool:
        """判断是否有任意可用歌词。"""
        return bool(self.plain or self.synced)


def _fetch_netease_lyrics(
    song_id: int,
    proxies: Optional[Dict[str, str]] = None,
) -> Optional[LyricsResult]:
    """通过已对齐的网易云歌曲 ID 获取精准 LRC 歌词（兼容中文/英文/日文/韩文等多语言版本）。"""
    try:
        resp = requests.get(
            _NETEASE_LYRIC_URL,
            params={'id': int(song_id), 'lv': 1, 'kv': 1, 'tv': -1},
            headers=_NETEASE_HEADERS,
            proxies=proxies,
            timeout=8,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        raw_lrc = str((data.get('lrc') or {}).get('lyric') or '').strip()
        if not raw_lrc:
            return None

        if _LRC_TIME_RE.search(raw_lrc):
            plain_lines = [
                _LRC_TIME_RE.sub('', line).strip()
                for line in raw_lrc.splitlines()
            ]
            plain = '\n'.join(l for l in plain_lines if l) or None
            return LyricsResult(plain=plain, synced=raw_lrc)
        return LyricsResult(plain=raw_lrc, synced=None)
    except Exception as e:
        logger.debug(f"网易云歌词抓取失败 (song_id={song_id}): {e}")
        return None


def fetch_lyrics(
    title: str,
    artist: str,
    album: Optional[str] = None,
    duration: Optional[int] = None,
    proxy: Optional[str] = None,
    netease_song_id: Optional[int] = None,
) -> Optional[LyricsResult]:
    """检索匹配歌词（命中网易云交叉匹配 ID 时优先使用网易云精准歌词，否则回退 LRCLIB）。"""
    proxies = {'http': proxy, 'https': proxy} if proxy else None

    if netease_song_id:
        ne_res = _fetch_netease_lyrics(int(netease_song_id), proxies=proxies)
        if ne_res and ne_res.has_any():
            return ne_res

    title = (title or '').strip()
    artist = (artist or '').strip()
    if not title or not artist:
        return None

    primary_artist = re.split(r'[,，;；/、]', artist, maxsplit=1)[0].strip() or artist

    params: Dict[str, Any] = {
        'track_name': title,
        'artist_name': primary_artist,
    }
    if album:
        params['album_name'] = album.strip()
    if duration and duration > 0:
        params['duration'] = int(duration)

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
            params={'q': f'{primary_artist} {title}'},
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
