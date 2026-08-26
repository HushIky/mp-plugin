# MoviePilot 自定义插件仓库

本仓库包含适用于 MoviePilot 的自定义插件集合。

## 包含插件

### 1. 站点限速与分享率控制 (`SiteSpeedLimit`)
- **功能**：当 MoviePilot 添加下载任务时，根据种子所属站点自动设置最大上传速度（KB/s）、最大分享率（Ratio）以及做种时间限制（分钟）。
- **兼容性**：MoviePilot V2 / V3。
- **支持下载器**：qBittorrent、Transmission、rTorrent。

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
