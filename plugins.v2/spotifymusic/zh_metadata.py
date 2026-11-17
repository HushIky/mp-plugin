"""第三方音乐中文元数据交叉匹配模块。

通过网易云音乐公开 Web API（免登录/免加密）对 Spotify 获取到的英文专辑与曲目进行交叉匹配，
在不影响 YouTube Music 音源检索与 LRCLIB 歌词匹配准确度的前提下，
保留网易云原始标题（如中英双语或纯中文元标题）用于音频标签打标与文件归档。
"""

from __future__ import annotations

import re
import threading
from typing import Any, Dict, List, Optional, Tuple

import requests

from app.log import logger

_NETEASE_SEARCH_URL = "https://music.163.com/api/search/get/web"
_NETEASE_ALBUM_URL = "https://music.163.com/api/v1/album/{album_id}"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://music.163.com/",
}

# 专辑级交叉匹配缓存：key=(album_lower, primary_artist_lower) -> dict 或 None（表示已查过未命中）
_ALBUM_CACHE: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()

_PART_RE = re.compile(r"\b(?:part|pt\.?|vol\.?|volume)\s*(\d+)\b", re.IGNORECASE)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_PAREN_SUFFIX_RE = re.compile(
    r"\s*[\[\(](?:Original\s+(?:Game|Motion\s+Picture|Video\s+Game)?\s*Soundtrack|"
    r"OST|Deluxe(?:\s+Edition)?|Remastered(?:\s+\d+)?|Explicit)[\]\)]\s*",
    re.IGNORECASE,
)


def _contains_cjk(text: str) -> bool:
    """判断文本中是否已包含中日韩统一表意文字。"""
    return bool(_CJK_RE.search(text or ""))


def _normalize_text(text: str) -> str:
    """规范化文本以便模糊比对（转小写、去除多余标点与空白）。"""
    cleaned = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", (text or "").lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _clean_album_title(album: str) -> str:
    """清理专辑标题中的原声带/版本后缀以便搜索匹配。"""
    text = _PAREN_SUFFIX_RE.sub(" ", album or "")
    return re.sub(r"\s+", " ", text).strip()


def _extract_part_number(text: str) -> Optional[str]:
    """提取标题中的 Part/Vol 分卷编号，防止 Part 3 误匹配到 Part 1/2。"""
    m = _PART_RE.search(text or "")
    return m.group(1) if m else None


def _extract_primary_artist(artist_str: str) -> str:
    """提取首位主艺术家名称。"""
    if not artist_str:
        return ""
    parts = re.split(r"\s*(?:,|/|、|;|feat\.?|ft\.?)\s*", artist_str.strip(), maxsplit=1, flags=re.IGNORECASE)
    return parts[0].strip() if parts else artist_str.strip()


def _artist_matches(primary_artist: str, candidate_artists: List[str]) -> bool:
    """校验候选艺术家列表是否与主艺术家匹配。"""
    target = _normalize_text(primary_artist)
    if not target:
        return False
    for cand in candidate_artists:
        cand_norm = _normalize_text(cand)
        if not cand_norm:
            continue
        if target == cand_norm or target in cand_norm or cand_norm in target:
            return True
    return False


def clear_album_cache() -> None:
    """清空专辑缓存（主要用于测试或手动刷新）。"""
    with _CACHE_LOCK:
        _ALBUM_CACHE.clear()


