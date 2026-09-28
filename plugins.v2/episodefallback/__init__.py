"""TMDB 待定集数订阅兜底插件。

解决 TMDB 尚未录入剧集集数导致无法添加订阅的问题。
当 TMDB 总集数未录入或为 0 时，自动提供可配置的默认兜底集数（默认 1 集），并支持自定义规则与记录。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.event import Event, eventmanager
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.event import SubscribeEpisodesRefreshEventData
from app.schemas.types import ChainEventType, NotificationType


def _now_str() -> str:
    """获取当前格式化时间字符串。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class EpisodeFallback(_PluginBase):
    """TMDB 待定集数订阅兜底插件主类。"""

    # 插件元信息
    plugin_name = "TMDB待定集数订阅兜底"
    plugin_desc = "解决 TMDB 尚未录入剧集集数导致无法添加订阅的问题。当 TMDB 总集数未录入或为 0 时，自动提供可配置的默认兜底集数（默认 1 集），解除订阅创建拦截。"
    plugin_icon = "episodefallback.png"
    plugin_version = "1.0.0"
    plugin_label = "订阅管理"
    plugin_author = "local"
    plugin_order = 20
    auth_level = 1

    # 内部运行状态
    _enabled: bool = False
    _default_episodes: int = 1
    _notify: bool = True
    _custom_rules_text: str = ""
    _custom_rules: List[Dict[str, Any]] = []
    _history: List[Dict[str, Any]] = []
    _max_history: int = 50

    def init_plugin(self, config: dict = None) -> None:
        """根据用户配置初始化插件状态与自定义规则。"""
        self.stop_service()
        self._enabled = False
        self._default_episodes = 1
        self._notify = True
        self._custom_rules_text = ""
        self._custom_rules = []

        if not config:
            return

        self._enabled = bool(config.get("enabled", False))
        try:
            self._default_episodes = max(1, int(config.get("default_episodes") or 1))
        except (ValueError, TypeError):
            self._default_episodes = 1
        self._notify = bool(config.get("notify", True))
        self._custom_rules_text = str(config.get("custom_rules") or "").strip()
        self._custom_rules = self._parse_custom_rules(self._custom_rules_text)

        # 加载历史记录
        self._load_history()

        logger.info(
            f"[{self.plugin_name}] 初始化完成：启用={self._enabled}，"
            f"默认兜底集数={self._default_episodes}，自定义规则数={len(self._custom_rules)}"
        )

    def get_state(self) -> bool:
        """获取插件启用状态。"""
        return self._enabled

    def _parse_custom_rules(self, text: str) -> List[Dict[str, Any]]:
        """解析用户配置的多行自定义规则。

        格式支持：
        - 标题关键词:集数 (如：凡人修仙传:52)
        - TMDB_ID:集数 (如：tmdb:12345:24 或 12345:24)
        """
        rules = []
        if not text:
            return rules
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":")
            if len(parts) >= 2:
                key = ":".join(parts[:-1]).strip()
                try:
                    ep_count = int(parts[-1].strip())
                    if ep_count > 0:
                        rules.append({"pattern": key, "episodes": ep_count})
                except ValueError:
                    continue
        return rules

    def _match_custom_rule(self, title: str, tmdb_id: Optional[str] = None) -> Optional[int]:
        """根据剧名或 TMDB ID 匹配用户自定义的特定集数规则。"""
        for rule in self._custom_rules:
            pattern = rule.get("pattern", "")
            episodes = rule.get("episodes", 1)
            # 1. 按 TMDB ID 匹配
            if tmdb_id and (pattern == str(tmdb_id) or pattern == f"tmdb:{tmdb_id}"):
                return episodes
            # 2. 按剧集名称正则/子串匹配
            if title and (pattern.lower() in title.lower() or re.search(pattern, title, re.IGNORECASE)):
                return episodes
        return None

    def _get_history_file(self) -> Path:
        """获取持久化历史记录 JSON 文件路径。"""
        data_path = self.get_data_path()
        return data_path / "fallback_history.json"

    def _load_history(self) -> None:
        """从本地磁盘读取历史兜底记录。"""
        try:
            h_file = self._get_history_file()
            if h_file.exists():
                with open(h_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self._history = data[-self._max_history:]
        except Exception as e:
            logger.debug(f"[{self.plugin_name}] 读取历史记录异常: {e}")
            self._history = []

    def _save_history(self) -> None:
        """持久化历史兜底记录到本地磁盘。"""
        try:
            h_file = self._get_history_file()
            h_file.parent.mkdir(parents=True, exist_ok=True)
            with open(h_file, "w", encoding="utf-8") as f:
                json.dump(self._history[-self._max_history:], f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"[{self.plugin_name}] 保存历史记录异常: {e}")

    @eventmanager.register(ChainEventType.SubscribeEpisodesRefresh)
    def on_subscribe_episodes_refresh(self, event: Event) -> None:
        """监听订阅总集数推算事件，当 TMDB 总集数未录入时提供兜底支持。"""
        if not self._enabled or not event or not event.event_data:
            return

        event_data: SubscribeEpisodesRefreshEventData = event.event_data

        # 仅当 TMDB 识别到的当前季总集数为空或 <= 0 时介入
        if event_data.current_total_episode and event_data.current_total_episode > 0:
            return

        mediainfo = event_data.mediainfo
        title = mediainfo.title if mediainfo else "未知剧集"
        season = event_data.season if event_data.season is not None else 1
        tmdb_id = getattr(mediainfo, "tmdb_id", None) or event_data.media_id

        # 检查是否匹配用户自定义特定规则
        matched_episodes = self._match_custom_rule(title, str(tmdb_id) if tmdb_id else None)
        target_episodes = matched_episodes if matched_episodes else self._default_episodes
        target_episodes = max(1, target_episodes)

        # 填充事件数据以覆盖主程序集数
        event_data.updated = True
        event_data.total_episode = target_episodes
        event_data.source = self.plugin_name
        event_data.reason = (
            f"TMDB 未录入第 {season} 季集数，自动兜底设为 {target_episodes} 集"
            + ("（匹配自定义规则）" if matched_episodes else "（默认配置）")
        )

        # 记录到历史列表
        record = {
            "title": title,
            "season": season,
            "episodes": target_episodes,
            "tmdb_id": str(tmdb_id) if tmdb_id else "",
            "time": _now_str(),
            "scene": event_data.scene or "create",
            "matched_rule": bool(matched_episodes),
        }
        self._history.append(record)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
        self._save_history()

        logger.info(
            f"[{self.plugin_name}] 已成功为《{title}》第 {season} 季注入兜底总集数: {target_episodes} 集"
            f" (触发场景: {event_data.scene})"
        )

        # 发送系统通知
        if self._notify:
            self.post_message(
                mtype=NotificationType.Plugin,
                title=f"📺 TMDB 待定集数自动兜底: {title}",
                text=(
                    f"剧集: {title}\n"
                    f"季号: 第 {season} 季\n"
                    f"设定总集数: {target_episodes} 集\n"
                    f"原因: TMDB 尚未录入该季分集信息，已自动填入兜底集数以保证订阅成功创建。\n"
                    f"💡 提示: 后续 TMDB 补充真实集数后，系统定时巡检会自动同步更新。"
                ),
            )

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
                                            "label": "启用 TMDB 待定集数兜底插件",
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
                                        "component": "VTextField",
                                        "props": {
                                            "model": "default_episodes",
                                            "label": "全局默认兜底总集数",
                                            "placeholder": "1",
                                            "type": "number",
                                            "hint": "当 TMDB 未录入集数且未匹配自定义规则时，默认填入的集数（推荐填 1）",
                                            "persistentHint": True,
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
                                            "model": "notify",
                                            "label": "触发兜底时发送系统通知",
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
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "custom_rules",
                                            "label": "特定剧集自定义集数规则 (可选，每行一条)",
                                            "placeholder": "凡人修仙传:52\n斗破苍穹:52\n海贼王:1100\n123456:24",
                                            "hint": "支持格式：'剧名关键词:集数' 或 'TMDB_ID:集数'，每行一条",
                                            "persistentHint": True,
                                            "rows": 4,
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
            "default_episodes": 1,
            "notify": True,
            "custom_rules": "",
        }
        return form_schema, default_config

    def get_page(self) -> Optional[List[dict]]:
        """拼装插件详情页面，展示运行概况与最近兜底记录。"""
        if not self._enabled:
            return [
                {
                    "component": "VAlert",
                    "props": {
                        "type": "info",
                        "text": "插件尚未启用，请在插件配置中开启以解除 TMDB 待定集数剧集无法订阅的限制。",
                    },
                }
            ]

        history_items = []
        for h in reversed(self._history[-10:]):
            title = h.get("title", "未知")
            season = h.get("season", 1)
            eps = h.get("episodes", 1)
            time_str = h.get("time", "")
            rule_tag = " [自定义规则]" if h.get("matched_rule") else " [默认值]"
            history_items.append(
                f"• 【第 {season} 季】{title} ➔ 注入 {eps} 集{rule_tag} ({time_str})"
            )

        history_text = (
            "\n".join(history_items)
            if history_items
            else "暂无兜底记录。当添加 TMDB 缺失集数的剧集时将自动记录在此。"
        )

        return [
            {
                "component": "VCard",
                "props": {"class": "mb-4", "color": "primary", "variant": "tonal"},
                "content": [
                    {
                        "component": "VCardTitle",
                        "text": "🛡️ TMDB 待定集数兜底已就绪",
                    },
                    {
                        "component": "VCardText",
                        "text": (
                            f"【运行状态】正常运行中 | 全局默认兜底集数: {self._default_episodes} 集 | "
                            f"自定义规则数: {len(self._custom_rules)} 条 | 累计兜底处理: {len(self._history)} 次\n\n"
                            "💡 机制说明：当您在 MoviePilot 中添加 TMDB 尚未录入分集的剧集时，本插件会自动将总集数推算为默认值（如 1 集）使订阅成功创建。\n"
                            "后续 TMDB 官方录入真实集数（如 12 或 24 集）后，系统定时巡检会自动升级覆盖为真实总集数。"
                        ),
                    },
                ],
            },
            {
                "component": "VCard",
                "props": {"class": "mb-4"},
                "content": [
                    {
                        "component": "VCardTitle",
                        "text": "📋 最近兜底记录",
                    },
                    {
                        "component": "VCardText",
                        "text": history_text,
                    },
                ],
            },
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        """注册插件 API 端点。"""
        return []

    def stop_service(self) -> None:
        """停止插件服务。"""
        pass
