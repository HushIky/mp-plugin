"""Spotify 公开 Embed 页面解析器。

无需官方 API Key 或凭据，直接解析 open.spotify.com/embed 页面公开的 Next.js 数据。
支持解析 Track、Album、Playlist、Artist 实体及曲目列表。
"""

from __future__ import annotations

import base64
import json
import re
import time
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


def _extract_image_url(obj: Any) -> str:
    """从 Spotify 各种层级对象（visualIdentity, coverArt, images, image 列表/字典）中提取最高分辨率图片 URL。"""
    if not obj:
        return ''
    if isinstance(obj, str):
        return obj if obj.startswith('http') else ''
    if isinstance(obj, list):
        valid = [img for img in obj if isinstance(img, dict) and img.get('url')]
        if valid:
            valid.sort(key=lambda x: int(x.get('maxHeight') or x.get('height') or 0), reverse=True)
            return str(valid[0].get('url', ''))
        for item in obj:
            res = _extract_image_url(item)
            if res:
                return res
    if isinstance(obj, dict):
        if obj.get('url') and isinstance(obj['url'], str) and obj['url'].startswith('http'):
            return str(obj['url'])
        if isinstance(obj.get('sources'), list) and obj['sources']:
            return _extract_image_url(obj['sources'])
        if isinstance(obj.get('image'), (list, dict)):
            return _extract_image_url(obj['image'])
        if isinstance(obj.get('images'), list):
            return _extract_image_url(obj['images'])
        if isinstance(obj.get('coverArt'), dict):
            return _extract_image_url(obj['coverArt'])
        if isinstance(obj.get('visualIdentity'), dict):
            return _extract_image_url(obj['visualIdentity'])
    return ''


def resolve_spotify_entity(
    url: str,
    proxy: Optional[str] = None,
    spotify_client_id: Optional[str] = None,
    spotify_client_secret: Optional[str] = None,
) -> Dict[str, Any]:
    """解析任意 Spotify 链接并返回结构化的元数据。"""
    parsed = parse_spotify_url(url)
    if not parsed:
        raise ValueError(f'无效的 Spotify 链接: {url}')
    
    kind, spotify_id = parsed

    # 尝试 Embed 页面解析
    data = None
    embed_err = None
    try:
        data = fetch_embed_json(kind, spotify_id, proxy=proxy)
    except Exception as e:
        embed_err = e

    entity = None
    if data:
        try:
            entity = _extract_entity(data)
        except Exception as e:
            embed_err = e

    if entity:
        if kind == 'track':
            return _format_track(entity, spotify_id)
        elif kind == 'album':
            return _format_album(entity, spotify_id)
        elif kind == 'playlist':
            return _format_playlist(entity, spotify_id)
        elif kind == 'artist':
            return _format_artist(
                entity,
                spotify_id,
                proxy=proxy,
                spotify_client_id=spotify_client_id,
                spotify_client_secret=spotify_client_secret,
            )
        else:
            raise ValueError(f'暂不支持的 Spotify 实体类型: {kind}')

    # 若 Embed 解析失败但配置了官方 API Key，尝试官方 API 回退
    if spotify_client_id and spotify_client_secret:
        if kind == 'artist':
            return _format_artist(
                {},
                spotify_id,
                proxy=proxy,
                spotify_client_id=spotify_client_id,
                spotify_client_secret=spotify_client_secret,
            )

    if embed_err:
        raise embed_err
    raise ValueError(f'无法解析 Spotify 实体: {url}')


