"""音频来源检索与音轨匹配器。

通过 Spotify、Apple Music (iTunes)、YouTube Music API 或 yt-dlp 搜索并精准匹配音乐音轨。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import requests
from app.log import logger

try:
    import yt_dlp
    _HAS_YTDLP = True
except ImportError:
    yt_dlp = None
    _HAS_YTDLP = False

try:
    from ytmusicapi import YTMusic
    _HAS_YTMUSICAPI = True
except ImportError:
    _HAS_YTMUSICAPI = False

_ytm_instance: Optional[YTMusic] = None


def get_ytm_client() -> Optional[YTMusic]:
    """获取单例 YTMusic 客户端。"""
    global _ytm_instance
    if not _HAS_YTMUSICAPI:
        return None
    if _ytm_instance is None:
        try:
            _ytm_instance = YTMusic()
        except Exception as e:
            logger.debug(f"初始化 YTMusic 客户端失败: {e}")
            return None
    return _ytm_instance


def _clean_string(text: str) -> str:
    """清理字符串用于比对。"""
    return re.sub(r'[\(\[\{].*?[\)\]\}]', '', text or '').strip().lower()


def _score_match(
    target_title: str,
    target_artist: str,
    target_duration: int,
    candidate_title: str,
    candidate_artists: List[str],
    candidate_duration: int,
) -> float:
    """计算候选音轨与目标曲目的匹配分值 (0~100)。"""
    score = 0.0
    c_title_clean = _clean_string(candidate_title)
    t_title_clean = _clean_string(target_title)
    t_artist_clean = _clean_string(target_artist)

    # 标题匹配
    if t_title_clean in c_title_clean or c_title_clean in t_title_clean:
        score += 45.0
    elif any(word in c_title_clean for word in t_title_clean.split() if len(word) > 1):
        score += 25.0

    # 艺术家匹配
    c_artists_str = ' '.join(candidate_artists).lower()
    if t_artist_clean in c_artists_str or any(a.lower() in t_artist_clean for a in candidate_artists):
        score += 35.0

    # 时长匹配（时长误差在 5 秒以内 +20 分，10 秒以内 +10 分）
    if target_duration > 0 and candidate_duration > 0:
        diff = abs(target_duration - candidate_duration)
        if diff <= 4:
            score += 20.0
        elif diff <= 10:
            score += 10.0
        elif diff > 30:
            score -= 20.0  # 可能是长视频或加长混音版

    # 惩罚项（卡拉OK、翻唱、现场版过滤）
    if 'instrumental' not in t_title_clean and 'karaoke' in c_title_clean:
        score -= 40.0
    if 'live' not in t_title_clean and 'live' in c_title_clean:
        score -= 20.0

    return score


def match_youtube_track(
    title: str,
    artist: str,
    album: Optional[str] = None,
    duration: int = 0,
    proxy: Optional[str] = None,
) -> Optional[Tuple[str, str]]:
    """
    为指定曲目检索匹配最佳的 YouTube 视频 ID 与标题。

    :return: (video_id, matched_title) 或 None
    """
    query = f"{artist} - {title}".strip()
    ytm = get_ytm_client()

    # 优先使用 YouTube Music 官方搜索
    if ytm:
        try:
            results = ytm.search(query, filter="songs", limit=5)
            best_id = None
            best_title = ""
            best_score = -1.0

            for r in results:
                vid = r.get("videoId")
                if not vid:
                    continue
                r_title = r.get("title", "")
                r_artists = [a.get("name", "") for a in (r.get("artists") or []) if isinstance(a, dict)]
                r_dur = r.get("duration_seconds") or 0
                score = _score_match(title, artist, duration, r_title, r_artists, r_dur)
                if score > best_score and score >= 40.0:
                    best_score = score
                    best_id = vid
                    best_title = r_title

            if best_id:
                logger.debug(f"YTMusic 匹配成功: {best_title} ({best_id}), 得分: {best_score}")
                return best_id, best_title
        except Exception as e:
            logger.debug(f"YTMusic 检索异常: {e}")

    # 回退到 yt-dlp 内置搜索
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': 'in_playlist',
        'skip_download': True,
        'noplaylist': True,
    }
    if proxy:
        ydl_opts['proxy'] = proxy

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_query = f"ytsearch5:{query} audio"
            info = ydl.extract_info(search_query, download=False)
            entries = (info or {}).get("entries") or []
            best_id = None
            best_title = ""
            best_score = -1.0

            for entry in entries:
                if not entry:
                    continue
                vid = entry.get("id")
                e_title = entry.get("title", "")
                e_uploader = entry.get("uploader") or entry.get("channel") or ""
                e_dur = int(entry.get("duration") or 0)
                score = _score_match(title, artist, duration, e_title, [e_uploader], e_dur)
                if score > best_score and score >= 30.0:
                    best_score = score
                    best_id = vid
                    best_title = e_title

            if best_id:
                logger.debug(f"yt-dlp 搜索匹配成功: {best_title} ({best_id}), 得分: {best_score}")
                return best_id, best_title
    except Exception as e:
        logger.warning(f"yt-dlp 搜索匹配音源失败: {e}")

    return None


def search_itunes_all(
    query: str,
    limit: int = 15,
    proxy: Optional[str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    通过 Apple iTunes Search API 免 Key 搜索音乐（单曲、专辑、艺术家）。
    无需配置任何凭据，国内/国际网络直连可用，提供高分辨率封面与丰富元数据。
    """
    query = (query or "").strip()
    if not query:
        return {"tracks": [], "albums": [], "artists": [], "playlists": []}

    proxies = {"http": proxy, "https": proxy} if proxy else None
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
    }
    base_url = "https://itunes.apple.com/search"

    tracks: List[Dict[str, Any]] = []
    albums: List[Dict[str, Any]] = []
    artists: List[Dict[str, Any]] = []

    # 1. 搜索单曲 (song)
    try:
        resp = requests.get(
            base_url,
            params={"term": query, "media": "music", "entity": "song", "limit": limit},
            headers=headers,
            proxies=proxies,
            timeout=10,
        )
        if resp.status_code == 200:
            for item in resp.json().get("results", []):
                t_id = str(item.get("trackId") or "")
                title = item.get("trackName") or ""
                if not t_id or not title:
                    continue
                artist = item.get("artistName") or "未知艺术家"
                album = item.get("collectionName") or ""
                dur_ms = item.get("trackTimeMillis") or 0
                dur_sec = dur_ms // 1000
                m, s = divmod(dur_sec, 60)
                dur_str = f"{m:02d}:{s:02d}" if dur_sec else ""
                artwork = item.get("artworkUrl100") or ""
                if artwork:
                    artwork = artwork.replace("100x100bb.jpg", "600x600bb.jpg")
                url = item.get("trackViewUrl") or item.get("collectionViewUrl") or f"https://music.apple.com/song/{t_id}"

                tracks.append({
                    "id": t_id,
                    "title": title,
                    "artist": artist,
                    "album": album,
                    "album_artist": artist,
                    "duration": dur_sec,
                    "duration_str": dur_str,
                    "cover_url": artwork,
                    "preview_url": item.get("previewUrl") or "",
                    "source": "itunes",
                    "url": url,
                })
    except Exception as e:
        logger.debug(f"iTunes 歌曲搜索异常: {e}")

    # 2. 搜索专辑 (album)
    try:
        resp_alb = requests.get(
            base_url,
            params={"term": query, "media": "music", "entity": "album", "limit": min(limit, 8)},
            headers=headers,
            proxies=proxies,
            timeout=10,
        )
        if resp_alb.status_code == 200:
            for item in resp_alb.json().get("results", []):
                alb_id = str(item.get("collectionId") or "")
                alb_name = item.get("collectionName") or ""
                if not alb_id or not alb_name:
                    continue
                artist = item.get("artistName") or "未知艺术家"
                release_date = item.get("releaseDate") or ""
                year = release_date.split("-")[0] if release_date else ""
                artwork = item.get("artworkUrl100") or ""
                if artwork:
                    artwork = artwork.replace("100x100bb.jpg", "600x600bb.jpg")
                url = item.get("collectionViewUrl") or f"https://music.apple.com/album/{alb_id}"

                albums.append({
                    "id": alb_id,
                    "title": alb_name,
                    "artist": artist,
                    "year": year,
                    "cover_url": artwork,
                    "source": "itunes",
                    "url": url,
                })
    except Exception as e:
        logger.debug(f"iTunes 专辑搜索异常: {e}")

    # 3. 搜索艺术家 (musicArtist)
    try:
        resp_art = requests.get(
            base_url,
            params={"term": query, "media": "music", "entity": "musicArtist", "limit": min(limit, 6)},
            headers=headers,
            proxies=proxies,
            timeout=10,
        )
        if resp_art.status_code == 200:
            for item in resp_art.json().get("results", []):
                art_id = str(item.get("artistId") or "")
                art_name = item.get("artistName") or ""
                if not art_id or not art_name:
                    continue
                url = item.get("artistLinkUrl") or f"https://music.apple.com/artist/{art_id}"
                artists.append({
                    "id": art_id,
                    "name": art_name,
                    "avatar_url": "",
                    "verified": True,
                    "source": "itunes",
                    "url": url,
                })
    except Exception as e:
        logger.debug(f"iTunes 艺术家搜索异常: {e}")

    return {
        "tracks": tracks,
        "albums": albums,
        "artists": artists,
        "playlists": [],
    }


