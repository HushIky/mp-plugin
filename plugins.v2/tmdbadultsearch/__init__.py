"""TMDB 成人内容(18+)搜索增强插件。

在主动搜索媒体时，通过切面在底层 TMDB API 检索方法中注入 adult=True (include_adult=true) 参数，
允许用户搜索成人影视内容；
同时利用 ContextVar 上下文变量严格限定仅在主动搜索链路中生效，
彻底隔离后台自动化下载、整理入库与刮削匹配识别，互不影响。
"""

from __future__ import annotations

import functools
from contextvars import ContextVar
from typing import Any, Dict, List, Optional, Tuple

from app.log import logger
from app.plugins import _PluginBase

# 标记当前执行上下文是否处于“用户主动媒体搜索”期间
_in_media_search: ContextVar[bool] = ContextVar("tmdbadultsearch_in_media_search", default=False)

# 保存原始方法引用以便在插件停用或开关关闭时无残留恢复
_ORIG_METHODS: Dict[str, Any] = {}
_HOOKED: bool = False
_TOTAL_SEARCH_COUNT: int = 0


def _apply_tmdb_adult_hooks() -> None:
    """对 TMDB 搜索施加切面包装，在 search_medias 期间注入 adult=True。"""
    global _HOOKED, _ORIG_METHODS
    if _HOOKED:
        return

    try:
        from app.modules.themoviedb import TheMovieDbModule
        from app.modules.themoviedb.tmdbv3api.objs.search import Search

        # 1. 包装 TheMovieDbModule.search_medias 与 async_search_medias，标记搜索上下文
        _ORIG_METHODS["TheMovieDbModule.search_medias"] = TheMovieDbModule.search_medias
        _ORIG_METHODS["TheMovieDbModule.async_search_medias"] = TheMovieDbModule.async_search_medias

        @functools.wraps(_ORIG_METHODS["TheMovieDbModule.search_medias"])
        def patched_search_medias(self, meta, media_source=None):
            token = _in_media_search.set(True)
            try:
                return _ORIG_METHODS["TheMovieDbModule.search_medias"](self, meta, media_source)
            finally:
                _in_media_search.reset(token)

        @functools.wraps(_ORIG_METHODS["TheMovieDbModule.async_search_medias"])
        async def patched_async_search_medias(self, meta, media_source=None):
            token = _in_media_search.set(True)
            try:
                return await _ORIG_METHODS["TheMovieDbModule.async_search_medias"](self, meta, media_source)
            finally:
                _in_media_search.reset(token)

        TheMovieDbModule.search_medias = patched_search_medias
        TheMovieDbModule.async_search_medias = patched_async_search_medias

        # 2. 包装 Search 对象的底层请求方法，仅在搜索上下文中注入 adult=True
        for m_name in ("movies", "tv_shows", "multi", "async_movies", "async_tv_shows", "async_multi"):
            if hasattr(Search, m_name):
                orig_func = getattr(Search, m_name)
                _ORIG_METHODS[f"Search.{m_name}"] = orig_func

                if m_name.startswith("async_"):
                    def make_async_wrapper(orig_f):
                        @functools.wraps(orig_f)
                        async def async_wrapper(self, term, adult=None, *args, **kwargs):
                            global _TOTAL_SEARCH_COUNT
                            if adult is None and _in_media_search.get():
                                adult = True
                                _TOTAL_SEARCH_COUNT += 1
                            return await orig_f(self, term, adult=adult, *args, **kwargs)
                        return async_wrapper

                    setattr(Search, m_name, make_async_wrapper(orig_func))
                else:
                    def make_sync_wrapper(orig_f):
                        @functools.wraps(orig_f)
                        def sync_wrapper(self, term, adult=None, *args, **kwargs):
                            global _TOTAL_SEARCH_COUNT
                            if adult is None and _in_media_search.get():
                                adult = True
                                _TOTAL_SEARCH_COUNT += 1
                            return orig_f(self, term, adult=adult, *args, **kwargs)
                        return sync_wrapper

                    setattr(Search, m_name, make_sync_wrapper(orig_func))

        _HOOKED = True
        logger.info("[TMDB成人内容搜索] 已成功启用 TMDB 成人内容(18+)搜索增强切面")
    except Exception as e:
        logger.error(f"[TMDB成人内容搜索] 启用 TMDB 成人搜索切面失败: {e}")