def _format_track(entity: Dict[str, Any], spotify_id: str) -> Dict[str, Any]:
    """格式化单曲。"""
    artists = [
        a.get('name', '').strip()
        for a in (entity.get('artists') or [])
        if isinstance(a, dict) and a.get('name')
    ]
    if not artists and entity.get('subtitle'):
        sub = str(entity['subtitle'])
        artists = [a.strip() for a in sub.replace('\xa0', ' ').split(',') if a.strip()]
    if not artists and entity.get('artist'):
        artists = [str(entity.get('artist')).strip()]

    cover_url = _extract_image_url(entity.get('coverArt')) or _extract_image_url(entity)

    album_name = ''
    album_artists: List[str] = []
    album_obj = entity.get('album')
    if isinstance(album_obj, dict):
        album_name = album_obj.get('name', '')
        album_artists = [
            a.get('name', '').strip()
            for a in (album_obj.get('artists') or [])
            if isinstance(a, dict) and a.get('name')
        ]
        if not album_artists and album_obj.get('subtitle'):
            sub = str(album_obj['subtitle'])
            album_artists = [a.strip() for a in sub.replace('\xa0', ' ').split(',') if a.strip()]
    if not album_artists:
        album_artists = list(artists)

    duration_ms = entity.get('duration') or 0
    duration_sec = int(duration_ms / 1000) if duration_ms else 0

    release_date = str(entity.get('releaseDate') or '')
    artist_str = ', '.join(artists) if artists else 'Unknown Artist'
    album_artist_str = ', '.join(album_artists) if album_artists else artist_str

    track_data = {
        'type': 'track',
        'spotify_id': spotify_id,
        'title': entity.get('name') or entity.get('title', 'Unknown Track'),
        'artists': artists,
        'artist': artist_str,
        'album_artists': album_artists,
        'album_artist': album_artist_str,
        'album': album_name or entity.get('name', ''),
        'cover_url': cover_url,
        'duration': duration_sec,
        'release_date': release_date,
        'track_number': entity.get('trackNumber') or 1,
        'disc_number': entity.get('discNumber') or 1,
        'total_tracks': 1,
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
        a.get('name', '').strip()
        for a in (entity.get('artists') or [])
        if isinstance(a, dict) and a.get('name')
    ]
    if not artists and entity.get('subtitle'):
        sub = str(entity['subtitle'])
        artists = [a.strip() for a in sub.replace('\xa0', ' ').split(',') if a.strip()]
    if not artists and entity.get('artist'):
        artists = [str(entity['artist']).strip()]
    album_artist_str = ', '.join(artists) if artists else 'Unknown Artist'

    cover_url = _extract_image_url(entity.get('coverArt')) or _extract_image_url(entity)

    raw_tracks = entity.get('trackList') or []
    if not raw_tracks and isinstance(entity.get('tracks'), dict):
        raw_tracks = entity['tracks'].get('items') or []

    total_tracks_count = len(raw_tracks)
    tracks: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw_tracks, 1):
        if not isinstance(item, dict):
            continue
        t_id = item.get('id') or item.get('uri', '').split(':')[-1] or f'{spotify_id}_{idx}'
        t_artists = [
            a.get('name', '').strip()
            for a in (item.get('artists') or [])
            if isinstance(a, dict) and a.get('name')
        ]
        if not t_artists and item.get('subtitle'):
            sub = str(item['subtitle'])
            t_artists = [a.strip() for a in sub.replace('\xa0', ' ').split(',') if a.strip()]
        if not t_artists:
            t_artists = list(artists)
        t_artist_str = ', '.join(t_artists) if t_artists else album_artist_str

        dur_ms = item.get('duration') or item.get('duration_ms') or 0
        dur_sec = int(dur_ms / 1000) if dur_ms else 0

        tracks.append({
            'type': 'track',
            'spotify_id': t_id,
            'title': item.get('title') or item.get('name', f'Track {idx}'),
            'artists': t_artists,
            'artist': t_artist_str,
            'album_artists': artists,
            'album_artist': album_artist_str,
            'album': album_name,
            'cover_url': cover_url,
            'duration': dur_sec,
            'track_number': item.get('trackNumber') or idx,
            'disc_number': item.get('discNumber') or 1,
            'total_tracks': total_tracks_count,
            'release_date': str(entity.get('releaseDate') or ''),
            'url': f'https://open.spotify.com/track/{t_id}',
        })

    return {
        'type': 'album',
        'spotify_id': spotify_id,
        'name': album_name,
        'artist': album_artist_str,
        'album_artist': album_artist_str,
        'cover_url': cover_url,
        'tracks': tracks,
        'total_tracks': len(tracks),
        'url': f'https://open.spotify.com/album/{spotify_id}',
    }


