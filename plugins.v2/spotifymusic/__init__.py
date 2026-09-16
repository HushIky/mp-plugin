"""Spotify 音乐下载与增量订阅管理插件。

支持 Spotify 链接解析、音乐搜索、歌单/艺术家增量订阅、元数据标签/封面/歌词内嵌以及目录自动整理。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi.responses import HTMLResponse

try:
    from app.core.config import settings
except ImportError:
    try:
        from app.runtime.config import settings
    except ImportError:
        settings = None

from app.core.event import eventmanager
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType, NotificationType

from . import matcher, spotify
from .db import MusicDatabase
from .queue import DownloadQueueManager
from .web_ui import render_music_workbench_html


class SpotifyMusic(_PluginBase):
    """Spotify 音乐下载与订阅插件主类。"""

    # 插件元信息
    plugin_name = "Spotify音乐下载与订阅"
    plugin_desc = "支持 Spotify 链接解析、音乐搜索、歌单/艺术家增量订阅、元数据标签/封面/歌词内嵌与目录自动整理。"
    plugin_icon = "spotifymusic.png"
    plugin_version = "1.1.10"
    plugin_label = "音乐管理"
    plugin_author = "local"
    plugin_order = 10
    auth_level = 1

    def _get_api_token(self) -> str:
        """获取 MoviePilot 系统的 API Token。"""
        if settings and hasattr(settings, "API_TOKEN") and settings.API_TOKEN:
            return str(settings.API_TOKEN).strip()
        return ""

    # 内部状态
    _enabled: bool = False
    _music_dir: str = ""
    _audio_format: str = "mp3"
    _audio_bitrate: str = "320"
    _template: str = "{artist}/{album} ({year})/{track_number:02d} - {title}.{ext}"
    _download_lyrics: bool = True
    _notify_success: bool = True
    _interval_minutes: int = 60
    _max_parallel: int = 3
    _proxy: str = ""
    _spotify_client_id: str = ""
    _spotify_client_secret: str = ""

    _db: Optional[MusicDatabase] = None
    _queue_mgr: Optional[DownloadQueueManager] = None

    def init_plugin(self, config: dict = None) -> None:
        """根据用户配置初始化插件状态、持久化数据库与下载队列。"""
        self.stop_service()
        self._enabled = False

        if not config:
            return

        self._enabled = bool(config.get("enabled", False))
        self._music_dir = str(config.get("music_dir") or "/media/music").strip()
        self._audio_format = str(config.get("format") or "mp3").lower().strip()
        self._audio_bitrate = str(config.get("bitrate") or "320").strip()
        self._template = str(
            config.get("template") or "{artist}/{album} ({year})/{track_number:02d} - {title}.{ext}"
        ).strip()
        self._download_lyrics = bool(config.get("download_lyrics", True))
        self._notify_success = bool(config.get("notify_download_success", True))
        self._interval_minutes = int(config.get("interval_minutes") or 60)
        self._max_parallel = int(config.get("max_parallel") or 3)
        self._proxy = str(config.get("proxy") or "").strip()
        self._spotify_client_id = str(config.get("spotify_client_id") or "").strip()
        self._spotify_client_secret = str(config.get("spotify_client_secret") or "").strip()

        # 初始化专属数据库
        data_path = self.get_data_path()
        self._db = MusicDatabase(data_path / "music_manager.db")
        temp_dir = data_path / "temp"

        # 初始化下载队列管理器
        self._queue_mgr = DownloadQueueManager(
            db=self._db,
            temp_dir=temp_dir,
            target_dir=Path(self._music_dir),
            template=self._template,
            audio_format=self._audio_format,
            audio_bitrate=self._audio_bitrate,
            max_parallel=self._max_parallel,
            download_lyrics=self._download_lyrics,
            proxy=self._proxy or None,
            on_task_completed=self._on_download_completed,
        )

        if self._enabled:
            self._queue_mgr.start(num_workers=2)

        # 检查是否在保存设置时提交了手动添加链接
        add_url = str(config.get("add_spotify_url") or "").strip()
        add_sync_mode = str(config.get("add_sync_mode") or "only_new").strip()
        if add_url and self._enabled:
            threading.Thread(
                target=self._handle_manual_add_url,
                args=(add_url, add_sync_mode),
                daemon=True,
                name="SpotifyMusicManualAdd",
            ).start()

        # 检查是否在保存设置时提交了歌曲搜索下载
        search_kw = str(config.get("search_keyword") or "").strip()
        if search_kw and self._enabled:
            threading.Thread(
                target=self._handle_manual_search_download,
                args=(search_kw,),
                daemon=True,
                name="SpotifyMusicManualSearch",
            ).start()

        logger.info(
            f"[{self.plugin_name}] 初始化完成：启用={self._enabled}，"
            f"目标目录={self._music_dir}，格式={self._audio_format}，巡检间隔={self._interval_minutes}分"
        )

    def get_state(self) -> bool:
        """获取插件启用状态。"""
        return self._enabled

    def get_service(self) -> List[Dict[str, Any]]:
        """注册定时巡检服务，定期检查订阅的艺术家与歌单是否有新曲目。"""
        if not self._enabled:
            return []
        return [
            {
                "id": "SpotifyMusicSubscriptionChecker",
                "name": "Spotify 订阅增量巡检",
                "trigger": "interval",
                "func": self.check_all_subscriptions,
                "kwargs": {"minutes": max(15, self._interval_minutes)},
            }
        ]

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
        """拼装插件配置页面表单 (Vuetify JSON 结构)。"""
        api_token = self._get_api_token()
        workbench_url = (
            f"/api/v1/plugin/SpotifyMusic/ui?token={api_token}"
            if api_token
            else "/api/v1/plugin/SpotifyMusic/ui"
        )

        form_schema = [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用 Spotify 音乐插件",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VBtn",
                                        "props": {
                                            "color": "primary",
                                            "href": workbench_url,
                                            "target": "_blank",
                                            "text": "🚀 打开全功能音乐工作台 (实时搜索 / 链接订阅 / 队列监控)",
                                            "prependIcon": "mdi-open-in-new",
                                            "block": True,
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VDivider",
                        "props": {"class": "my-4"},
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "music_dir",
                                            "label": "音乐存储根目录",
                                            "placeholder": "/media/music",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "format",
                                            "label": "音频输出格式",
                                            "items": [
                                                {"title": "MP3", "value": "mp3"},
                                                {"title": "FLAC (无损)", "value": "flac"},
                                                {"title": "M4A / AAC", "value": "m4a"},
                                                {"title": "OPUS", "value": "opus"},
                                            ],
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "bitrate",
                                            "label": "MP3 音频比特率 (KB/s)",
                                            "items": [
                                                {"title": "320K (高品质)", "value": "320"},
                                                {"title": "256K", "value": "256"},
                                                {"title": "192K", "value": "192"},
                                                {"title": "128K (节省空间)", "value": "128"},
                                            ],
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "template",
                                            "label": "目录与文件命名模板",
                                            "placeholder": "{artist}/{album} ({year})/{track_number:02d} - {title}.{ext}",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "download_lyrics",
                                            "label": "抓取并内嵌歌词 (含 .lrc 文件)",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "notify_download_success",
                                            "label": "下载完成发送系统通知",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "interval_minutes",
                                            "label": "订阅自动巡检间隔 (分钟)",
                                            "placeholder": "60",
                                            "type": "number",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "max_parallel",
                                            "label": "最大并行下载任务数",
                                            "placeholder": "3",
                                            "type": "number",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "spotify_client_id",
                                            "label": "Spotify Client ID (可选，用于官方搜索 API)",
                                            "placeholder": "在 developer.spotify.com 免费申请",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "spotify_client_secret",
                                            "label": "Spotify Client Secret (可选)",
                                            "placeholder": "Spotify 开发者密钥",
                                            "type": "password",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "proxy",
                                            "label": "HTTP/HTTPS 代理 (可选)",
                                            "placeholder": "例如: http://127.0.0.1:7890",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                ],
            }
        ]
        default_config = {
            "enabled": False,
            "music_dir": "/media/music",
            "format": "mp3",
            "bitrate": "320",
            "template": "{artist}/{album} ({year})/{track_number:02d} - {title}.{ext}",
            "download_lyrics": True,
            "notify_download_success": True,
            "interval_minutes": 60,
            "max_parallel": 3,
            "proxy": "",
            "spotify_client_id": "",
            "spotify_client_secret": "",
        }
        return form_schema, default_config

    def get_page(self) -> Optional[List[dict]]:
        """拼装插件详情概览页面。"""
        if not self._enabled or not self._db:
            return [
                {
                    "component": "VAlert",
                    "props": {
                        "type": "warning",
                        "text": "插件尚未启用，请在配置面板中开启并设置音乐目标目录。",
                    },
                }
            ]

        subs = self._db.list_subscriptions()
        tasks = self._db.list_tasks(limit=15)
        completed_tasks = [t for t in tasks if t.get("status") == "completed"]
        active_tasks = [
            t
            for t in tasks
            if t.get("status") in ("downloading", "processing", "pending")
        ]
        failed_tasks = [t for t in tasks if t.get("status") == "failed"]

        sub_list_text = []
        for s in subs:
            mode_text = "🌿 仅增量" if s.get("sync_mode") == "only_new" else "📦 全量"
            last_chk = (s.get("last_checked") or "从未")[:16].replace("T", " ")
            sub_list_text.append(
                f"• 【{s.get('type', '').upper()}】{s.get('name')} | 模式: {mode_text} | "
                f"已下载: {s.get('downloaded_tracks', 0)} 首 | 上次检查: {last_chk}"
            )
        subs_summary = (
            "\n".join(sub_list_text)
            if sub_list_text
            else "暂无活跃订阅。可点击下方工作台直接添加 Spotify 链接。"
        )

        recent_task_text = []
        for t in tasks[:8]:
            st = t.get("status")
            pct = t.get("progress", 0.0)
            status_icon = "✅" if st == "completed" else ("❌" if st == "failed" else "⏳")
            recent_task_text.append(
                f"{status_icon} [{st.upper()}] {t.get('artist')} - {t.get('title')} ({pct:.0f}%)"
            )
        tasks_summary = (
            "\n".join(recent_task_text) if recent_task_text else "暂无下载任务记录。"
        )

        api_token = self._get_api_token()
        workbench_url = (
            f"/api/v1/plugin/SpotifyMusic/ui?token={api_token}"
            if api_token
            else "/api/v1/plugin/SpotifyMusic/ui"
        )

        return [
            {
                "component": "VCard",
                "props": {"class": "mb-4", "color": "primary", "variant": "tonal"},
                "content": [
                    {
                        "component": "VCardTitle",
                        "text": "🎧 Spotify 音乐全功能独立工作台",
                    },
                    {
                        "component": "VCardText",
                        "text": (
                            "推荐使用专属 Web 工作台：无需在设置中反复保存，直接在网页中实时检索、查看专辑封面、一键下载、Spotify 链接解析与增量订阅管理，并实时监控下载转码进度。\n\n"
                            "💡 点击下方按钮将在新标签页中打开工作台，已自动注入 MoviePilot 鉴权凭证。"
                        ),
                    },
                    {
                        "component": "VCardActions",
                        "content": [
                            {
                                "component": "VBtn",
                                "props": {
                                    "color": "primary",
                                    "href": workbench_url,
                                    "target": "_blank",
                                    "text": "🚀 立即进入音乐工作台 (免配 Token)",
                                    "prependIcon": "mdi-open-in-new",
                                    "block": True,
                                },
                            }
                        ],
                    },
                ],
            },
            {
                "component": "VCard",
                "props": {"class": "mb-4"},
                "content": [
                    {
                        "component": "VCardTitle",
                        "text": "🎵 运行概况与订阅统计",
                    },
                    {
                        "component": "VCardText",
                        "text": (
                            f"【运行状态】已激活订阅: {len(subs)} 个 | 进行中任务: {len(active_tasks)} 个 | 已完成: {len(completed_tasks)} 条 | 失败: {len(failed_tasks)} 条\n\n"
                            f"【已订阅的歌单与艺术家】\n{subs_summary}\n\n"
                            f"【最近任务动态】\n{tasks_summary}"
                        ),
                    },
                ],
            },
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        """注册插件 REST API 路由，供前端面板与外部调用。"""
        return [
            {
                "path": "/ui",
                "endpoint": self.api_ui,
                "methods": ["GET"],
                "allow_anonymous": True,
                "summary": "音乐搜索与订阅独立工作台 UI",
                "description": "返回内置 SPA 交互工作台 HTML 界面",
            },
            {
                "path": "/search/query",
                "endpoint": self.api_search_query,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "检索歌曲候选列表",
                "description": "根据关键词检索匹配的歌曲列表（含封面、时长、多艺术家）",
            },
            {
                "path": "/resolve",
                "endpoint": self.api_resolve_url,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "解析 Spotify 链接",
                "description": "解析 Spotify 单曲、专辑、歌单或艺术家链接并返回元数据及曲目列表",
            },
            {
                "path": "/download/single",
                "endpoint": self.api_download_single,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "下载单曲",
                "description": "提交单曲至下载队列",
            },
            {
                "path": "/download/batch",
                "endpoint": self.api_download_batch,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "批量下载曲目",
                "description": "批量提交专辑或歌单曲目至下载队列",
            },
            {
                "path": "/subscriptions",
                "endpoint": self.api_list_subscriptions,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "获取订阅列表",
                "description": "获取所有 Spotify 歌单与艺术家订阅",
            },
            {
                "path": "/subscribe",
                "endpoint": self.api_add_subscription,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "添加订阅",
                "description": "添加 Spotify 歌单或艺术家订阅，支持设置【仅监控新增】",
            },
            {
                "path": "/subscriptions/add",
                "endpoint": self.api_add_subscription,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "添加订阅（别名）",
                "description": "添加 Spotify 歌单或艺术家订阅",
            },
            {
                "path": "/subscriptions/{sub_id}",
                "endpoint": self.api_delete_subscription,
                "methods": ["DELETE"],
                "auth": "bear",
                "summary": "删除订阅",
                "description": "删除指定的 Spotify 订阅",
            },
            {
                "path": "/subscriptions/{sub_id}/delete",
                "endpoint": self.api_delete_subscription,
                "methods": ["POST", "DELETE"],
                "auth": "bear",
                "summary": "删除订阅（别名）",
                "description": "删除指定的 Spotify 订阅",
            },
            {
                "path": "/subscriptions/{sub_id}/sync",
                "endpoint": self.api_sync_subscription,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "立即检查单项订阅",
                "description": "立即触发指定订阅的增量更新检查",
            },
            {
                "path": "/tasks",
                "endpoint": self.api_list_tasks,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "获取下载任务列表",
                "description": "获取进行中、排队中与历史下载任务",
            },
            {
                "path": "/tasks/clear_completed",
                "endpoint": self.api_clear_completed_tasks,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "清理已完成任务",
                "description": "清空所有状态为 completed 的任务记录",
            },
            {
                "path": "/tasks/retry_failed",
                "endpoint": self.api_retry_failed_tasks,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "重试失败任务",
                "description": "重置所有失败任务并重新排队",
            },
        ]

    # ==================== API 路由处理逻辑 ====================

    def api_ui(self) -> Any:
        """返回内置独立音乐搜索工作台 HTML。"""
        api_token = self._get_api_token()
        return HTMLResponse(
            content=render_music_workbench_html(default_token=api_token)
        )

    def api_search_query(
        self,
        query: str = "",
        q: str = "",
        keyword: str = "",
        limit: int = 15,
    ) -> Dict[str, Any]:
        """全分类搜索音乐候选项（单曲、专辑、艺术家、歌单）。"""
        kw = (query or q or keyword or "").strip()
        try:
            candidates = matcher.search_music_all(
                query=kw,
                limit=limit,
                proxy=self._proxy or None,
                spotify_client_id=self._spotify_client_id or None,
                spotify_client_secret=self._spotify_client_secret or None,
            )
            if isinstance(candidates, list):
                data = {
                    "tracks": candidates,
                    "albums": [],
                    "artists": [],
                    "playlists": [],
                }
            else:
                data = candidates
            return {"code": 0, "msg": "ok", "data": data, "success": True}
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 搜索音乐异常: {e}")
            return {
                "code": -1,
                "msg": str(e),
                "data": {"tracks": [], "albums": [], "artists": [], "playlists": []},
                "success": False,
                "message": str(e),
            }

    # ==================== API 路由处理逻辑 ====================

    def api_resolve_url(self, url: str) -> Dict[str, Any]:
        """解析 Spotify 链接。"""
        if not url:
            return {"success": False, "message": "URL 不能为空"}
        try:
            entity = spotify.resolve_spotify_entity(
                url,
                self._proxy or None,
                spotify_client_id=self._spotify_client_id or None,
                spotify_client_secret=self._spotify_client_secret or None,
            )
            return {"success": True, "data": entity}
        except Exception as e:
            return {"success": False, "message": f"解析 Spotify 链接失败: {str(e)}"}

    def api_download_single(self, track: Dict[str, Any]) -> Dict[str, Any]:
        """提交单曲下载。"""
        if not self._queue_mgr:
            return {"success": False, "message": "插件尚未初始化"}
        actual_track = track.get("track") if isinstance(track.get("track"), dict) else track
        task_id = self._queue_mgr.submit_track(actual_track)
        return {"success": True, "data": {"task_id": task_id}}

    def api_download_batch(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """批量提交下载。"""
        if not self._queue_mgr:
            return {"success": False, "message": "插件尚未初始化"}
        tracks = payload.get("tracks") or []
        playlist_name = payload.get("playlist_name")
        task_ids = []
        for t in tracks:
            tid = self._queue_mgr.submit_track(t, playlist_name=playlist_name)
            task_ids.append(tid)
        return {"success": True, "data": {"task_ids": task_ids, "count": len(task_ids)}}

    def api_list_subscriptions(self) -> Dict[str, Any]:
        """查询所有订阅。"""
        if not self._db:
            return {"success": True, "data": []}
        return {"success": True, "data": self._db.list_subscriptions()}

    def api_add_subscription(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """添加订阅（支持 only_new 仅监控新增选项）。"""
        if not self._db or not self._queue_mgr:
            return {"success": False, "message": "插件尚未初始化"}

        url = str(payload.get("url") or "").strip()
        sync_mode = str(payload.get("sync_mode") or "all").strip()  # 'all' 或 'only_new'
        interval_min = int(payload.get("interval_minutes") or self._interval_minutes)

        try:
            # 1. 解析远端元数据与曲目列表
            entity = spotify.resolve_spotify_entity(
                url,
                self._proxy or None,
                spotify_client_id=self._spotify_client_id or None,
                spotify_client_secret=self._spotify_client_secret or None,
            )
            sub_type = entity.get("type", "playlist")
            spotify_id = entity.get("spotify_id", "")
            name = entity.get("name") or "未命名订阅"
            cover_url = entity.get("cover_url", "")
            tracks = entity.get("tracks") or []

            # 2. 存入数据库
            sub_record = self._db.add_subscription(
                sub_type=sub_type,
                spotify_id=spotify_id,
                name=name,
                url=url,
                cover_url=cover_url,
                interval_minutes=interval_min,
                sync_mode=sync_mode,
            )
            sub_id = sub_record.get("id")

            # 3. 处理存量曲目
            if sync_mode == "only_new":
                # 仅监控新增：直接将当前所有唱片与曲目作为存量基准入库，不排入下载队列
                if sub_type == "artist":
                    releases = entity.get("releases") or []
                    if not releases and self._spotify_client_id and self._spotify_client_secret:
                        releases = spotify.get_spotify_artist_albums(
                            spotify_id,
                            self._spotify_client_id,
                            self._spotify_client_secret,
                            limit=50,
                            max_pages=20,
                            proxy=self._proxy or None,
                        )
                    for rel in releases:
                        rel_id = rel.get("spotify_id")
                        if rel_id:
                            self._db.record_track_history(
                                sub_id=sub_id,
                                track_spotify_id=rel_id,
                                track_name=rel.get("name", ""),
                                artist_name=name,
                                album_name=rel.get("name", ""),
                                status="existing_base",
                            )
                    base_count = self._db.batch_record_existing_base(sub_id, tracks)
                    total_count = max(len(tracks), len(releases))
                    self._db.update_subscription_stats(sub_id, total_tracks=total_count)
                    msg = (
                        f"艺术家订阅成功！已建立 {len(releases)} 张唱片与 {base_count} 首曲目的存量基准，"
                        f"后续仅自动同步该艺术家新发行的专辑与单曲。"
                    )
                else:
                    base_count = self._db.batch_record_existing_base(sub_id, tracks)
                    self._db.update_subscription_stats(sub_id, total_tracks=len(tracks))
                    msg = f"订阅成功！已建立 {base_count} 首存量基准，后续仅同步新增曲目。"
            else:
                if sub_type == "artist":
                    # 艺术家全量同步：异步触发全部 Releases 与专辑曲目下载
                    threading.Thread(
                        target=self._sync_single_subscription,
                        args=(sub_record,),
                        daemon=True,
                        name=f"SpotifyMusicArtistSync-{sub_id}",
                    ).start()
                    msg = "艺术家全量订阅已创建！已启动后台全量同步该艺术家全部唱片与单曲。"
                else:
                    # 歌单/专辑全量同步：将现有所有曲目排入下载队列
                    enqueued = 0
                    for t in tracks:
                        self._queue_mgr.submit_track(t, subscription_id=sub_id, playlist_name=name)
                        enqueued += 1
                    self._db.update_subscription_stats(sub_id, total_tracks=len(tracks))
                    msg = f"订阅成功！已将全部 {enqueued} 首曲目推入下载队列。"

            return {"success": True, "message": msg, "data": sub_record}
        except Exception as e:
            return {"success": False, "message": f"添加订阅失败: {str(e)}"}

    def api_delete_subscription(self, sub_id: int) -> Dict[str, Any]:
        """删除订阅。"""
        if not self._db:
            return {"success": False, "message": "插件尚未初始化"}
        self._db.delete_subscription(sub_id)
        return {"success": True, "message": "订阅已删除"}

    def api_sync_subscription(self, sub_id: int) -> Dict[str, Any]:
        """立即手动检查指定订阅。"""
        if not self._db:
            return {"success": False, "message": "插件尚未初始化"}
        sub = self._db.get_subscription(sub_id)
        if not sub:
            return {"success": False, "message": "订阅不存在"}

        threading.Thread(
            target=self._sync_single_subscription,
            args=(sub,),
            daemon=True,
            name=f"SpotifyMusicSync-{sub_id}",
        ).start()
        return {"success": True, "message": f"正在后台同步订阅 [{sub.get('name')}]"}

    def api_list_tasks(self, status: Optional[str] = None) -> Dict[str, Any]:
        """查询任务队列。"""
        if not self._db:
            return {"success": True, "data": []}
        return {"success": True, "data": self._db.list_tasks(status=status, limit=100)}

    def api_clear_completed_tasks(self) -> Dict[str, Any]:
        """清理已完成任务。"""
        if not self._db:
            return {"success": False, "message": "插件尚未初始化"}
        count = self._db.clear_completed_tasks()
        return {"success": True, "message": f"已清理 {count} 条已完成任务"}

    def api_retry_failed_tasks(self) -> Dict[str, Any]:
        """重试失败任务。"""
        if not self._db or not self._queue_mgr:
            return {"success": False, "message": "插件尚未初始化"}
        failed_ids = self._db.retry_failed_tasks()
        for fid in failed_ids:
            task = self._db.get_task(fid)
            if task:
                self._queue_mgr.submit_track(
                    {
                        "title": task["title"],
                        "artist": task["artist"],
                        "album": task["album"],
                        "cover_url": task["cover_url"],
                        "spotify_id": task["spotify_id"],
                    },
                    subscription_id=task.get("subscription_id"),
                )
        return {"success": True, "message": f"已重试 {len(failed_ids)} 个失败任务"}

    # ==================== 后台调度与同步逻辑 ====================

    def check_all_subscriptions(self) -> None:
        """定时任务：巡检所有已启用的订阅。"""
        if not self._enabled or not self._db:
            return
        threading.Thread(
            target=self._sync_all_subscriptions_worker,
            daemon=True,
            name="SpotifyMusicSyncAll",
        ).start()

    def _sync_all_subscriptions_worker(self) -> None:
        """后台线程：遍历检查所有启用订阅。"""
        subs = self._db.list_subscriptions() if self._db else []
        for sub in subs:
            if not sub.get("enabled"):
                continue
            if sub.get("sync_mode") == "once":
                continue
            self._sync_single_subscription(sub)

    def _sync_single_subscription(self, sub: Dict[str, Any]) -> None:
        """执行单项订阅的增量检查与下载排队。"""
        sub_id = sub["id"]
        sub_name = sub.get("name", "")
        url = sub.get("url", "")
        logger.info(f"[{self.plugin_name}] 开始检查订阅更新: {sub_name} ({url})")

        try:
            entity = spotify.resolve_spotify_entity(
                url,
                self._proxy or None,
                spotify_client_id=self._spotify_client_id or None,
                spotify_client_secret=self._spotify_client_secret or None,
            )
            tracks = entity.get("tracks") or []

            # 如果是艺术家，解析每个新 Release
            if sub.get("type") == "artist":
                releases = entity.get("releases") or []
                if not releases and self._spotify_client_id and self._spotify_client_secret:
                    releases = spotify.get_spotify_artist_albums(
                        sub.get("spotify_id"),
                        self._spotify_client_id,
                        self._spotify_client_secret,
                        limit=50,
                        max_pages=20,
                        proxy=self._proxy or None,
                    )
                new_releases_count = 0
                new_tracks_count = 0
                for rel in releases:
                    rel_id = rel.get("spotify_id")
                    if not rel_id or self._db.is_track_in_history(sub_id, rel_id):
                        continue
                    # 拉取该 release 详情
                    rel_url = f"https://open.spotify.com/album/{rel_id}"
                    try:
                        album_ent = spotify.resolve_spotify_entity(
                            rel_url,
                            self._proxy or None,
                            spotify_client_id=self._spotify_client_id or None,
                            spotify_client_secret=self._spotify_client_secret or None,
                        )
                        album_tracks = album_ent.get("tracks") or []
                        for t in album_tracks:
                            t_id = t.get("spotify_id")
                            if t_id and not self._db.is_track_in_history(sub_id, t_id):
                                self._queue_mgr.submit_track(t, subscription_id=sub_id, playlist_name=sub_name)
                                self._db.record_track_history(
                                    sub_id=sub_id,
                                    track_spotify_id=t_id,
                                    track_name=t.get("title", ""),
                                    artist_name=t.get("artist", sub_name),
                                    album_name=t.get("album", rel.get("name", "")),
                                    status="downloaded",
                                )
                                new_tracks_count += 1
                        self._db.record_track_history(
                            sub_id=sub_id,
                            track_spotify_id=rel_id,
                            track_name=rel.get("name", ""),
                            artist_name=sub_name,
                            album_name=rel.get("name", ""),
                            status="downloaded",
                        )
                        new_releases_count += 1
                    except Exception as ex:
                        logger.debug(f"拉取艺术家 Release {rel_id} 异常: {ex}")
                self._db.update_subscription_stats(sub_id, total_tracks=len(releases))
                if new_releases_count > 0:
                    logger.info(
                        f"[{self.plugin_name}] 艺术家 [{sub_name}] 发现 {new_releases_count} 张新唱片"
                        f" (共 {new_tracks_count} 首曲目)，已加入下载队列"
                    )
                    if self._notify_success:
                        self.post_message(
                            mtype=NotificationType.Plugin,
                            title=f"🎵 艺术家发布新作品: {sub_name}",
                            text=f"检测到 {new_releases_count} 张新唱片（共 {new_tracks_count} 首曲目），正在后台自动下载并整理归档。",
                        )
                return

            # 歌单/专辑的增量比对
            new_tracks_count = 0
            for t in tracks:
                t_id = t.get("spotify_id")
                if not t_id:
                    continue
                # 比对历史表
                if not self._db.is_track_in_history(sub_id, t_id):
                    self._queue_mgr.submit_track(t, subscription_id=sub_id, playlist_name=sub_name)
                    new_tracks_count += 1

            self._db.update_subscription_stats(sub_id, total_tracks=len(tracks))
            if new_tracks_count > 0:
                logger.info(f"[{self.plugin_name}] 订阅 [{sub_name}] 发现 {new_tracks_count} 首新曲目，已加入下载队列")
                if self._notify_success:
                    self.post_message(
                        mtype=NotificationType.Plugin,
                        title=f"🎵 Spotify 订阅更新: {sub_name}",
                        text=f"检测到 {new_tracks_count} 首新曲目，正在后台自动下载并整理归档。",
                    )
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 检查订阅 [{sub_name}] 失败: {e}")

    def _on_download_completed(self, track_info: Dict[str, Any], final_path: Path) -> None:
        """曲目下载与转移完成时的回调通知。"""
        title = track_info.get("title") or track_info.get("name") or "Unknown"
        artist = track_info.get("artist") or "Unknown"
        logger.info(f"[{self.plugin_name}] 曲目完成归档: {artist} - {title} -> {final_path}")

        if self._notify_success:
            self.post_message(
                mtype=NotificationType.Plugin,
                title="🎵 音乐下载完成",
                text=f"曲目: {title}\n艺术家: {artist}\n专辑: {track_info.get('album', '')}\n路径: {final_path.name}",
            )

    def _handle_manual_add_url(self, url: str, sync_mode: str) -> None:
        """后台异步处理表单中提交的 Spotify 链接。"""
        if not self._db or not self._queue_mgr:
            return
        logger.info(f"[{self.plugin_name}] 正在处理手动提交的 Spotify 链接: {url} (模式: {sync_mode})")
        try:
            entity = spotify.resolve_spotify_entity(
                url,
                self._proxy or None,
                spotify_client_id=self._spotify_client_id or None,
                spotify_client_secret=self._spotify_client_secret or None,
            )
            sub_type = entity.get("type", "playlist")
            spotify_id = entity.get("spotify_id", "")
            name = entity.get("name") or "未命名"
            cover_url = entity.get("cover_url", "")
            tracks = entity.get("tracks") or []

            if sync_mode == "once" or sub_type == "track":
                # 单次下载：直接排入下载队列，不计入定时巡检订阅
                for t in tracks:
                    self._queue_mgr.submit_track(t, playlist_name=name)
                self.post_message(
                    mtype=NotificationType.Plugin,
                    title=f"🎵 Spotify 链接已解析 ({name})",
                    text=f"类型: {sub_type}\n已将 {len(tracks)} 首曲目提交至下载队列，正在后台自动转码与内嵌元数据。",
                )
            elif sync_mode == "only_new":
                # 长期增量订阅（仅监控新增）
                sub_record = self._db.add_subscription(
                    sub_type=sub_type,
                    spotify_id=spotify_id,
                    name=name,
                    url=url,
                    cover_url=cover_url,
                    interval_minutes=self._interval_minutes,
                    sync_mode="only_new",
                )
                sub_id = sub_record.get("id")
                if sub_type == "artist":
                    releases = entity.get("releases") or []
                    if not releases and self._spotify_client_id and self._spotify_client_secret:
                        releases = spotify.get_spotify_artist_albums(
                            spotify_id,
                            self._spotify_client_id,
                            self._spotify_client_secret,
                            limit=50,
                            max_pages=20,
                            proxy=self._proxy or None,
                        )
                    for rel in releases:
                        rel_id = rel.get("spotify_id")
                        if rel_id:
                            self._db.record_track_history(
                                sub_id=sub_id,
                                track_spotify_id=rel_id,
                                track_name=rel.get("name", ""),
                                artist_name=name,
                                album_name=rel.get("name", ""),
                                status="existing_base",
                            )
                    base_count = self._db.batch_record_existing_base(sub_id, tracks)
                    total_count = max(len(tracks), len(releases))
                    self._db.update_subscription_stats(sub_id, total_tracks=total_count)
                    self.post_message(
                        mtype=NotificationType.Plugin,
                        title=f"🎵 Spotify 艺术家订阅成功 ({name})",
                        text=f"已成功添加长期订阅！已建立 {len(releases)} 张唱片与 {base_count} 首曲目的存量基准，后续将自动监控并下载新增发行的专辑与单曲。",
                    )
                else:
                    base_count = self._db.batch_record_existing_base(sub_id, tracks)
                    self._db.update_subscription_stats(sub_id, total_tracks=len(tracks))
                    self.post_message(
                        mtype=NotificationType.Plugin,
                        title=f"🎵 Spotify 订阅添加成功 ({name})",
                        text=f"已成功添加长期订阅！已建立 {base_count} 首存量基准曲目，后续将自动监控并下载新增歌曲。",
                    )
            else:
                # 长期全量订阅
                sub_record = self._db.add_subscription(
                    sub_type=sub_type,
                    spotify_id=spotify_id,
                    name=name,
                    url=url,
                    cover_url=cover_url,
                    interval_minutes=self._interval_minutes,
                    sync_mode="all",
                )
                sub_id = sub_record.get("id")
                if sub_type == "artist":
                    threading.Thread(
                        target=self._sync_single_subscription,
                        args=(sub_record,),
                        daemon=True,
                        name=f"SpotifyMusicArtistSync-{sub_id}",
                    ).start()
                    self.post_message(
                        mtype=NotificationType.Plugin,
                        title=f"🎵 Spotify 艺术家全量订阅成功 ({name})",
                        text="已创建艺术家全量订阅，后台正自动同步下载该艺术家的全部历史唱片与曲目。",
                    )
                else:
                    enqueued = 0
                    for t in tracks:
                        self._queue_mgr.submit_track(t, subscription_id=sub_id, playlist_name=name)
                        enqueued += 1
                    self._db.update_subscription_stats(sub_id, total_tracks=len(tracks))
                    self.post_message(
                        mtype=NotificationType.Plugin,
                        title=f"🎵 Spotify 全量订阅添加成功 ({name})",
                        text=f"已将 {enqueued} 首曲目推入下载队列，并将该歌单加入定期巡检订阅列表。",
                    )
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 处理 Spotify 链接失败: {e}")
            self.post_message(
                mtype=NotificationType.Plugin,
                title="❌ Spotify 链接解析失败",
                text=f"链接: {url}\n错误原因: {e}",
            )

    def _handle_manual_search_download(self, keyword: str) -> None:
        """后台异步处理手动搜索并下载。"""
        if not self._queue_mgr:
            return
        logger.info(f"[{self.plugin_name}] 正在搜索并下载单曲: {keyword}")
        try:
            parts = keyword.split(maxsplit=1)
            artist = parts[0] if len(parts) > 1 else ""
            title = parts[1] if len(parts) > 1 else keyword
            track_info = {
                "title": title,
                "artist": artist,
                "album": "",
                "cover_url": "",
            }
            task_id = self._queue_mgr.submit_track(track_info)
            self.post_message(
                mtype=NotificationType.Plugin,
                title=f"🎵 已提交歌曲搜索下载: {keyword}",
                text=f"已生成下载任务，正在匹配 YouTube Music 最优音源并下载打标。",
            )
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 搜索单曲失败: {e}")

    def stop_service(self) -> None:
        """停止插件后台队列与服务。"""
        if self._queue_mgr:
            self._queue_mgr.stop()
            self._queue_mgr = None
        self._db = None