def _restore_tmdb_adult_hooks() -> None:
    """恢复原始 TMDB 方法，解除切面拦截。"""
    global _HOOKED, _ORIG_METHODS
    if not _HOOKED:
        return

    try:
        from app.modules.themoviedb import TheMovieDbModule
        from app.modules.themoviedb.tmdbv3api.objs.search import Search

        if "TheMovieDbModule.search_medias" in _ORIG_METHODS:
            TheMovieDbModule.search_medias = _ORIG_METHODS["TheMovieDbModule.search_medias"]
        if "TheMovieDbModule.async_search_medias" in _ORIG_METHODS:
            TheMovieDbModule.async_search_medias = _ORIG_METHODS["TheMovieDbModule.async_search_medias"]

        for m_name in ("movies", "tv_shows", "multi", "async_movies", "async_tv_shows", "async_multi"):
            key = f"Search.{m_name}"
            if key in _ORIG_METHODS and hasattr(Search, m_name):
                setattr(Search, m_name, _ORIG_METHODS[key])

        _ORIG_METHODS.clear()
        _HOOKED = False
        logger.info("[TMDB成人内容搜索] 已恢复原始 TMDB 搜索配置，已清理所有切面")
    except Exception as e:
        logger.error(f"[TMDB成人内容搜索] 恢复原始 TMDB 搜索配置异常: {e}")


class TmdbAdultSearch(_PluginBase):
    """TMDB 成人内容(18+)搜索增强插件主类。"""

    # 插件元信息
    plugin_name = "TMDB成人内容搜索"
    plugin_desc = "在主动搜索媒体时允许 TMDB 返回成人影视内容 (18+)，且完全不影响后台自动化入库、整理与刮削匹配识别。"
    plugin_icon = "tmdbadultsearch.png"
    plugin_version = "1.0.0"
    plugin_label = "搜索管理"
    plugin_author = "local"
    plugin_order = 21
    auth_level = 1

    # 内部运行状态
    _enabled: bool = False

    def init_plugin(self, config: dict = None) -> None:
        """根据配置初始化并挂载 TMDB 成人内容搜索切面。"""
        self.stop_service()
        self._enabled = False

        if not config:
            return

        self._enabled = bool(config.get("enabled", False))

        if self._enabled:
            _apply_tmdb_adult_hooks()
        else:
            _restore_tmdb_adult_hooks()

        logger.info(f"[{self.plugin_name}] 初始化完成：启用={self._enabled}")

    def get_state(self) -> bool:
        """获取插件启用状态。"""
        return self._enabled

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
        """拼装插件配置表单 (Vuetify JSON 结构)。"""
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
                                            "label": "启用 TMDB 成人内容搜索 (18+)",
                                            "hint": "开启后在主动搜索影视剧时允许 TMDB 返回成人内容，完全不影响后台自动识别与匹配",
                                            "persistentHint": True,
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
        default_config = {
            "enabled": False,
        }
        return form_schema, default_config

    def get_page(self) -> Optional[List[dict]]:
        """拼装插件详情页面，展示运行概况与说明。"""
        if not self._enabled:
            return [
                {
                    "component": "VAlert",
                    "props": {
                        "type": "info",
                        "text": "插件尚未启用。请在配置中开启开关以支持在搜索影视剧时显示 TMDB 成人内容 (18+)。",
                    },
                }
            ]

        return [
            {
                "component": "VCard",
                "props": {"class": "mb-4", "color": "primary", "variant": "tonal"},
                "content": [
                    {
                        "component": "VCardTitle",
                        "text": "🔞 TMDB 成人搜索服务已就绪",
                    },
                    {
                        "component": "VCardText",
                        "text": (
                            f"【运行状态】🟢 已生效 (支持 18+ 影视检索)\n"
                            f"【累计放行】{_TOTAL_SEARCH_COUNT} 次成人搜索请求\n\n"
                            "💡 机制原理与安全保障：\n"
                            "1. 主动搜索放行：仅在用户主动发起媒体搜索（Web 界面检索、微信/Telegram 对话搜索）时，动态注入 adult=True (include_adult=true) 参数。\n"
                            "2. 后台严格隔离：下载完成后的自动整理、刮削入库与元数据匹配等流程，依然强制保持为严格的非成人识别，杜绝媒体库误匹配污染。"
                        ),
                    },
                ],
            }
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        """注册插件 API 端点。"""
        return []

    @property
    def total_search_count(self) -> int:
        """获取累计放行成人搜索请求次数。"""
        return _TOTAL_SEARCH_COUNT

    def stop_service(self) -> None:
        """停止插件服务并恢复原始 TMDB 方法。"""
        self._enabled = False
        _restore_tmdb_adult_hooks()