def _format_playlist(entity: Dict[str, Any], spotify_id: str) -> Dict[str, Any]:
    """格式化播放列表。"""
    playlist_name = entity.get('name') or entity.get('title', 'Unknown Playlist')
    cover_url = _extract_image_url(entity.get('coverArt')) or _extract_image_url(entity)

    raw_tracks = entity.get('trackList') or []
    if not raw_tracks and isinstance(entity.get('tracks'), dict):
        raw_tracks = entity['tracks'].get('items') or []

    tracks: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw_tracks, 1):
        if not isinstance(item, dict):
            continue
        t_id = item.get('id') or item.get('uri', '').split(':')[-1] or f'{spotify_id}_{idx}'
        t_artists = [
            a.get('name', '').strip()
            for a in (item.get('artists') or [])
            if isinstance(a, dict) and a.get('name')
        ]
        if not t_artists and item.get('subtitle'):
            sub = str(item['subtitle'])
            t_artists = [a.strip() for a in sub.replace('\xa0', ' ').split(',') if a.strip()]
        t_artist_str = ', '.join(t_artists) if t_artists else 'Unknown Artist'

        album_obj = item.get('album', {}) if isinstance(item.get('album'), dict) else {}
        album_name = album_obj.get('name', '')
        album_artists = [
            a.get('name', '').strip()
            for a in (album_obj.get('artists') or [])
            if isinstance(a, dict) and a.get('name')
        ]
        if not album_artists and album_obj.get('subtitle'):
            sub = str(album_obj['subtitle'])
            album_artists = [a.strip() for a in sub.replace('\xa0', ' ').split(',') if a.strip()]
        album_artist_str = ', '.join(album_artists) if album_artists else t_artist_str

        dur_ms = item.get('duration') or item.get('duration_ms') or 0
        dur_sec = int(dur_ms / 1000) if dur_ms else 0

        tracks.append({
            'type': 'track',
            'spotify_id': t_id,
            'title': item.get('title') or item.get('name', f'Track {idx}'),
            'artists': t_artists,
            'artist': t_artist_str,
            'album_artists': album_artists,
            'album_artist': album_artist_str,
            'album': album_name,
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


def _format_artist(
    entity: Dict[str, Any],
    spotify_id: str,
    proxy: Optional[str] = None,
    spotify_client_id: Optional[str] = None,
    spotify_client_secret: Optional[str] = None,
) -> Dict[str, Any]:
    """格式化艺术家，解析艺术家头像、Top Tracks 曲目列表及全部 Release 专辑与单曲。"""
    artist_name = entity.get('name') or entity.get('title', 'Unknown Artist')
    cover_url = _extract_image_url(entity)

    # 1. 解析 Release (专辑/单曲/合集)
    discography = entity.get('discography') or {}
    all_releases: List[Dict[str, Any]] = []

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
                'cover_url': _extract_image_url(release.get('coverArt')) or cover_url,
            })

    # 2. 解析艺术家热门曲目 (trackList)
    raw_tracks = entity.get('trackList') or []
    if not raw_tracks and isinstance(entity.get('tracks'), dict):
        raw_tracks = entity['tracks'].get('items') or []
    elif not raw_tracks and isinstance(entity.get('tracks'), list):
        raw_tracks = entity['tracks']

    tracks: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw_tracks, 1):
        if not isinstance(item, dict):
            continue
        t_id = item.get('id') or item.get('uri', '').split(':')[-1] or f'{spotify_id}_{idx}'

        # 解析艺术家列表（subtitle 包含 'Artist1,\xa0Artist2'）
        subtitle = str(item.get('subtitle') or '')
        if subtitle:
            t_artists = [a.strip() for a in subtitle.replace('\xa0', ' ').split(',') if a.strip()]
        else:
            t_artists = [
                a.get('name', '').strip()
                for a in (item.get('artists') or [])
                if isinstance(a, dict) and a.get('name')
            ]
        if not t_artists:
            t_artists = [artist_name]
        t_artist_str = ', '.join(t_artists)

        album_obj = item.get('album', {}) if isinstance(item.get('album'), dict) else {}
        album_name = album_obj.get('name') or artist_name
        track_cover = _extract_image_url(item) or _extract_image_url(album_obj) or cover_url

        dur_ms = item.get('duration') or item.get('duration_ms') or 0
        dur_sec = int(dur_ms / 1000) if dur_ms else 0

        tracks.append({
            'type': 'track',
            'spotify_id': t_id,
            'title': item.get('title') or item.get('name', f'Track {idx}'),
            'artists': t_artists,
            'artist': t_artist_str,
            'album_artists': [artist_name],
            'album_artist': artist_name,
            'album': album_name,
            'cover_url': track_cover,
            'duration': dur_sec,
            'track_number': item.get('trackNumber') or idx,
            'disc_number': item.get('discNumber') or 1,
            'total_tracks': len(raw_tracks),
            'release_date': str(item.get('releaseDate') or entity.get('releaseDate') or ''),
            'url': f'https://open.spotify.com/track/{t_id}',
        })

    # 3. 若配置了官方 Spotify API，增强曲目与 releases
    if spotify_client_id and spotify_client_secret:
        try:
            if not cover_url or artist_name == 'Unknown Artist':
                art_detail = get_spotify_artist_details(spotify_id, spotify_client_id, spotify_client_secret, proxy=proxy)
                if art_detail:
                    if art_detail.get('name') and artist_name == 'Unknown Artist':
                        artist_name = art_detail['name']
                    if art_detail.get('cover_url') and not cover_url:
                        cover_url = art_detail['cover_url']

            api_tracks = get_spotify_artist_top_tracks(spotify_id, spotify_client_id, spotify_client_secret, proxy=proxy)
            if api_tracks:
                tracks = api_tracks

            if not all_releases:
                api_releases = get_spotify_artist_albums(spotify_id, spotify_client_id, spotify_client_secret, proxy=proxy)
                if api_releases:
                    all_releases = api_releases
        except Exception as e:
            logger.warning(f"通过 Spotify 官方 API 补充艺术家信息异常: {e}")

    return {
        'type': 'artist',
        'spotify_id': spotify_id,
        'name': artist_name,
        'artist': artist_name,
        'cover_url': cover_url,
        'releases': all_releases,
        'total_releases': len(all_releases),
        'tracks': tracks,
        'total_tracks': len(tracks),
        'url': f'https://open.spotify.com/artist/{spotify_id}',
    }


