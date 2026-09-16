"""Spotify 公开 Embed 页面解析器。

无需官方 API Key 或凭据，直接解析 open.spotify.com/embed 页面公开的 Next.js 数据。
支持解析 Track、Album、Playlist、Artist 实体及曲目列表。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

import requests
from app.log import logger

SPOTIFY_URL_RE = re.compile(
    r'(?:https?://)?(?:open\.)?spotify\.com/'
    r'(?:intl-[a-z]{2}/)?'
    r'(?P<type>track|album|playlist|artist|episode|show)/'
    r'(?P<id>[A-Za-z0-9]+)'
)

_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
)


def parse_spotify_url(url: str) -> Optional[Tuple[str, str]]:
    """解析 Spotify 链接或 URI，返回 (type, id)。"""
    if not url:
        return None
    url = url.strip()
    if url.startswith('spotify:'):
        try:
            _, kind, sid = url.split(':', 2)
            return kind, sid
        except ValueError:
            return None
    match = SPOTIFY_URL_RE.search(url)
    if not match:
        return None
    return match.group('type'), match.group('id')


def fetch_embed_json(kind: str, spotify_id: str, proxy: Optional[str] = None) -> Dict[str, Any]:
    """请求 open.spotify.com/embed/{kind}/{spotify_id} 并提取 __NEXT_DATA__。"""
    url = f'https://open.spotify.com/embed/{kind}/{spotify_id}'
    proxies = {'http': proxy, 'https': proxy} if proxy else None
    response = requests.get(
        url,
        headers={
            'User-Agent': _USER_AGENT,
            'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
        },
        proxies=proxies,
        timeout=15,
    )
    response.raise_for_status()

    match = re.search(
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        response.text,
        re.DOTALL,
    )
    if not match:
        raise ValueError(f'未在 Spotify 页面中找到有效数据 ({kind}/{spotify_id})')
    
    return json.loads(match.group(1))


def _extract_entity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """从 __NEXT_DATA__ payload 中提取主实体对象。"""
    page_props = payload.get('props', {}).get('pageProps', {}) or {}
    candidates = [
        page_props.get('state', {}).get('data', {}).get('entity')
        if isinstance(page_props.get('state'), dict)
        else None,
        page_props.get('entity'),
        page_props.get('data', {}).get('entity')
        if isinstance(page_props.get('data'), dict)
        else None,
    ]
    for c in candidates:
        if isinstance(c, dict) and (c.get('name') or c.get('title')):
            return c
    raise ValueError('无法从 Spotify 响应中解析出有效实体')


def resolve_spotify_entity(url: str, proxy: Optional[str] = None) -> Dict[str, Any]:
    """解析任意 Spotify 链接并返回结构化的元数据。"""
    parsed = parse_spotify_url(url)
    if not parsed:
        raise ValueError(f'无效的 Spotify 链接: {url}')
    
    kind, spotify_id = parsed
    data = fetch_embed_json(kind, spotify_id, proxy=proxy)
    entity = _extract_entity(data)

    if kind == 'track':
        return _format_track(entity, spotify_id)
    elif kind == 'album':
        return _format_album(entity, spotify_id)
    elif kind == 'playlist':
        return _format_playlist(entity, spotify_id)
    elif kind == 'artist':
        return _format_artist(entity, spotify_id, proxy=proxy)
    else:
        raise ValueError(f'暂不支持的 Spotify 实体类型: {kind}')


def _format_track(entity: Dict[str, Any], spotify_id: str) -> Dict[str, Any]:
    """格式化单曲。"""
    artists = [
        a.get('name', '')
        for a in (entity.get('artists') or [])
        if isinstance(a, dict) and a.get('name')
    ]
    if not artists and entity.get('artist'):
        artists = [str(entity.get('artist'))]

    cover_url = ''
    cover_art = entity.get('coverArt') or {}
    if isinstance(cover_art, dict) and cover_art.get('sources'):
        sources = cover_art['sources']
        if sources:
            cover_url = sources[0].get('url', '')

    album_name = ''
    album_obj = entity.get('album')
    if isinstance(album_obj, dict):
        album_name = album_obj.get('name', '')

    duration_ms = entity.get('duration') or 0
    duration_sec = int(duration_ms / 1000) if duration_ms else 0

    release_date = str(entity.get('releaseDate') or '')

    track_data = {
        'type': 'track',
        'spotify_id': spotify_id,
        'title': entity.get('name') or entity.get('title', 'Unknown Track'),
        'artists': artists,
        'artist': ' / '.join(artists) if artists else 'Unknown Artist',
        'album': album_name or entity.get('name', ''),
        'cover_url': cover_url,
        'duration': duration_sec,
        'release_date': release_date,
        'track_number': entity.get('trackNumber') or 1,
        'disc_number': entity.get('discNumber') or 1,
        'url': f'https://open.spotify.com/track/{spotify_id}',
    }
    return {
        'type': 'track',
        'spotify_id': spotify_id,
        'name': track_data['title'],
        'tracks': [track_data],
        'total_tracks': 1,
        'cover_url': cover_url,
        'url': track_data['url'],
    }


def _format_album(entity: Dict[str, Any], spotify_id: str) -> Dict[str, Any]:
    """格式化专辑。"""
    album_name = entity.get('name') or entity.get('title', 'Unknown Album')
    artists = [
        a.get('name', '')
        for a in (entity.get('artists') or [])
        if isinstance(a, dict) and a.get('name')
    ]
    cover_url = ''
    cover_art = entity.get('coverArt') or {}
    if isinstance(cover_art, dict) and cover_art.get('sources'):
        sources = cover_art['sources']
        if sources:
            cover_url = sources[0].get('url', '')

    raw_tracks = entity.get('trackList') or []
    if not raw_tracks and isinstance(entity.get('tracks'), dict):
        raw_tracks = entity['tracks'].get('items') or []

    tracks: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw_tracks, 1):
        if not isinstance(item, dict):
            continue
        t_id = item.get('id') or item.get('uri', '').split(':')[-1] or f'{spotify_id}_{idx}'
        t_artists = [
            a.get('name', '')
            for a in (item.get('artists') or [])
            if isinstance(a, dict) and a.get('name')
        ] or artists
        dur_ms = item.get('duration') or item.get('duration_ms') or 0
        dur_sec = int(dur_ms / 1000) if dur_ms else 0

        tracks.append({
            'type': 'track',
            'spotify_id': t_id,
            'title': item.get('title') or item.get('name', f'Track {idx}'),
            'artists': t_artists,
            'artist': ' / '.join(t_artists) if t_artists else 'Unknown Artist',
            'album': album_name,
            'cover_url': cover_url,
            'duration': dur_sec,
            'track_number': item.get('trackNumber') or idx,
            'disc_number': item.get('discNumber') or 1,
            'release_date': str(entity.get('releaseDate') or ''),
            'url': f'https://open.spotify.com/track/{t_id}',
        })

    return {
        'type': 'album',
        'spotify_id': spotify_id,
        'name': album_name,
        'artist': ' / '.join(artists),
        'cover_url': cover_url,
        'tracks': tracks,
        'total_tracks': len(tracks),
        'url': f'https://open.spotify.com/album/{spotify_id}',
    }


def _format_playlist(entity: Dict[str, Any], spotify_id: str) -> Dict[str, Any]:
    """格式化播放列表。"""
    playlist_name = entity.get('name') or entity.get('title', 'Unknown Playlist')
    cover_url = ''
    cover_art = entity.get('coverArt') or {}
    if isinstance(cover_art, dict) and cover_art.get('sources'):
        sources = cover_art['sources']
        if sources:
            cover_url = sources[0].get('url', '')

    raw_tracks = entity.get('trackList') or []
    if not raw_tracks and isinstance(entity.get('tracks'), dict):
        raw_tracks = entity['tracks'].get('items') or []

    tracks: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw_tracks, 1):
        if not isinstance(item, dict):
            continue
        t_id = item.get('id') or item.get('uri', '').split(':')[-1] or f'{spotify_id}_{idx}'
        t_artists = [
            a.get('name', '')
            for a in (item.get('artists') or [])
            if isinstance(a, dict) and a.get('name')
        ]
        dur_ms = item.get('duration') or item.get('duration_ms') or 0
        dur_sec = int(dur_ms / 1000) if dur_ms else 0

        tracks.append({
            'type': 'track',
            'spotify_id': t_id,
            'title': item.get('title') or item.get('name', f'Track {idx}'),
            'artists': t_artists,
            'artist': ' / '.join(t_artists) if t_artists else 'Unknown Artist',
            'album': item.get('album', {}).get('name', '') if isinstance(item.get('album'), dict) else '',
            'cover_url': cover_url,
            'duration': dur_sec,
            'track_number': item.get('trackNumber') or idx,
            'disc_number': item.get('discNumber') or 1,
            'url': f'https://open.spotify.com/track/{t_id}',
        })

    return {
        'type': 'playlist',
        'spotify_id': spotify_id,
        'name': playlist_name,
        'cover_url': cover_url,
        'tracks': tracks,
        'total_tracks': len(tracks),
        'url': f'https://open.spotify.com/playlist/{spotify_id}',
    }


def _format_artist(entity: Dict[str, Any], spotify_id: str, proxy: Optional[str] = None) -> Dict[str, Any]:
    """格式化艺术家，解析艺术家主页的全部 Release 专辑与单曲。"""
    artist_name = entity.get('name') or entity.get('title', 'Unknown Artist')
    cover_url = ''
    visual_img = entity.get('visualIdentity', {}).get('image') or {}
    if isinstance(visual_img, dict) and visual_img.get('sources'):
        cover_url = visual_img['sources'][0].get('url', '')

    discography = entity.get('discography') or {}
    all_releases: List[Dict[str, Any]] = []

    # 包含 albums, singles, compilations 等分段
    for group_key in ('albums', 'singles', 'compilations'):
        group = discography.get(group_key) or {}
        items = group.get('items') or []
        for release in items:
            if not isinstance(release, dict):
                continue
            rel_id = release.get('id') or release.get('uri', '').split(':')[-1]
            if not rel_id:
                continue
            all_releases.append({
                'spotify_id': rel_id,
                'name': release.get('name', ''),
                'type': release.get('type', 'album'),
                'release_date': str(release.get('releaseDate') or ''),
                'cover_url': (release.get('coverArt', {}).get('sources') or [{}])[0].get('url', ''),
            })

    return {
        'type': 'artist',
        'spotify_id': spotify_id,
        'name': artist_name,
        'cover_url': cover_url,
        'releases': all_releases,
        'total_releases': len(all_releases),
        'tracks': [],  # 艺术家的曲目按需从各 release 专辑进一步拉取
        'total_tracks': 0,
        'url': f'https://open.spotify.com/artist/{spotify_id}',
    }