def search_music_all(
    query: str,
    limit: int = 15,
    proxy: Optional[str] = None,
    spotify_client_id: Optional[str] = None,
    spotify_client_secret: Optional[str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    全分类搜索音乐（单曲、专辑、艺术家、歌单）。
    多级自动回退机制：
    1. Spotify 全分类搜索（免 Key GraphQL 或官方 API）；
    2. YouTube Music 全分类搜索；
    3. Apple iTunes Search API 免 Key 全局零配置搜索；
    4. yt-dlp 单曲搜索。
    """
    query = (query or "").strip()
    if not query:
        return {"tracks": [], "albums": [], "artists": [], "playlists": []}

    # 1. 优先使用 Spotify 全分类搜索（免 Key 或官方凭据均支持）
    try:
        from . import spotify
        sp_results = spotify.search_spotify_all(
            query=query,
            client_id=spotify_client_id,
            client_secret=spotify_client_secret,
            limit=limit,
            proxy=proxy,
        )
        if any(sp_results.get(k) for k in ("tracks", "albums", "artists", "playlists")):
            return sp_results
    except Exception as e:
        logger.debug(f"Spotify 全分类搜索跳过: {e}")

    # 2. 默认零配置回退：YouTube Music 全分类搜索
    ytm = get_ytm_client()
    if ytm:
        try:
            tracks: List[Dict[str, Any]] = []
            albums: List[Dict[str, Any]] = []
            artists: List[Dict[str, Any]] = []
            playlists: List[Dict[str, Any]] = []

            # 搜索单曲
            song_res = ytm.search(query, filter="songs", limit=limit)
            for r in song_res:
                vid = r.get("videoId")
                if not vid:
                    continue
                title = r.get("title") or "未知歌曲"
                artists_list = [
                    a.get("name", "")
                    for a in (r.get("artists") or [])
                    if isinstance(a, dict) and a.get("name")
                ]
                artist = ", ".join(artists_list) if artists_list else "未知艺术家"
                album_info = r.get("album")
                album_name = album_info.get("name") if isinstance(album_info, dict) else ""
                thumbnails = r.get("thumbnails") or []
                cover_url = thumbnails[-1].get("url") if thumbnails else ""
                dur_sec = r.get("duration_seconds") or 0
                dur_str = r.get("duration") or ""
                if not dur_str and dur_sec:
                    m, s = divmod(int(dur_sec), 60)
                    dur_str = f"{m:02d}:{s:02d}"

                tracks.append({
                    "id": vid,
                    "title": title,
                    "artist": artist,
                    "album": album_name or "",
                    "album_artist": artist,
                    "duration": dur_sec,
                    "duration_str": dur_str,
                    "cover_url": cover_url,
                    "source": "ytmusic",
                    "url": f"https://music.youtube.com/watch?v={vid}",
                })

            # 搜索专辑
            album_res = ytm.search(query, filter="albums", limit=min(limit, 8))
            for a in album_res:
                browse_id = a.get("browseId") or ""
                alb_title = a.get("title") or ""
                artists_list = [
                    ar.get("name", "")
                    for ar in (a.get("artists") or [])
                    if isinstance(ar, dict) and ar.get("name")
                ]
                alb_artist = ", ".join(artists_list) if artists_list else "未知艺术家"
                thumbnails = a.get("thumbnails") or []
                cover_url = thumbnails[-1].get("url") if thumbnails else ""
                year = a.get("year") or ""
                albums.append({
                    "id": browse_id,
                    "title": alb_title,
                    "artist": alb_artist,
                    "year": str(year),
                    "cover_url": cover_url,
                    "source": "ytmusic",
                    "url": f"https://music.youtube.com/browse/{browse_id}" if browse_id else "",
                })

            # 搜索艺术家
            artist_res = ytm.search(query, filter="artists", limit=min(limit, 6))
            for ar in artist_res:
                browse_id = ar.get("browseId") or ""
                art_name = ar.get("artist") or ar.get("title") or ""
                thumbnails = ar.get("thumbnails") or []
                cover_url = thumbnails[-1].get("url") if thumbnails else ""
                artists.append({
                    "id": browse_id,
                    "name": art_name,
                    "avatar_url": cover_url,
                    "verified": False,
                    "source": "ytmusic",
                    "url": f"https://music.youtube.com/browse/{browse_id}" if browse_id else "",
                })

            if tracks or albums or artists:
                return {
                    "tracks": tracks,
                    "albums": albums,
                    "artists": artists,
                    "playlists": playlists,
                }
        except Exception as e:
            logger.debug(f"YTMusic 全分类检索异常: {e}")

    # 3. Apple iTunes Search API 免 Key 全局零配置搜索回退
    try:
        itunes_results = search_itunes_all(query=query, limit=limit, proxy=proxy)
        if any(itunes_results.get(k) for k in ("tracks", "albums", "artists")):
            return itunes_results
    except Exception as e:
        logger.debug(f"iTunes 全分类搜索回退异常: {e}")

    # 4. 回退 yt-dlp 搜索单曲
    tracks = []
    if _HAS_YTDLP:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "skip_download": True,
            "noplaylist": True,
        }
        if proxy:
            ydl_opts["proxy"] = proxy

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                search_query = f"ytsearch{limit}:{query}"
                info = ydl.extract_info(search_query, download=False)
                entries = (info or {}).get("entries") or []
                for entry in entries:
                    if not entry:
                        continue
                    vid = entry.get("id")
                    if not vid:
                        continue
                    title = entry.get("title", "")
                    uploader = entry.get("uploader") or entry.get("channel") or "未知艺术家"
                    dur = int(entry.get("duration") or 0)
                    m, s = divmod(dur, 60)
                    dur_str = f"{m:02d}:{s:02d}" if dur else ""
                    thumbnails = entry.get("thumbnails") or []
                    cover = thumbnails[-1].get("url") if thumbnails else (entry.get("thumbnail") or "")

                    tracks.append({
                        "id": vid,
                        "title": title,
                        "artist": uploader,
                        "album": "",
                        "album_artist": uploader,
                        "duration": dur,
                        "duration_str": dur_str,
                        "cover_url": cover,
                        "source": "youtube",
                        "url": f"https://www.youtube.com/watch?v={vid}",
                    })
        except Exception as e:
            logger.warning(f"yt-dlp 搜索候选失败: {e}")

    return {
        "tracks": tracks,
        "albums": [],
        "artists": [],
        "playlists": [],
    }


def search_music_candidates(
    query: str,
    limit: int = 15,
    proxy: Optional[str] = None,
    spotify_client_id: Optional[str] = None,
    spotify_client_secret: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """搜索音乐候选曲目列表（向后兼容接口）。"""
    all_res = search_music_all(
        query=query,
        limit=limit,
        proxy=proxy,
        spotify_client_id=spotify_client_id,
        spotify_client_secret=spotify_client_secret,
    )
    return all_res.get("tracks") or []


