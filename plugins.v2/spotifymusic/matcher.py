"""音频来源检索与音轨匹配器。

通过 YouTube Music API 或 yt-dlp 搜索精准匹配 Spotify 曲目的最佳 YouTube 音频。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

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
