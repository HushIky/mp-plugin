"""SQLite 数据库持久化模块。

统一管理 Spotify 订阅列表、已下载/存量基准去重历史，以及后台下载任务状态。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.log import logger


def _now_iso() -> str:
    """获取当前 UTC ISO 时间字符串。"""
    return datetime.now(timezone.utc).isoformat()


class MusicDatabase:
    """管理订阅与下载状态的 SQLite 客户端。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)
        self._lock = threading.Lock()
        self._init_tables()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self) -> None:
        """初始化数据表结构与字段迁移。"""
        with self._lock, self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,                -- playlist / artist / album
                    spotify_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    cover_url TEXT,
                    interval_minutes INTEGER DEFAULT 60,
                    sync_mode TEXT DEFAULT 'all',      -- all: 全量同步; only_new: 仅监控新增
                    enabled INTEGER DEFAULT 1,
                    last_checked TEXT,
                    total_tracks INTEGER DEFAULT 0,
                    downloaded_tracks INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS subscription_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subscription_id INTEGER NOT NULL,
                    track_spotify_id TEXT NOT NULL,
                    track_name TEXT,
                    artist_name TEXT,
                    album_name TEXT,
                    status TEXT DEFAULT 'downloaded',  -- downloaded: 实际下载; existing_base: 存量基准跳过
                    downloaded_at TEXT NOT NULL,
                    file_path TEXT,
                    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id) ON DELETE CASCADE,
                    UNIQUE(subscription_id, track_spotify_id)
                );

                CREATE TABLE IF NOT EXISTS download_tasks (
                    id TEXT PRIMARY KEY,               -- Task UUID
                    title TEXT NOT NULL,
                    artist TEXT,
                    album TEXT,
                    cover_url TEXT,
                    spotify_id TEXT,
                    subscription_id INTEGER,
                    status TEXT NOT NULL,              -- pending, matching, downloading, processing, completed, failed
                    progress REAL DEFAULT 0.0,
                    speed TEXT,
                    error_msg TEXT,
                    file_path TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)

    # ==================== 订阅管理 (Subscriptions) ====================

    def add_subscription(
        self,
        sub_type: str,
        spotify_id: str,
        name: str,
        url: str,
        cover_url: str = '',
        interval_minutes: int = 60,
        sync_mode: str = 'all',
    ) -> Dict[str, Any]:
        """添加或更新订阅。"""
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO subscriptions
                (type, spotify_id, name, url, cover_url, interval_minutes, sync_mode, enabled, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(spotify_id) DO UPDATE SET
                    name=excluded.name,
                    cover_url=excluded.cover_url,
                    interval_minutes=excluded.interval_minutes,
                    sync_mode=excluded.sync_mode
                """,
                (sub_type, spotify_id, name, url, cover_url, interval_minutes, sync_mode, _now_iso()),
            )
            sub_id = cur.lastrowid
            row = conn.execute("SELECT * FROM subscriptions WHERE spotify_id = ?", (spotify_id,)).fetchone()
            return dict(row) if row else {"id": sub_id}

    def list_subscriptions(self) -> List[Dict[str, Any]]:
        """获取所有订阅列表（附带动态统计已下载与已跳过存量计数）。"""
        with self._lock, self._connect() as conn:
            rows = conn.execute("""
                SELECT s.id, s.type, s.spotify_id, s.name, s.url, s.cover_url, s.interval_minutes, s.sync_mode, s.enabled, s.last_checked, s.created_at,
                       MAX(s.total_tracks, COALESCE((SELECT COUNT(*) FROM subscription_history WHERE subscription_id = s.id), 0)) AS total_tracks,
                       MAX(s.downloaded_tracks, COALESCE((SELECT COUNT(*) FROM subscription_history WHERE subscription_id = s.id AND status = 'downloaded'), 0)) AS downloaded_tracks,
                       COALESCE((SELECT COUNT(*) FROM subscription_history WHERE subscription_id = s.id AND status = 'existing_base'), 0) AS skipped_tracks
                FROM subscriptions s
                ORDER BY s.id DESC
            """).fetchall()
            return [dict(r) for r in rows]

    def get_subscription(self, sub_id: int) -> Optional[Dict[str, Any]]:
        """按 ID 获取订阅详情（附带动态统计已下载与已跳过存量计数）。"""
        with self._lock, self._connect() as conn:
            row = conn.execute("""
                SELECT s.id, s.type, s.spotify_id, s.name, s.url, s.cover_url, s.interval_minutes, s.sync_mode, s.enabled, s.last_checked, s.created_at,
                       MAX(s.total_tracks, COALESCE((SELECT COUNT(*) FROM subscription_history WHERE subscription_id = s.id), 0)) AS total_tracks,
                       MAX(s.downloaded_tracks, COALESCE((SELECT COUNT(*) FROM subscription_history WHERE subscription_id = s.id AND status = 'downloaded'), 0)) AS downloaded_tracks,
                       COALESCE((SELECT COUNT(*) FROM subscription_history WHERE subscription_id = s.id AND status = 'existing_base'), 0) AS skipped_tracks
                FROM subscriptions s
                WHERE s.id = ?
            """, (sub_id,)).fetchone()
            return dict(row) if row else None

    def delete_subscription(self, sub_id: int) -> bool:
        """删除指定订阅。"""
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))
            return True

    def toggle_subscription(self, sub_id: int, enabled: bool) -> bool:
        """切换订阅启用/禁用状态。"""
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE subscriptions SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, sub_id),
            )
            return True

    def update_subscription_stats(
        self,
        sub_id: int,
        total_tracks: Optional[int] = None,
        downloaded_increment: int = 0,
        last_checked: Optional[str] = None,
    ) -> None:
        """更新订阅的统计与检查时间。若 total_tracks 为 None 或 <= 0 则保持原曲目总数不覆盖。"""
        check_time = last_checked or _now_iso()
        with self._lock, self._connect() as conn:
            if total_tracks is not None and total_tracks > 0:
                conn.execute(
                    """
                    UPDATE subscriptions
                    SET total_tracks = MAX(?, total_tracks),
                        downloaded_tracks = downloaded_tracks + ?,
                        last_checked = ?
                    WHERE id = ?
                    """,
                    (total_tracks, downloaded_increment, check_time, sub_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE subscriptions
                    SET downloaded_tracks = downloaded_tracks + ?,
                        last_checked = ?
                    WHERE id = ?
                    """,
                    (downloaded_increment, check_time, sub_id),
                )

    # ==================== 订阅历史 (History & Deduplication) ====================

    def is_track_in_history(self, sub_id: int, track_spotify_id: str) -> bool:
        """判断曲目是否已存在于该订阅的历史记录中。"""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM subscription_history WHERE subscription_id = ? AND track_spotify_id = ?",
                (sub_id, track_spotify_id),
            ).fetchone()
            return bool(row)

    def record_track_history(
        self,
        sub_id: int,
        track_spotify_id: str,
        track_name: str,
        artist_name: str,
        album_name: str,
        status: str = 'downloaded',
        file_path: Optional[str] = None,
    ) -> None:
        """记录曲目到订阅历史（用于去重）。若订阅已被删除则忽略外键冲突。"""
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO subscription_history
                    (subscription_id, track_spotify_id, track_name, artist_name, album_name, status, downloaded_at, file_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (sub_id, track_spotify_id, track_name, artist_name, album_name, status, _now_iso(), file_path),
                )
        except sqlite3.Error as e:
            logger.debug(f"记录订阅历史忽略外键或数据库约束冲突 (sub_id={sub_id}): {e}")

    def batch_record_existing_base(
        self,
        sub_id: int,
        tracks: List[Dict[str, Any]],
    ) -> int:
        """批量将当前存量曲目记录为基准存量（仅监控新增模式）。"""
        now = _now_iso()
        count = 0
        with self._lock, self._connect() as conn:
            for t in tracks:
                t_id = t.get("spotify_id")
                if not t_id:
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO subscription_history
                    (subscription_id, track_spotify_id, track_name, artist_name, album_name, status, downloaded_at)
                    VALUES (?, ?, ?, ?, ?, 'existing_base', ?)
                    """,
                    (
                        sub_id,
                        t_id,
                        t.get("title") or t.get("name", ""),
                        t.get("artist") or ", ".join(t.get("artists") or []),
                        t.get("album", ""),
                        now,
                    ),
                )
                count += 1
        return count

    # ==================== 任务队列 (Download Tasks) ====================

    def create_task(
        self,
        task_id: str,
        title: str,
        artist: str = '',
        album: str = '',
        cover_url: str = '',
        spotify_id: Optional[str] = None,
        subscription_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """创建下载任务。"""
        now = _now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO download_tasks
                (id, title, artist, album, cover_url, spotify_id, subscription_id, status, progress, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0.0, ?, ?)
                """,
                (task_id, title, artist, album, cover_url, spotify_id, subscription_id, now, now),
            )
        return self.get_task(task_id) or {}

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """按 ID 查询任务详情。"""
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM download_tasks WHERE id = ?", (task_id,)).fetchone()
            return dict(row) if row else None

    def update_task_progress(
        self,
        task_id: str,
        status: Optional[str] = None,
        progress: Optional[float] = None,
        speed: Optional[str] = None,
        error_msg: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> None:
        """更新任务进度与状态。"""
        now = _now_iso()
        updates = ["updated_at = ?"]
        params: List[Any] = [now]

        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if progress is not None:
            updates.append("progress = ?")
            params.append(float(progress))
        if speed is not None:
            updates.append("speed = ?")
            params.append(speed)
        if error_msg is not None:
            updates.append("error_msg = ?")
            params.append(error_msg)
        if file_path is not None:
            updates.append("file_path = ?")
            params.append(file_path)

        params.append(task_id)
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE download_tasks SET {', '.join(updates)} WHERE id = ?", params)

    def list_tasks(
        self,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """获取任务列表。"""
        with self._lock, self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM download_tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM download_tasks ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def delete_task(self, task_id: str) -> bool:
        """删除任务。"""
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM download_tasks WHERE id = ?", (task_id,))
            return True

    def clear_completed_tasks(self) -> int:
        """清理所有已完成的任务。"""
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM download_tasks WHERE status = 'completed'")
            return cur.rowcount

    def clear_failed_tasks(self) -> int:
        """清理所有失败的任务。"""
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM download_tasks WHERE status = 'failed'")
            return cur.rowcount

    def retry_failed_tasks(self) -> List[str]:
        """重置所有失败任务为 pending。"""
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT id FROM download_tasks WHERE status = 'failed'").fetchall()
            failed_ids = [r["id"] for r in rows]
            if failed_ids:
                conn.execute(
                    """
                    UPDATE download_tasks
                    SET status = 'pending', progress = 0.0, error_msg = NULL, updated_at = ?
                    WHERE status = 'failed'
                    """,
                    (_now_iso(),),
                )
            return failed_ids
