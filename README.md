# MoviePilot 自定义插件仓库

本仓库包含适用于 MoviePilot 的自定义插件集合。

## 包含插件

### 1. 站点限速与分享率控制 (`SiteSpeedLimit`)
- **功能**：当 MoviePilot 添加下载任务时，根据种子所属站点自动设置最大上传速度（KB/s）、最大分享率（Ratio）以及做种时间限制（分钟）。
- **兼容性**：MoviePilot V2 / V3。
- **支持下载器**：qBittorrent、Transmission、rTorrent。

### 2. Spotify 音乐下载与订阅 (`SpotifyMusic`)
- **功能**：
  - **无需 API Key 解析**：支持解析 Spotify 单曲、专辑、歌单与艺术家链接。
  - **增量订阅管理**：支持定期自动巡检歌单/艺术家更新，支持“全量同步”与“仅监控新增（忽略存量）”双模式。
  - **音源匹配与下载**：基于 yt-dlp 和 YouTube Music 精准匹配音源并抽取 MP3 (320k) / FLAC 等音频。
  - **元数据与歌词内嵌**：使用 mutagen 自动注入 ID3 标签、高清专辑封面，并通过 LRCLIB 抓取并内嵌歌词（同步生成 `.lrc` 文件与 `.m3u8` 歌单）。
  - **目录自动整理**：根据自定义路径模板（如 `{artist}/{album} ({year})/{track_number:02d} - {title}.{ext}`）自动归档到媒体库。
- **兼容性**：MoviePilot V2 / V3。

## 使用方式

### 作为第三方插件市场使用
在 MoviePilot 的环境变量或配置中设置：
```bash
PLUGIN_MARKET="https://github.com/HushIky/mp-plugin"
```
或者多个市场仓库逗号分隔：
```bash
PLUGIN_MARKET="https://github.com/jxxghp/MoviePilot-Plugins,https://github.com/HushIky/mp-plugin"
```
进入 MoviePilot Web 界面 -> **插件市场**，即可搜索并一键安装。