def get_spotify_artist_details(
    spotify_id: str,
    client_id: str,
    client_secret: str,
    proxy: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """调用 Spotify 官方 API 获取艺术家详情。"""
    token = get_spotify_access_token(client_id, client_secret, proxy=proxy)
    if not token:
        return None
    url = f"https://api.spotify.com/v1/artists/{spotify_id}"
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        resp = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": _USER_AGENT,
            },
            proxies=proxies,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "spotify_id": data.get("id") or spotify_id,
            "name": data.get("name") or "Unknown Artist",
            "cover_url": _extract_image_url(data.get("images")),
            "genres": data.get("genres") or [],
            "popularity": data.get("popularity") or 0,
        }
    except Exception as e:
        logger.warning(f"Spotify 官方获取艺术家详情异常: {e}")
        return None


def get_spotify_artist_top_tracks(
    spotify_id: str,
    client_id: str,
    client_secret: str,
    market: str = "US",
    proxy: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """调用 Spotify 官方 API 获取艺术家 Top Tracks。"""
    token = get_spotify_access_token(client_id, client_secret, proxy=proxy)
    if not token:
        return []
    url = f"https://api.spotify.com/v1/artists/{spotify_id}/top-tracks"
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        resp = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": _USER_AGENT,
            },
            params={"market": market},
            proxies=proxies,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("tracks") or []
        results = []
        for idx, item in enumerate(items, 1):
            artists_list = [
                a.get("name", "")
                for a in (item.get("artists") or [])
                if isinstance(a, dict) and a.get("name")
            ]
            artist_str = ", ".join(artists_list) if artists_list else "未知艺术家"
            album_info = item.get("album") or {}
            album_artists_list = [
                a.get("name", "")
                for a in (album_info.get("artists") or [])
                if isinstance(a, dict) and a.get("name")
            ]
            album_artist_str = (
                ", ".join(album_artists_list) if album_artists_list else artist_str
            )
            cover_url = _extract_image_url(album_info.get("images"))
            dur_ms = item.get("duration_ms") or 0
            dur_sec = dur_ms // 1000
            results.append({
                "type": "track",
                "spotify_id": item.get("id"),
                "title": item.get("name"),
                "artists": artists_list,
                "artist": artist_str,
                "album_artists": album_artists_list,
                "album_artist": album_artist_str,
                "album": album_info.get("name") or "",
                "cover_url": cover_url,
                "duration": dur_sec,
                "track_number": item.get("track_number") or idx,
                "disc_number": item.get("disc_number") or 1,
                "total_tracks": len(items),
                "release_date": str(album_info.get("release_date") or ""),
                "url": f"https://open.spotify.com/track/{item.get('id')}",
            })
        return results
    except Exception as e:
        logger.warning(f"Spotify 官方获取艺术家 Top Tracks 异常: {e}")
        return []


