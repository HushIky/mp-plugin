import json
import re
from typing import Any, Dict, List, Optional, Tuple

from app.core.event import Event, eventmanager
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType


class SiteSpeedLimit(_PluginBase):
    """根据站点自定义下载任务最大上传速度、分享率与做种时间的插件。"""

    # 插件元信息
    plugin_name = "站点限速与分享率控制"
    plugin_desc = "添加下载任务时，根据种子所属站点自动设置最大上传速度、最大分享率及做种时间。"
    plugin_icon = "sitespeedlimit.png"
    plugin_version = "1.0.1"
    plugin_label = "下载管理"
    plugin_author = "local"
    plugin_order = 100
    auth_level = 1

    # 内部运行状态
    _enabled: bool = False
    _rules_text: str = ""
    _site_rules: Dict[str, Dict[str, Any]] = {}
    _default_upload_limit: Optional[float] = None
    _default_ratio_limit: Optional[float] = None
    _default_seeding_time_limit: Optional[int] = None
    _modified_count: int = 0

    def init_plugin(self, config: dict = None) -> None:
        """
        根据用户配置初始化插件状态与规则映射。

        :param config: 插件配置字典
        """
        self.stop_service()
        self._enabled = False
        self._rules_text = ""
        self._site_rules = {}
        self._default_upload_limit = None
        self._default_ratio_limit = None
        self._default_seeding_time_limit = None

        if not config:
            return

        self._enabled = bool(config.get("enabled", False))
        self._rules_text = str(config.get("rules_text") or "").strip()

        # 解析全局默认值
        raw_default_upload = config.get("default_upload_limit")
        if raw_default_upload not in (None, ""):
            try:
                self._default_upload_limit = float(raw_default_upload)
            except (ValueError, TypeError):
                self._default_upload_limit = None

        raw_default_ratio = config.get("default_ratio_limit")
        if raw_default_ratio not in (None, ""):
            try:
                self._default_ratio_limit = float(raw_default_ratio)
            except (ValueError, TypeError):
                self._default_ratio_limit = None

        raw_default_seeding_time = config.get("default_seeding_time_limit")
        if raw_default_seeding_time not in (None, ""):
            try:
                self._default_seeding_time_limit = int(raw_default_seeding_time)
            except (ValueError, TypeError):
                self._default_seeding_time_limit = None

        # 解析多行站点规则
        self._site_rules = self._parse_rules(self._rules_text)
        logger.info(
            f"[{self.plugin_name}] 初始化完成：启用状态={self._enabled}，"
            f"加载站点规则数={len(self._site_rules)}"
        )

    def get_state(self) -> bool:
        """获取插件启用状态。"""
        return self._enabled

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
        """
        拼装插件配置页面表单（Vuetify JSON 结构）与默认配置字典。

        :return: (表单组件列表, 默认配置字典)
        """
        form_schema = [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "enabled",
                            "label": "启用插件",
                        },
                    },
                    {
                        "component": "VTextarea",
                        "props": {
                            "model": "rules_text",
                            "label": "站点规则配置 (每行一条)",
                            "placeholder": (
                                "# 格式：站点名称, 最大上传速度(KB/s), 最大分享率, 做种时长(分钟)\n"
                                "# 留空或填 0 表示不限制对应项，支持以 # 注释\n"
                                "HDSky, 5120, 2.0, 1440\n"
                                "OurBits, 10240, 3.0\n"
                                "M-Team, 0, 1.5, 2880"
                            ),
                            "rows": 8,
                            "hint": "每行格式为：站点名, 上传限速(KB/s), 分享率, 做种时长(分钟)",
                            "persistentHint": True,
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "default_upload_limit",
                            "label": "全局默认上传限速 (KB/s，留空不限制)",
                            "placeholder": "例如: 10240",
                            "type": "number",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "default_ratio_limit",
                            "label": "全局默认分享率限制 (留空不限制)",
                            "placeholder": "例如: 2.5",
                            "type": "number",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "default_seeding_time_limit",
                            "label": "全局默认做种时长限制 (分钟，留空不限制)",
                            "placeholder": "例如: 2880",
                            "type": "number",
                        },
                    },
                ],
            }
        ]
        default_config = {
            "enabled": False,
            "rules_text": (
                "# 格式：站点名称, 最大上传速度(KB/s), 最大分享率, 做种时长(分钟)\n"
                "# 留空或填 0 表示不限制对应项\n"
                "HDSky, 5120, 2.0, 1440\n"
                "OurBits, 10240, 3.0\n"
            ),
            "default_upload_limit": "",
            "default_ratio_limit": "",
            "default_seeding_time_limit": "",
        }
        return form_schema, default_config

    def get_page(self) -> Optional[List[dict]]:
        """
        拼装插件详情页面内容。

        :return: 详情页 Vuetify JSON 配置
        """
        if not self._enabled:
            return [
                {
                    "component": "VAlert",
                    "props": {
                        "type": "warning",
                        "text": "插件当前处于未启用状态，请在配置中启用并保存。",
                    },
                }
            ]

        rules_summary = []
        for site_key, rule in self._site_rules.items():
            site_display = rule.get("site_raw", site_key)
            up_lim = f"{rule['upload_limit']} KB/s" if rule.get("upload_limit") is not None else "不限"
            ratio_lim = f"{rule['ratio_limit']}" if rule.get("ratio_limit") is not None else "不限"
            seed_lim = f"{rule['seeding_time_limit']} 分钟" if rule.get("seeding_time_limit") is not None else "不限"
            rules_summary.append(f"• 站点【{site_display}】：上传限速 {up_lim} | 分享率 {ratio_lim} | 做种时间 {seed_lim}")

        summary_text = "\n".join(rules_summary) if rules_summary else "当前未配置任何特定站点规则。"

        return [
            {
                "component": "VCard",
                "props": {
                    "class": "mb-4",
                },
                "content": [
                    {
                        "component": "VCardTitle",
                        "text": "站点限速与分享率控制运行概况",
                    },
                    {
                        "component": "VCardText",
                        "text": (
                            f"插件运行正常。\n"
                            f"已解析生效站点规则数：{len(self._site_rules)} 条\n"
                            f"本次运行已调整任务数：{self._modified_count} 个\n\n"
                            f"已生效的站点规则：\n{summary_text}"
                        ),
                    },
                ],
            }
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        """注册插件 API 路由。"""
        return []

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """注册插件远程命令。"""
        return []

    @eventmanager.register(EventType.DownloadAdded)
    def on_download_added(self, event: Event) -> None:
        """
        监听下载添加事件，根据种子站点自动设置下载器的上传速度、分享率与做种时长。

        :param event: 下载添加事件对象
        """
        if not self._enabled:
            return

        event_data = event.event_data or {}
        download_hash = event_data.get("hash")
        downloader = event_data.get("downloader")
        context = event_data.get("context")

        if not download_hash:
            logger.debug(f"[{self.plugin_name}] 收到下载事件但缺少 download_hash，跳过处理")
            return

        # 提取站点名称
        site_name = self._extract_site_name(context=context, event_data=event_data)
        site_key = site_name.strip().lower() if site_name else ""

        # 查找匹配规则
        rule = self._site_rules.get(site_key)
        upload_limit: Optional[float] = None
        ratio_limit: Optional[float] = None
        seeding_time_limit: Optional[int] = None

        if rule:
            upload_limit = rule.get("upload_limit")
            ratio_limit = rule.get("ratio_limit")
            seeding_time_limit = rule.get("seeding_time_limit")
            match_source = f"站点规则【{rule.get('site_raw', site_name)}】"
        else:
            upload_limit = self._default_upload_limit
            ratio_limit = self._default_ratio_limit
            seeding_time_limit = self._default_seeding_time_limit
            match_source = "全局默认规则"

        if upload_limit is None and ratio_limit is None and seeding_time_limit is None:
            logger.debug(
                f"[{self.plugin_name}] 种子 {download_hash} (站点: {site_name or '未知'}) "
                "无匹配的限速或分享率规则，跳过处理"
            )
            return

        logger.info(
            f"[{self.plugin_name}] 匹配到 {match_source}，正在为任务 {download_hash} "
            f"(下载器: {downloader or '默认'}) 设置参数："
            f"上传限速={upload_limit if upload_limit is not None else '不修改'} KB/s, "
            f"分享率={ratio_limit if ratio_limit is not None else '不修改'}, "
            f"做种时长={seeding_time_limit if seeding_time_limit is not None else '不修改'} 分钟"
        )

        try:
            update_result = self.chain.update_torrent(
                hash_string=download_hash,
                downloader=downloader,
                upload_limit=upload_limit,
                ratio_limit=ratio_limit,
                seeding_time_limit=seeding_time_limit,
            )
            self._modified_count += 1
            logger.info(
                f"[{self.plugin_name}] 任务 {download_hash} 参数设置成功，返回结果: {update_result}"
            )
        except Exception as err:
            logger.error(
                f"[{self.plugin_name}] 设置任务 {download_hash} 限速与分享率失败: {err}"
            )

    def stop_service(self) -> None:
        """停止插件服务并清理资源。"""
        pass

    @staticmethod
    def _parse_rules(rules_text: str) -> Dict[str, Dict[str, Any]]:
        """
        解析多行或 JSON 格式的站点规则文本。

        :param rules_text: 用户输入的规则配置文本
        :return: 规范化的站点规则映射字典 {site_name_lower: rule_dict}
        """
        rules_map: Dict[str, Dict[str, Any]] = {}
        if not rules_text:
            return rules_map

        # 支持 JSON 数组格式
        text_stripped = rules_text.strip()
        if text_stripped.startswith("[") and text_stripped.endswith("]"):
            try:
                json_data = json.loads(text_stripped)
                if isinstance(json_data, list):
                    for item in json_data:
                        if isinstance(item, dict) and item.get("site"):
                            s_name = str(item["site"]).strip()
                            s_key = s_name.lower()
                            up_lim = item.get("upload_limit")
                            rat_lim = item.get("ratio_limit")
                            seed_lim = item.get("seeding_time_limit")
                            rules_map[s_key] = {
                                "site_raw": s_name,
                                "upload_limit": float(up_lim) if up_lim not in (None, "", 0) else None,
                                "ratio_limit": float(rat_lim) if rat_lim not in (None, "", 0) else None,
                                "seeding_time_limit": int(seed_lim) if seed_lim not in (None, "", 0) else None,
                            }
                    return rules_map
            except Exception:
                pass

        # 解析按行分隔的文本
        lines = rules_text.splitlines()
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            # 支持中英文逗号、分号或冒号切分
            parts = [p.strip() for p in re.split(r"[,，;；]", line) if p.strip()]
            if not parts:
                continue

            site_raw = parts[0]
            site_key = site_raw.lower()

            upload_limit = None
            ratio_limit = None
            seeding_time_limit = None

            if len(parts) >= 2:
                val = parts[1]
                if val and val != "0":
                    try:
                        upload_limit = float(val)
                    except ValueError:
                        upload_limit = None

            if len(parts) >= 3:
                val = parts[2]
                if val and val != "0":
                    try:
                        ratio_limit = float(val)
                    except ValueError:
                        ratio_limit = None

            if len(parts) >= 4:
                val = parts[3]
                if val and val != "0":
                    try:
                        seeding_time_limit = int(float(val))
                    except ValueError:
                        seeding_time_limit = None

            rules_map[site_key] = {
                "site_raw": site_raw,
                "upload_limit": upload_limit,
                "ratio_limit": ratio_limit,
                "seeding_time_limit": seeding_time_limit,
            }

        return rules_map

    @staticmethod
    def _extract_site_name(context: Any, event_data: Dict[str, Any]) -> str:
        """
        从事件或上下文对象中提取站点名称。

        :param context: 资源上下文
        :param event_data: 事件原始数据字典
        :return: 站点名称字符串
        """
        if context:
            torrent_info = getattr(context, "torrent_info", None)
            if torrent_info and hasattr(torrent_info, "site_name"):
                return str(torrent_info.site_name or "")
            if isinstance(context, dict):
                t_info = context.get("torrent_info")
                if isinstance(t_info, dict):
                    return str(t_info.get("site_name") or "")
                if hasattr(t_info, "site_name"):
                    return str(getattr(t_info, "site_name", "") or "")

        return str(event_data.get("torrent_site") or "")
