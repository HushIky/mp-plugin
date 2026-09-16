"""音乐文件整理与目标目录转移模块。

支持根据自定义目录结构模板（如 {artist}/{album}/{track:02d} - {title}.{ext}）将加工完毕的音频和歌词安全移动到最终媒体库。
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.log import logger

_INVALID_FS_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def sanitize_path_part(text: str) -> str:
    """清理路径段文本，移除不可作为文件名的特殊符号。"""
    safe = _INVALID_FS_CHARS.sub('', text or '').strip().strip('.')
    return safe or 'Unknown'


def build_relative_path(template: str, track_info: Dict[str, Any], ext: str) -> Path:
    """
    根据用户配置的模板构建相对路径。

    :param template: 路径模板，例如 "{artist}/{album} ({year})/{track_number:02d} - {title}.{ext}"
    :param track_info: 曲目元数据
    :param ext: 文件扩展名 (如 "mp3")
    :return: 相对 Path 对象
    """
    title = sanitize_path_part(str(track_info.get("title") or track_info.get("name") or "Unknown Title"))
    artists_list = track_info.get("artists") or []
    artist = sanitize_path_part(str(track_info.get("artist") or ", ".join(artists_list) or "Unknown Artist"))
    album = sanitize_path_part(str(track_info.get("album") or "Unknown Album"))
    release_date = str(track_info.get("release_date") or "")
    year = release_date[:4] if len(release_date) >= 4 else "Unknown Year"
    track_num = int(track_info.get("track_number") or 1)
    disc_num = int(track_info.get("disc_number") or 1)
    ext_clean = ext.lstrip('.')

    # 规范化模板占位符
    tpl = template or "{artist}/{album}/{track_number:02d} - {title}.{ext}"
    tpl = tpl.replace("{track:02d}", f"{track_num:02d}")
    tpl = tpl.replace("{track}", str(track_num))
    tpl = tpl.replace("{track_number:02d}", f"{track_num:02d}")
    tpl = tpl.replace("{track_number}", str(track_num))
    tpl = tpl.replace("{disc:02d}", f"{disc_num:02d}")
    tpl = tpl.replace("{disc}", str(disc_num))
    tpl = tpl.replace("{disc_number}", str(disc_num))
    tpl = tpl.replace("{ext}", ext_clean)
    tpl = tpl.replace("{output-ext}", ext_clean)

    # 渲染其余字符串变量
    try:
        rendered = tpl.format(
            artist=artist,
            artists=artist,
            title=title,
            album=album,
            year=year,
        )
    except Exception as e:
        logger.warning(f"目录模板渲染异常 ({tpl}): {e}，回退到默认结构")
        rendered = f"{artist}/{album}/{track_num:02d} - {title}.{ext_clean}"

    # 清理每一个路径段
    parts = [sanitize_path_part(p) for p in rendered.split('/') if p.strip()]
    return Path(*parts)


def transfer_to_destination(
    audio_temp_path: Path,
    lrc_temp_path: Optional[Path],
    destination_root: Path | str,
    template: str,
    track_info: Dict[str, Any],
    playlist_name: Optional[str] = None,
) -> Tuple[Path, Optional[Path]]:
    """
    将临时目录中的音频与歌词文件安全移动到目标目录。

    :param audio_temp_path: 临时音频文件
    :param lrc_temp_path: 临时歌词文件
    :param destination_root: 目标音乐库根目录
    :param template: 路径模板
    :param track_info: 曲目元数据
    :param playlist_name: 歌单名（可选）
    :return: (最终音频路径, 最终歌词路径)
    """
    dest_base = Path(destination_root)
    dest_base.mkdir(parents=True, exist_ok=True)

    ext = audio_temp_path.suffix.lower().lstrip('.')
    rel_path = build_relative_path(template, track_info, ext)
    final_audio_path = dest_base / rel_path
    final_audio_path.parent.mkdir(parents=True, exist_ok=True)

    # 移动音频文件
    shutil.move(str(audio_temp_path), str(final_audio_path))
    logger.info(f"音频文件整理成功: {final_audio_path}")

    # 移动歌词文件
    final_lrc_path = None
    if lrc_temp_path and lrc_temp_path.exists():
        final_lrc_path = final_audio_path.with_suffix('.lrc')
        shutil.move(str(lrc_temp_path), str(final_lrc_path))
        logger.debug(f"歌词文件整理成功: {final_lrc_path}")

    # 歌单 M3U 文件自动追加
    if playlist_name:
        try:
            _append_to_m3u(dest_base, playlist_name, final_audio_path, track_info)
        except Exception as e:
            logger.debug(f"更新 M3U 歌单失败: {e}")

    return final_audio_path, final_lrc_path


def _append_to_m3u(
    dest_base: Path,
    playlist_name: str,
    audio_path: Path,
    track_info: Dict[str, Any],
) -> None:
    """追加单曲到歌单 M3U 文件中。"""
    safe_pl_name = sanitize_path_part(playlist_name)
    m3u_file = dest_base / f"{safe_pl_name}.m3u8"
    duration = int(track_info.get("duration") or 0)
    title = track_info.get("title") or track_info.get("name") or "Unknown"
    artist = track_info.get("artist") or "Unknown"

    # 计算相对于 M3U 文件的相对路径
    try:
        entry_path = audio_path.relative_to(dest_base).as_posix()
    except Exception:
        entry_path = audio_path.as_posix()

    lines = []
    if not m3u_file.exists():
        lines.append("#EXTM3U\n")
    
    # 检查是否已存在
    content = m3u_file.read_text(encoding='utf-8') if m3u_file.exists() else ""
    if entry_path not in content:
        lines.append(f"#EXTINF:{duration},{artist} - {title}\n")
        lines.append(f"{entry_path}\n")
        with m3u_file.open('a', encoding='utf-8') as f:
            f.writelines(lines)