def get_spotify_artist_albums(
    spotify_id: str,
    client_id: str,
    client_secret: str,
    limit: int = 50,
    max_pages: int = 10,
    proxy: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """调用 Spotify 官方 API 分页获取艺术家全量 Releases (Albums / Singles / Compilations)。"""
    token = get_spotify_access_token(client_id, client_secret, proxy=proxy)
    if not token:
        return []
    url = f"https://api.spotify.com/v1/artists/{spotify_id}/albums"
    proxies = {"http": proxy, "https": proxy} if proxy else None
    results: List[Dict[str, Any]] = []
    seen_ids = set()
    offset = 0
    page_limit = min(50, limit) if limit > 0 else 50
    pages = 0

    try:
        while pages < max_pages:
            resp = requests.get(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": _USER_AGENT,
                },
                params={
                    "include_groups": "album,single,compilation",
                    "limit": page_limit,
                    "offset": offset,
                },
                proxies=proxies,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items") or []
            if not items:
                break
            for item in items:
                rel_id = item.get("id")
                if not rel_id or rel_id in seen_ids:
                    continue
                seen_ids.add(rel_id)
                cover_url = _extract_image_url(item.get("images"))
                results.append({
                    "spotify_id": rel_id,
                    "name": item.get("name", ""),
                    "type": item.get("album_type") or item.get("type", "album"),
                    "release_date": str(item.get("release_date") or ""),
                    "cover_url": cover_url,
                    "total_tracks": item.get("total_tracks") or 0,
                })
            offset += len(items)
            total = data.get("total") or 0
            pages += 1
            if offset >= total or len(items) < page_limit:
                break
        return results
    except Exception as e:
        logger.warning(f"Spotify 官方获取艺术家 Releases 异常: {e}")
        return results


def get_spotify_album_tracks(
    album_id: str,
    client_id: str,
    client_secret: str,
    album_name: str = "",
    album_cover: str = "",
    release_date: str = "",
    proxy: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """调用 Spotify 官方 API 获取专辑全部曲目。"""
    token = get_spotify_access_token(client_id, client_secret, proxy=proxy)
    if not token:
        return []
    url = f"https://api.spotify.com/v1/albums/{album_id}/tracks"
    proxies = {"http": proxy, "https": proxy} if proxy else None
    results = []
    offset = 0
    limit = 50
    try:
        while True:
            resp = requests.get(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": _USER_AGENT,
                },
                params={"limit": limit, "offset": offset},
                proxies=proxies,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items") or []
            if not items:
                break
            for idx, item in enumerate(items, offset + 1):
                t_id = item.get("id")
                if not t_id:
                    continue
                artists_list = [
                    a.get("name", "")
                    for a in (item.get("artists") or [])
                    if isinstance(a, dict) and a.get("name")
                ]
                artist_str = ", ".join(artists_list) if artists_list else "未知艺术家"
                dur_ms = item.get("duration_ms") or 0
                dur_sec = dur_ms // 1000
                results.append({
                    "type": "track",
                    "spotify_id": t_id,
                    "title": item.get("name"),
                    "artists": artists_list,
                    "artist": artist_str,
                    "album_artists": artists_list,
                    "album_artist": artist_str,
                    "album": album_name,
                    "cover_url": album_cover,
                    "duration": dur_sec,
                    "track_number": item.get("track_number") or idx,
                    "disc_number": item.get("disc_number") or 1,
                    "total_tracks": data.get("total") or len(items),
                    "release_date": release_date,
                    "url": f"https://open.spotify.com/track/{t_id}",
                })
            offset += len(items)
            total = data.get("total") or 0
            if offset >= total or len(items) < limit:
                break
        return results
    except Exception as e:
        logger.warning(f"Spotify 官方获取专辑 {album_id} 曲目异常: {e}")
        return results


def get_spotify_artist_all_tracks(
    spotify_id: str,
    client_id: str,
    client_secret: str,
    max_albums: int = 50,
    proxy: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """调用 Spotify 官方 API 获取艺术家全部 Releases 并展开为全部曲目（可达上千首）。"""
    releases = get_spotify_artist_albums(spotify_id, client_id, client_secret, limit=50, proxy=proxy)
    all_tracks = []
    seen_track_ids = set()
    for rel in releases[:max_albums]:
        rel_id = rel.get("spotify_id")
        if not rel_id:
            continue
        tracks = get_spotify_album_tracks(
            album_id=rel_id,
            client_id=client_id,
            client_secret=client_secret,
            album_name=rel.get("name", ""),
            album_cover=rel.get("cover_url", ""),
            release_date=rel.get("release_date", ""),
            proxy=proxy,
        )
        for t in tracks:
            tid = t.get("spotify_id")
            if tid and tid not in seen_track_ids:
                seen_track_ids.add(tid)
                all_tracks.append(t)
    return all_tracks


_spotify_token: Optional[str] = None
_spotify_token_expires_at: float = 0.0


def get_spotify_access_token(
    client_id: str,
    client_secret: str,
    proxy: Optional[str] = None,
) -> Optional[str]:
    """使用 Client Credentials Flow 获取官方 Spotify Access Token。"""
    global _spotify_token, _spotify_token_expires_at
    if not client_id or not client_secret:
        return None
    now = time.time()
    if _spotify_token and now < _spotify_token_expires_at - 60:
        return _spotify_token

    url = "https://accounts.spotify.com/api/token"
    raw_creds = f"{client_id.strip()}:{client_secret.strip()}".encode("utf-8")
    auth_header = base64.b64encode(raw_creds).decode("utf-8")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Basic {auth_header}",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": _USER_AGENT,
            },
            data={"grant_type": "client_credentials"},
            proxies=proxies,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        _spotify_token = data.get("access_token")
        expires_in = int(data.get("expires_in") or 3600)
        _spotify_token_expires_at = now + expires_in
        return _spotify_token
    except Exception as e:
        logger.warning(f"获取 Spotify 官方 Access Token 失败: {e}")
        return None


def search_spotify_tracks(
    query: str,
    client_id: str,
    client_secret: str,
    limit: int = 15,
    proxy: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """调用 Spotify 官方 API 搜索曲目。"""
    token = get_spotify_access_token(client_id, client_secret, proxy=proxy)
    if not token:
        return []

    url = "https://api.spotify.com/v1/search"
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        resp = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": _USER_AGENT,
            },
            params={"q": query, "type": "track", "limit": limit},
            proxies=proxies,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("tracks", {}).get("items") or []
        results = []
        for item in items:
            artists_list = [
                a.get("name", "")
                for a in (item.get("artists") or [])
                if isinstance(a, dict) and a.get("name")
            ]
            artist_str = ", ".join(artists_list) if artists_list else "未知艺术家"
            album_info = item.get("album") or {}
            album_artists_list = [
                a.get("name", "")
                for a in (album_info.get("artists") or [])
                if isinstance(a, dict) and a.get("name")
            ]
            album_artist_str = (
                ", ".join(album_artists_list) if album_artists_list else artist_str
            )
            album_images = album_info.get("images") or []
            cover_url = album_images[0].get("url") if album_images else ""
            dur_ms = item.get("duration_ms") or 0
            dur_sec = dur_ms // 1000
            m, s = divmod(dur_sec, 60)
            dur_str = f"{m:02d}:{s:02d}"

            results.append({
                "id": item.get("id"),
                "spotify_id": item.get("id"),
                "title": item.get("name"),
                "artist": artist_str,
                "album": album_info.get("name") or "",
                "album_artist": album_artist_str,
                "duration": dur_sec,
                "duration_str": dur_str,
                "cover_url": cover_url,
                "source": "spotify",
                "url": f"https://open.spotify.com/track/{item.get('id')}",
            })
        return results
    except Exception as e:
        logger.warning(f"Spotify 官方搜索异常: {e}")
        return []

