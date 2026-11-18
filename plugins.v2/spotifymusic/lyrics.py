"""歌词抓取模块，支持网易云精准歌词与 LRCLIB 同步/纯文本歌词检索。"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests
from app.log import logger

LRCLIB_BASE = 'https://lrclib.net/api'
_NETEASE_LYRIC_URL = 'https://music.163.com/api/song/lyric'
_KUGOU_LYRIC_SEARCH_URL = 'http://krcs.kugou.com/search'
_KUGOU_LYRIC_DOWNLOAD_URL = 'http://lyrics.kugou.com/download'
_USER_AGENT = 'MoviePilot-SpotifyMusic (https://github.com/HushIky/mp-plugin)'
_NETEASE_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    ),
    'Referer': 'https://music.163.com/',
}
_LRC_TIME_RE = re.compile(r'\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]')
_LRC_META_TAG_RE = re.compile(r'^\[(?:id|ar|ti|al|by|hash|sign|qq|total|offset):.*\]$', re.IGNORECASE)


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


def _fetch_kugou_lyrics(
    file_hash: str,
    duration_sec: Optional[int] = None,
    proxies: Optional[Dict[str, str]] = None,
) -> Optional[LyricsResult]:
    """通过已对齐的酷狗曲目 hash 获取精准 LRC 歌词（覆盖腾讯音乐首发独占专辑）。"""
    try:
        dur_ms = int(duration_sec) * 1000 if duration_sec and duration_sec > 0 else ''
        s_resp = requests.get(
            _KUGOU_LYRIC_SEARCH_URL,
            params={'ver': 1, 'man': 'yes', 'client': 'mobi', 'keyword': '', 'duration': dur_ms, 'hash': file_hash},
            headers={'User-Agent': _NETEASE_HEADERS['User-Agent']},
            proxies=proxies,
            timeout=8,
        )
        if s_resp.status_code != 200:
            return None
        cands = s_resp.json().get('candidates') or []
        if not cands or not isinstance(cands[0], dict):
            return None

        c0 = cands[0]
        lrc_id = c0.get('id')
        accesskey = c0.get('accesskey')
        if not lrc_id or not accesskey:
            return None

        dl_resp = requests.get(
            _KUGOU_LYRIC_DOWNLOAD_URL,
            params={'ver': 1, 'client': 'pc', 'id': lrc_id, 'accesskey': accesskey, 'fmt': 'lrc', 'charset': 'utf8'},
            headers={'User-Agent': _NETEASE_HEADERS['User-Agent']},
            proxies=proxies,
            timeout=8,
        )
        if dl_resp.status_code != 200:
            return None
        b64_content = str(dl_resp.json().get('content') or '').strip()
        if not b64_content:
            return None

        decoded = base64.b64decode(b64_content).decode('utf-8', errors='ignore').strip()
        cleaned_lines = [
            line.rstrip('\r')
            for line in decoded.splitlines()
            if line.strip() and not _LRC_META_TAG_RE.match(line.strip())
        ]
        raw_lrc = '\n'.join(cleaned_lines).strip()
        if not raw_lrc:
            return None

        if _LRC_TIME_RE.search(raw_lrc):
            plain_lines = [_LRC_TIME_RE.sub('', line).strip() for line in cleaned_lines]
            plain = '\n'.join(l for l in plain_lines if l) or None
            return LyricsResult(plain=plain, synced=raw_lrc)
        return LyricsResult(plain=raw_lrc, synced=None)
    except Exception as e:
        logger.debug(f"酷狗歌词抓取失败 (hash={file_hash}): {e}")
        return None


def fetch_lyrics(
    title: str,
    artist: str,
    album: Optional[str] = None,
    duration: Optional[int] = None,
    proxy: Optional[str] = None,
    netease_song_id: Optional[int] = None,
    kugou_hash: Optional[str] = None,
) -> Optional[LyricsResult]:
    """检索匹配歌词（命中网易云/酷狗交叉匹配标识时优先使用对应官方精准歌词，否则回退 LRCLIB）。"""
    proxies = {'http': proxy, 'https': proxy} if proxy else None

    if netease_song_id:
        ne_res = _fetch_netease_lyrics(int(netease_song_id), proxies=proxies)
        if ne_res and ne_res.has_any():
            return ne_res

    if kugou_hash:
        kg_res = _fetch_kugou_lyrics(str(kugou_hash), duration_sec=duration, proxies=proxies)
        if kg_res and kg_res.has_any():
            return kg_res

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