def _fetch_netease_album_detail(album_id: int, proxies: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    """拉取网易云专辑详情并提取标准化的专辑名与曲目列表。"""
    try:
        resp = requests.get(
            _NETEASE_ALBUM_URL.format(album_id=album_id),
            headers=_HEADERS,
            proxies=proxies,
            timeout=8,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        album_obj = data.get("album") or {}
        album_name = str(album_obj.get("name") or "").strip()
        songs = data.get("songs") or []
        if not album_name or not songs:
            return None

        parsed_tracks: List[Dict[str, Any]] = []
        for idx, s in enumerate(songs, start=1):
            if not isinstance(s, dict):
                continue
            s_name = str(s.get("name") or "").strip()
            if not s_name:
                continue
            s_no = int(s.get("no") or idx)
            s_cd = str(s.get("cd") or "1").strip()
            s_dt = int(s.get("dt") or s.get("duration") or 0)
            s_artists = [
                str(a.get("name") or "").strip()
                for a in (s.get("ar") or s.get("artists") or [])
                if isinstance(a, dict) and a.get("name")
            ]
            parsed_tracks.append({
                "no": s_no,
                "cd": s_cd,
                "name": s_name,
                "duration_sec": s_dt // 1000 if s_dt else 0,
                "artists": s_artists,
            })

        return {
            "id": album_id,
            "name": album_name,
            "tracks": parsed_tracks,
        }
    except Exception as e:
        logger.debug(f"拉取网易云专辑详情失败 (id={album_id}): {e}")
        return None


def match_netease_album(
    album: str,
    artist: str,
    total_tracks: int = 0,
    proxy: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    在网易云音乐中检索并校验与 Spotify 专辑匹配的中文/双语专辑详情（带内存缓存）。
    """
    clean_alb = _clean_album_title(album)
    primary_artist = _extract_primary_artist(artist)
    if not clean_alb or not primary_artist:
        return None

    cache_key = (clean_alb.lower(), primary_artist.lower())
    with _CACHE_LOCK:
        if cache_key in _ALBUM_CACHE:
            return _ALBUM_CACHE[cache_key]

    proxies = {"http": proxy, "https": proxy} if proxy else None
    src_part = _extract_part_number(clean_alb)
    clean_norm = _normalize_text(clean_alb)
    # 若带系列前缀（如 "Honkai: Star Rail - Let There Be Laughter (Part 1)"），同时提取后半核心段用于子串匹配
    core_segment = clean_alb.split(" - ", 1)[-1].strip() if " - " in clean_alb else clean_alb
    core_norm = _normalize_text(core_segment)

    query_candidates = [f"{core_segment} {primary_artist}".strip()]
    if core_segment != clean_alb:
        query_candidates.append(f"{clean_alb} {primary_artist}".strip())

    matched_album_id: Optional[int] = None
    best_score = -1

    for q in query_candidates:
        try:
            resp = requests.post(
                _NETEASE_SEARCH_URL,
                data={"s": q, "type": 10, "offset": 0, "limit": 10},
                headers=_HEADERS,
                proxies=proxies,
                timeout=8,
            )
            if resp.status_code != 200:
                continue
            albums = resp.json().get("result", {}).get("albums") or []
            for cand in albums:
                if not isinstance(cand, dict):
                    continue
                cand_name = str(cand.get("name") or "").strip()
                cand_id = cand.get("id")
                if not cand_name or not cand_id:
                    continue

                cand_artists = [
                    str(a.get("name") or "").strip()
                    for a in (cand.get("artists") or [])
                    if isinstance(a, dict) and a.get("name")
                ]
                if not _artist_matches(primary_artist, cand_artists):
                    continue

                # 严格校验分卷编号（如 Part 1 / Part 2 / Part 3）
                cand_part = _extract_part_number(cand_name)
                if src_part and cand_part and src_part != cand_part:
                    continue
                if src_part and not cand_part:
                    continue

                cand_norm = _normalize_text(cand_name)
                title_hit = (
                    (core_norm and core_norm in cand_norm)
                    or (clean_norm and clean_norm in cand_norm)
                )
                if not title_hit:
                    continue

                score = 100
                if _contains_cjk(cand_name):
                    score += 20
                cand_size = int(cand.get("size") or 0)
                if total_tracks and cand_size:
                    if cand_size == total_tracks:
                        score += 30
                    elif abs(cand_size - total_tracks) > max(3, int(total_tracks * 0.2)):
                        continue

                if score > best_score:
                    best_score = score
                    matched_album_id = int(cand_id)

            if matched_album_id:
                break
        except Exception as e:
            logger.debug(f"检索网易云专辑异常 ({q}): {e}")

    result: Optional[Dict[str, Any]] = None
    if matched_album_id:
        result = _fetch_netease_album_detail(matched_album_id, proxies=proxies)

    with _CACHE_LOCK:
        _ALBUM_CACHE[cache_key] = result
    return result


def _match_track_in_album(
    album_detail: Dict[str, Any],
    title: str,
    track_number: int = 0,
    duration: int = 0,
) -> Optional[str]:
    """在已匹配的网易云专辑曲目列表中精准对齐单曲，返回网易云元标题。"""
    tracks = album_detail.get("tracks") or []
    if not tracks:
        return None

    title_norm = _normalize_text(title)
    best_name: Optional[str] = None
    best_score = 0

    for item in tracks:
        ne_name = str(item.get("name") or "").strip()
        if not ne_name:
            continue
        ne_norm = _normalize_text(ne_name)
        ne_no = int(item.get("no") or 0)
        ne_dur = int(item.get("duration_sec") or 0)

        english_hit = bool(title_norm and title_norm in ne_norm)
        no_hit = bool(track_number and ne_no == track_number)
        dur_diff = abs(ne_dur - duration) if (duration and ne_dur) else None

        # 安全校验：
        # 1) 英文原名明确包含在网易云双语曲名中，且时长无严重冲突
        # 2) 纯中文曲名则要求曲号严格一致且时长误差 <= 3s
        if english_hit:
            if dur_diff is not None and dur_diff > 6:
                continue
            score = 100 + (20 if no_hit else 0) + (10 if _contains_cjk(ne_name) else 0)
        elif no_hit and dur_diff is not None and dur_diff <= 3 and _contains_cjk(ne_name):
            score = 85
        else:
            continue

        if score > best_score:
            best_score = score
            best_name = ne_name

    return best_name


def match_netease_single(
    title: str,
    artist: str,
    duration: int = 0,
    proxy: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    """
    针对无专辑信息或专辑未命中的散装单曲，通过网易云单曲搜索做严格防翻唱交叉匹配。
    """
    primary_artist = _extract_primary_artist(artist)
    if not title or not primary_artist:
        return None

    proxies = {"http": proxy, "https": proxy} if proxy else None
    title_norm = _normalize_text(title)
    src_has_cover = any(kw in title_norm for kw in ("cover", "remix", "live"))

    try:
        resp = requests.post(
            _NETEASE_SEARCH_URL,
            data={"s": f"{title} {primary_artist}".strip(), "type": 1, "offset": 0, "limit": 10},
            headers=_HEADERS,
            proxies=proxies,
            timeout=8,
        )
        if resp.status_code != 200:
            return None

        songs = resp.json().get("result", {}).get("songs") or []
        for s in songs:
            if not isinstance(s, dict):
                continue
            s_name = str(s.get("name") or "").strip()
            if not s_name:
                continue

            s_artists = [
                str(a.get("name") or "").strip()
                for a in (s.get("artists") or [])
                if isinstance(a, dict) and a.get("name")
            ]
            if not _artist_matches(primary_artist, s_artists):
                continue

            s_norm = _normalize_text(s_name)
            if not src_has_cover and any(kw in s_name.lower() for kw in ("cover", "翻唱", "remix")):
                continue

            if not (title_norm and title_norm in s_norm):
                continue

            s_dur_sec = int(s.get("duration") or 0) // 1000
            if duration and s_dur_sec and abs(s_dur_sec - duration) > 3:
                continue

            if not _contains_cjk(s_name):
                continue

            alb_name = str((s.get("album") or {}).get("name") or "").strip()
            return {
                "title": s_name,
                "album": alb_name,
            }
    except Exception as e:
        logger.debug(f"检索网易云单曲异常 ({title}): {e}")

    return None


def enrich_track_zh_metadata(
    track_info: Dict[str, Any],
    proxy: Optional[str] = None,
) -> Dict[str, Any]:
    """
    使用网易云音乐交叉匹配补全中文/双语元标题（保留网易云元标题）。

    保留原始 `search_title` / `search_album` 用于 YouTube 音源及 LRCLIB 歌词匹配，
    将命中后的网易云专辑名与曲目名写入 `title` / `album` 用于元数据标签和目录归档。
    """
    orig_title = str(track_info.get("title") or track_info.get("name") or "").strip()
    orig_album = str(track_info.get("album") or "").strip()
    if orig_title and not track_info.get("search_title"):
        track_info["search_title"] = orig_title
    if orig_album and not track_info.get("search_album"):
        track_info["search_album"] = orig_album

    # 曲名与专辑均已含中文时无需重复匹配
    if _contains_cjk(orig_title) and (not orig_album or _contains_cjk(orig_album)):
        return track_info

    artist = str(track_info.get("artist") or "").strip()
    if not artist and isinstance(track_info.get("artists"), list):
        artist = ", ".join(str(a).strip() for a in track_info["artists"] if a)
    if not orig_title or not artist:
        return track_info

    duration = int(track_info.get("duration") or 0)
    track_number = int(track_info.get("track_number") or 0)
    total_tracks = int(track_info.get("total_tracks") or 0)

    # 1. 优先尝试专辑级对齐（同专辑共享缓存，零额外请求）
    if orig_album:
        alb_detail = match_netease_album(
            album=orig_album,
            artist=artist,
            total_tracks=total_tracks,
            proxy=proxy,
        )
        if alb_detail:
            matched_track_name = _match_track_in_album(
                album_detail=alb_detail,
                title=orig_title,
                track_number=track_number,
                duration=duration,
            )
            if _contains_cjk(alb_detail.get("name", "")):
                track_info["album"] = alb_detail["name"]
            if matched_track_name:
                track_info["title"] = matched_track_name
            if track_info.get("title") != orig_title or track_info.get("album") != orig_album:
                logger.info(
                    f"[中文元数据匹配] 专辑对齐成功: '{orig_title}' -> '{track_info.get('title')}' "
                    f"| 专辑: '{track_info.get('album')}'"
                )
            return track_info

    # 2. 兜底尝试单曲级严格交叉匹配
    single_res = match_netease_single(
        title=orig_title,
        artist=artist,
        duration=duration,
        proxy=proxy,
    )
    if single_res:
        track_info["title"] = single_res["title"]
        if single_res.get("album") and _contains_cjk(single_res["album"]):
            track_info["album"] = single_res["album"]
        logger.info(
            f"[中文元数据匹配] 单曲对齐成功: '{orig_title}' -> '{track_info.get('title')}'"
        )

    return track_info
