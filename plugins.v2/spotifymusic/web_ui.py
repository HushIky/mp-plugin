"""SpotifyMusic 独立交互式 Web 工作台页面生成器。

为用户提供独立的音乐搜索、Spotify 链接解析与增量订阅、实时下载队列监控与订阅管理 SPA。
"""

from __future__ import annotations


def render_music_workbench_html(
    plugin_name: str = "Spotify 音乐工作台",
    api_prefix: str = "/api/v1/plugin/SpotifyMusic",
    default_token: str = "",
) -> str:
    """生成内嵌 Vue 3 与 TailwindCSS 的单页交互界面 HTML。"""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{plugin_name}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://unpkg.com/vue@3/dist/vue.global.prod.js"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    body {{
      background-color: #0f172a;
      color: #f8fafc;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    }}
    .glass {{
      background: rgba(30, 41, 59, 0.7);
      backdrop-filter: blur(12px);
      border: 1px solid rgba(255, 255, 255, 0.08);
    }}
    .glass-card {{
      background: rgba(30, 41, 59, 0.5);
      backdrop-filter: blur(8px);
      border: 1px solid rgba(255, 255, 255, 0.05);
      transition: all 0.2s ease-in-out;
    }}
    .glass-card:hover {{
      transform: translateY(-2px);
      border-color: rgba(34, 197, 94, 0.4);
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3);
    }}
    .spotify-green {{
      background-color: #1ed760;
    }}
    .spotify-green:hover {{
      background-color: #1fdf64;
    }}
  </style>
</head>
<body class="min-h-screen">
  <div id="app" class="max-w-7xl mx-auto px-4 py-6">
    <!-- 顶部导航栏 -->
    <header class="glass rounded-2xl p-4 mb-6 flex flex-col md:flex-row md:items-center justify-between gap-4">
      <div class="flex items-center gap-3">
        <div class="w-10 h-10 rounded-xl spotify-green flex items-center justify-center text-slate-900 text-xl font-bold">
          <i class="fa-brands fa-spotify"></i>
        </div>
        <div>
          <h1 class="text-xl font-bold tracking-tight text-white flex items-center gap-2">
            Spotify 音乐搜索与订阅工作台
            <span class="text-xs font-normal px-2.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">v1.1.11</span>
          </h1>
          <p class="text-xs text-slate-400">高品质音频下载 • 元数据/歌词/封面打标 • 增量订阅管理</p>
        </div>
      </div>

      <!-- 选项卡切换 与 Token 设置 -->
      <div class="flex items-center gap-3">
        <div class="flex items-center gap-1.5 bg-slate-800/80 p-1.5 rounded-xl border border-slate-700/50">
          <button 
            v-for="tab in tabs" 
            :key="tab.id"
            @click="activeTab = tab.id"
            :class="activeTab === tab.id ? 'bg-emerald-600 text-white shadow-lg' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700/50'"
            class="px-4 py-2 rounded-lg text-sm font-medium transition flex items-center gap-2"
          >
            <i :class="tab.icon"></i>
            {{{{ tab.name }}}}
            <span v-if="tab.id === 'tasks' && activeTaskCount > 0" class="px-1.5 py-0.5 text-xs rounded-full bg-emerald-400 text-slate-900 font-bold">
              {{{{ activeTaskCount }}}}
            </span>
          </button>
        </div>

        <!-- 复制 API-Key 按钮 -->
        <button 
          v-if="token"
          @click="copyToken"
          title="复制 API-Key 凭证到剪贴板"
          class="px-3 py-2 rounded-xl bg-slate-800/80 hover:bg-slate-700 text-slate-300 hover:text-emerald-400 border border-slate-700/50 transition flex items-center gap-1.5 text-xs font-medium"
        >
          <i class="fa-regular fa-copy"></i>
          <span class="hidden sm:inline">复制 API-Key</span>
        </button>

        <button 
          @click="showTokenModal = true"
          title="配置 API Token 凭证"
          class="p-2.5 rounded-xl bg-slate-800/80 hover:bg-slate-700 text-slate-400 hover:text-emerald-400 border border-slate-700/50 transition"
        >
          <i class="fa-solid fa-key"></i>
        </button>
      </div>
    </header>

    <!-- Token 配置模态框 -->
    <div v-if="showTokenModal" class="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
      <div class="glass rounded-2xl p-6 max-w-md w-full space-y-4 border border-slate-700 shadow-2xl">
        <div class="flex items-center justify-between">
          <h3 class="text-base font-bold text-white flex items-center gap-2">
            <i class="fa-solid fa-key text-emerald-400"></i>
            配置 MoviePilot API Token
          </h3>
          <button v-if="token" @click="showTokenModal = false" class="text-slate-400 hover:text-white">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>
        <p class="text-xs text-slate-400 leading-relaxed">
          请输入 MoviePilot 的 API Token 进行鉴权。可在 MoviePilot Web 界面<b>【设置 -> 基础设置 -> API Token】</b>中查看。凭证将安全保存在您的本地浏览器中。
        </p>

        <!-- 已绑定 Token 状态与复制提示 -->
        <div v-if="token" class="p-3 bg-emerald-950/40 border border-emerald-800/50 rounded-xl flex items-center justify-between text-xs">
          <div class="flex items-center gap-2 text-emerald-300 truncate">
            <i class="fa-solid fa-circle-check"></i>
            <span>已同步 MoviePilot 鉴权凭证</span>
          </div>
          <button 
            @click="copyToken" 
            class="px-2.5 py-1 rounded bg-emerald-700 hover:bg-emerald-600 text-white font-medium flex items-center gap-1 transition text-[11px] flex-shrink-0"
          >
            <i class="fa-regular fa-copy"></i> 复制凭证
          </button>
        </div>

        <div>
          <input 
            v-model="tokenInput" 
            type="password" 
            placeholder="输入 API Token..." 
            class="w-full px-4 py-3 bg-slate-800/90 border border-slate-700 rounded-xl text-white placeholder-slate-500 focus:outline-none focus:border-emerald-500 text-sm"
          >
        </div>
        <div class="flex justify-end gap-2 pt-2">
          <button 
            @click="saveToken" 
            :disabled="!tokenInput.trim()"
            class="px-5 py-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white text-sm font-medium transition"
          >
            保存并连接
          </button>
        </div>
      </div>
    </div>

    <!-- 消息提示 Toast -->
    <div v-if="toast.show" class="fixed bottom-6 right-6 z-50 flex items-center gap-3 px-5 py-3.5 rounded-xl shadow-2xl text-sm font-medium transition-all"
         :class="toast.type === 'error' ? 'bg-rose-900/90 text-rose-200 border border-rose-700' : 'bg-emerald-900/90 text-emerald-200 border border-emerald-700'">
      <i :class="toast.type === 'error' ? 'fa-solid fa-circle-exclamation' : 'fa-solid fa-circle-check'"></i>
      <span>{{{{ toast.message }}}}</span>
    </div>

    <!-- TAB 1: 音乐搜索与一键下载 -->
    <main v-if="activeTab === 'search'" class="space-y-6">
      <!-- 搜索栏 -->
      <div class="glass rounded-2xl p-6">
        <form @submit.prevent="handleSearch" class="flex flex-col md:flex-row gap-3">
          <div class="relative flex-1">
            <i class="fa-solid fa-magnifying-glass absolute left-4 top-1/2 -translate-y-1/2 text-slate-400"></i>
            <input 
              v-model="searchQuery" 
              type="text" 
              placeholder="输入歌手、歌名（例如：周杰伦 晴天 或 Taylor Swift Cruel Summer）..." 
              class="w-full pl-11 pr-4 py-3.5 bg-slate-800/90 border border-slate-700 rounded-xl text-white placeholder-slate-500 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition"
              :disabled="searching"
            >
          </div>
          <button 
            type="submit" 
            :disabled="searching || !searchQuery.trim()"
            class="px-8 py-3.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-medium transition flex items-center justify-center gap-2 shadow-lg shadow-emerald-900/20"
          >
            <i v-if="searching" class="fa-solid fa-spinner fa-spin"></i>
            <i v-else class="fa-solid fa-search"></i>
            <span>{{{{ searching ? '搜索中...' : '搜索歌曲' }}}}</span>
          </button>
        </form>
      </div>

      <!-- 分类筛选导航栏 (分栏展示) -->
      <div v-if="totalResultsCount > 0" class="flex flex-wrap items-center gap-2">
        <button 
          @click="searchCategory = 'all'"
          class="px-4 py-2 rounded-xl text-xs font-medium transition flex items-center gap-1.5"
          :class="searchCategory === 'all' ? 'bg-emerald-600 text-white shadow-lg shadow-emerald-900/30' : 'glass hover:bg-slate-700/50 text-slate-300'"
        >
          <i class="fa-solid fa-border-all"></i>
          <span>全部 ({{{{ totalResultsCount }}}})</span>
        </button>
        <button 
          v-if="searchResults.tracks?.length > 0"
          @click="searchCategory = 'tracks'"
          class="px-4 py-2 rounded-xl text-xs font-medium transition flex items-center gap-1.5"
          :class="searchCategory === 'tracks' ? 'bg-emerald-600 text-white shadow-lg shadow-emerald-900/30' : 'glass hover:bg-slate-700/50 text-slate-300'"
        >
          <i class="fa-solid fa-music"></i>
          <span>单曲 ({{{{ searchResults.tracks.length }}}})</span>
        </button>
        <button 
          v-if="searchResults.albums?.length > 0"
          @click="searchCategory = 'albums'"
          class="px-4 py-2 rounded-xl text-xs font-medium transition flex items-center gap-1.5"
          :class="searchCategory === 'albums' ? 'bg-emerald-600 text-white shadow-lg shadow-emerald-900/30' : 'glass hover:bg-slate-700/50 text-slate-300'"
        >
          <i class="fa-solid fa-compact-disc"></i>
          <span>专辑 ({{{{ searchResults.albums.length }}}})</span>
        </button>
        <button 
          v-if="searchResults.artists?.length > 0"
          @click="searchCategory = 'artists'"
          class="px-4 py-2 rounded-xl text-xs font-medium transition flex items-center gap-1.5"
          :class="searchCategory === 'artists' ? 'bg-emerald-600 text-white shadow-lg shadow-emerald-900/30' : 'glass hover:bg-slate-700/50 text-slate-300'"
        >
          <i class="fa-solid fa-user-pen"></i>
          <span>艺术家 ({{{{ searchResults.artists.length }}}})</span>
        </button>
        <button 
          v-if="searchResults.playlists?.length > 0"
          @click="searchCategory = 'playlists'"
          class="px-4 py-2 rounded-xl text-xs font-medium transition flex items-center gap-1.5"
          :class="searchCategory === 'playlists' ? 'bg-emerald-600 text-white shadow-lg shadow-emerald-900/30' : 'glass hover:bg-slate-700/50 text-slate-300'"
        >
          <i class="fa-solid fa-list-music"></i>
          <span>歌单 ({{{{ searchResults.playlists.length }}}})</span>
        </button>
      </div>

      <!-- 搜索结果区 -->
      <div v-if="totalResultsCount > 0" class="space-y-8">
        <!-- 1. 艺术家 分栏 -->
        <div v-if="(searchCategory === 'all' || searchCategory === 'artists') && searchResults.artists?.length > 0" class="space-y-3">
          <div class="flex items-center justify-between">
            <h2 class="text-base font-semibold text-white flex items-center gap-2">
              <i class="fa-solid fa-user-pen text-emerald-400"></i>
              <span>艺术家 ({{{{ searchResults.artists.length }}}})</span>
            </h2>
            <span class="text-xs text-slate-400">支持一键增量订阅，自动监控歌手最新发行的唱片与单曲</span>
          </div>
          <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <div 
              v-for="item in searchResults.artists" 
              :key="item.id || item.url"
              class="glass-card rounded-2xl p-4 flex flex-col justify-between"
            >
              <div class="flex items-center gap-4">
                <div class="w-16 h-16 rounded-full overflow-hidden bg-slate-800 flex-shrink-0 relative border-2 border-emerald-500/20">
                  <img v-if="item.avatar_url" :src="item.avatar_url" :alt="item.name" class="w-full h-full object-cover">
                  <div v-else class="w-full h-full flex items-center justify-center text-slate-600 text-xl">
                    <i class="fa-solid fa-user"></i>
                  </div>
                </div>
                <div class="flex-1 min-w-0">
                  <div class="flex items-center gap-1.5">
                    <h3 class="font-bold text-white text-base truncate" :title="item.name">{{{{ item.name }}}}</h3>
                    <i v-if="item.verified" class="fa-solid fa-circle-check text-blue-400 text-xs flex-shrink-0" title="认证艺术家"></i>
                  </div>
                  <div class="mt-2 flex items-center gap-2">
                    <span 
                      class="text-[10px] px-2 py-0.5 rounded-full border flex items-center gap-1"
                      :class="item.source === 'spotify' ? 'bg-emerald-950/60 text-emerald-400 border-emerald-800/80' : (item.source === 'itunes' ? 'bg-rose-950/60 text-rose-300 border-rose-800/80' : 'bg-slate-800 text-slate-400 border-slate-700')"
                    >
                      <i v-if="item.source === 'spotify'" class="fa-brands fa-spotify text-emerald-400"></i>
                      <i v-else-if="item.source === 'itunes'" class="fa-brands fa-apple text-rose-400"></i>
                      <i v-else-if="item.source === 'ytmusic'" class="fa-brands fa-youtube text-red-400"></i>
                      <span>{{{{ item.source === 'spotify' ? 'Spotify' : (item.source === 'itunes' ? 'Apple Music' : 'YouTube Music') }}}}</span>
                    </span>
                    <a v-if="item.url" :href="item.url" target="_blank" class="text-xs text-slate-500 hover:text-emerald-400 transition" title="在网页中打开">
                      <i class="fa-solid fa-arrow-up-right-from-square"></i>
                    </a>
                  </div>
                </div>
              </div>
              <div class="mt-4 pt-3 border-t border-slate-700/50 flex items-center justify-end gap-2">
                <button 
                  @click="subscribeEntity(item, 'only_new')"
                  :disabled="subscribingMap[item.id || item.url]"
                  class="px-3 py-1.5 rounded-lg text-xs font-medium transition flex items-center gap-1 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white shadow-sm"
                >
                  <i v-if="subscribingMap[item.id || item.url]" class="fa-solid fa-spinner fa-spin"></i>
                  <i v-else class="fa-solid fa-rss"></i>
                  <span>仅监控新增</span>
                </button>
                <button 
                  @click="subscribeEntity(item, 'full')"
                  :disabled="subscribingMap[item.id || item.url]"
                  class="px-3 py-1.5 rounded-lg text-xs font-medium transition flex items-center gap-1 bg-slate-700 hover:bg-slate-600 disabled:opacity-50 text-slate-200"
                >
                  <i v-if="subscribingMap[item.id || item.url]" class="fa-solid fa-spinner fa-spin"></i>
                  <i v-else class="fa-solid fa-box-archive"></i>
                  <span>全量同步</span>
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- 2. 专辑 分栏 -->
        <div v-if="(searchCategory === 'all' || searchCategory === 'albums') && searchResults.albums?.length > 0" class="space-y-3">
          <div class="flex items-center justify-between">
            <h2 class="text-base font-semibold text-white flex items-center gap-2">
              <i class="fa-solid fa-compact-disc text-emerald-400"></i>
              <span>专辑 ({{{{ searchResults.albums.length }}}})</span>
            </h2>
            <span class="text-xs text-slate-400">支持一键订阅/批量下载整张专辑</span>
          </div>
          <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <div 
              v-for="item in searchResults.albums" 
              :key="item.id || item.url"
              class="glass-card rounded-2xl p-4 flex flex-col justify-between"
            >
              <div class="flex gap-4">
                <div class="w-20 h-20 rounded-xl overflow-hidden bg-slate-800 flex-shrink-0 relative">
                  <img v-if="item.cover_url" :src="item.cover_url" :alt="item.title" class="w-full h-full object-cover">
                  <div v-else class="w-full h-full flex items-center justify-center text-slate-600 text-2xl">
                    <i class="fa-solid fa-compact-disc"></i>
                  </div>
                </div>
                <div class="flex-1 min-w-0">
                  <h3 class="font-bold text-white text-base truncate" :title="item.title">{{{{ item.title }}}}</h3>
                  <p class="text-xs text-emerald-400 truncate mt-0.5" :title="item.artist">
                    <i class="fa-solid fa-user-pen mr-1 opacity-70"></i>{{{{ item.artist }}}}
                  </p>
                  <p v-if="item.year" class="text-xs text-slate-400 mt-0.5">
                    <i class="fa-regular fa-calendar mr-1 opacity-70"></i>{{{{ item.year }}}} 年发行
                  </p>
                  <div class="mt-2 flex items-center gap-2">
                    <span 
                      class="text-[10px] px-2 py-0.5 rounded-full border flex items-center gap-1"
                      :class="item.source === 'spotify' ? 'bg-emerald-950/60 text-emerald-400 border-emerald-800/80' : (item.source === 'itunes' ? 'bg-rose-950/60 text-rose-300 border-rose-800/80' : 'bg-slate-800 text-slate-400 border-slate-700')"
                    >
                      <i v-if="item.source === 'spotify'" class="fa-brands fa-spotify text-emerald-400"></i>
                      <i v-else-if="item.source === 'itunes'" class="fa-brands fa-apple text-rose-400"></i>
                      <i v-else-if="item.source === 'ytmusic'" class="fa-brands fa-youtube text-red-400"></i>
                      <span>{{{{ item.source === 'spotify' ? 'Spotify' : (item.source === 'itunes' ? 'Apple Music' : 'YouTube Music') }}}}</span>
                    </span>
                    <a v-if="item.url" :href="item.url" target="_blank" class="text-xs text-slate-500 hover:text-emerald-400 transition" title="在网页中打开">
                      <i class="fa-solid fa-arrow-up-right-from-square"></i>
                    </a>
                  </div>
                </div>
              </div>
              <div class="mt-4 pt-3 border-t border-slate-700/50 flex items-center justify-end">
                <button 
                  @click="subscribeEntity(item, 'full')"
                  :disabled="subscribingMap[item.id || item.url]"
                  class="px-4 py-1.5 rounded-lg text-xs font-medium transition flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white"
                >
                  <i v-if="subscribingMap[item.id || item.url]" class="fa-solid fa-spinner fa-spin"></i>
                  <i v-else class="fa-solid fa-cloud-arrow-down"></i>
                  <span>一键下载专辑</span>
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- 3. 单曲 分栏 -->
        <div v-if="(searchCategory === 'all' || searchCategory === 'tracks') && searchResults.tracks?.length > 0" class="space-y-3">
          <div class="flex items-center justify-between">
            <h2 class="text-base font-semibold text-white flex items-center gap-2">
              <i class="fa-solid fa-music text-emerald-400"></i>
              <span>单曲 ({{{{ searchResults.tracks.length }}}})</span>
            </h2>
            <span class="text-xs text-slate-400">点击“一键下载”将自动匹配最佳音源、内嵌元数据与歌词并归档</span>
          </div>
          <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <div 
              v-for="(item, idx) in searchResults.tracks" 
              :key="item.id || idx"
              class="glass-card rounded-2xl p-4 flex flex-col justify-between"
            >
              <div class="flex gap-4">
                <div class="w-20 h-20 rounded-xl overflow-hidden bg-slate-800 flex-shrink-0 relative">
                  <img v-if="item.cover_url" :src="item.cover_url" :alt="item.title" class="w-full h-full object-cover">
                  <div v-else class="w-full h-full flex items-center justify-center text-slate-600 text-2xl">
                    <i class="fa-solid fa-music"></i>
                  </div>
                  <span v-if="item.duration_str" class="absolute bottom-1 right-1 px-1.5 py-0.5 rounded bg-black/70 text-[10px] text-white">
                    {{{{ item.duration_str }}}}
                  </span>
                </div>
                <div class="flex-1 min-w-0">
                  <h3 class="font-bold text-white text-base truncate" :title="item.title">{{{{ item.title }}}}</h3>
                  <p class="text-xs text-emerald-400 truncate mt-0.5" :title="item.artist">
                    <i class="fa-solid fa-user-pen mr-1 opacity-70"></i>{{{{ item.artist }}}}
                  </p>
                  <p v-if="item.album" class="text-xs text-slate-400 truncate mt-0.5" :title="item.album">
                    <i class="fa-solid fa-compact-disc mr-1 opacity-70"></i>{{{{ item.album }}}}
                  </p>
                  <div class="mt-2 flex items-center gap-2">
                    <span 
                      class="text-[10px] px-2 py-0.5 rounded-full border flex items-center gap-1"
                      :class="item.source === 'spotify' ? 'bg-emerald-950/60 text-emerald-400 border-emerald-800/80' : (item.source === 'itunes' ? 'bg-rose-950/60 text-rose-300 border-rose-800/80' : 'bg-slate-800 text-slate-400 border-slate-700')"
                    >
                      <i v-if="item.source === 'spotify'" class="fa-brands fa-spotify text-emerald-400"></i>
                      <i v-else-if="item.source === 'itunes'" class="fa-brands fa-apple text-rose-400"></i>
                      <i v-else-if="item.source === 'ytmusic'" class="fa-brands fa-youtube text-red-400"></i>
                      <i v-else class="fa-brands fa-youtube text-slate-400"></i>
                      <span>{{{{ item.source === 'spotify' ? 'Spotify' : (item.source === 'itunes' ? 'Apple Music' : (item.source === 'ytmusic' ? 'YouTube Music' : 'YouTube')) }}}}</span>
                    </span>
                    <a v-if="item.url" :href="item.url" target="_blank" class="text-xs text-slate-500 hover:text-emerald-400 transition" title="在网页中打开">
                      <i class="fa-solid fa-arrow-up-right-from-square"></i>
                    </a>
                  </div>
                </div>
              </div>
              <div class="mt-4 pt-3 border-t border-slate-700/50 flex items-center justify-end">
                <button 
                  @click="downloadTrack(item)"
                  :disabled="downloadingMap[item.id]"
                  class="px-4 py-1.5 rounded-lg text-xs font-medium transition flex items-center gap-1.5"
                  :class="downloadedMap[item.id] ? 'bg-emerald-950/60 text-emerald-400 border border-emerald-800' : 'bg-emerald-600 hover:bg-emerald-500 text-white'"
                >
                  <i v-if="downloadingMap[item.id]" class="fa-solid fa-spinner fa-spin"></i>
                  <i v-else-if="downloadedMap[item.id]" class="fa-solid fa-check"></i>
                  <i v-else class="fa-solid fa-download"></i>
                  <span>{{{{ downloadingMap[item.id] ? '提交中...' : (downloadedMap[item.id] ? '已加入队列' : '一键下载') }}}}</span>
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- 4. 歌单 分栏 -->
        <div v-if="(searchCategory === 'all' || searchCategory === 'playlists') && searchResults.playlists?.length > 0" class="space-y-3">
          <div class="flex items-center justify-between">
            <h2 class="text-base font-semibold text-white flex items-center gap-2">
              <i class="fa-solid fa-list-music text-emerald-400"></i>
              <span>歌单 ({{{{ searchResults.playlists.length }}}})</span>
            </h2>
            <span class="text-xs text-slate-400">支持一键订阅歌单，自动同步最新曲目</span>
          </div>
          <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <div 
              v-for="item in searchResults.playlists" 
              :key="item.id || item.url"
              class="glass-card rounded-2xl p-4 flex flex-col justify-between"
            >
              <div class="flex gap-4">
                <div class="w-20 h-20 rounded-xl overflow-hidden bg-slate-800 flex-shrink-0 relative">
                  <img v-if="item.cover_url" :src="item.cover_url" :alt="item.name" class="w-full h-full object-cover">
                  <div v-else class="w-full h-full flex items-center justify-center text-slate-600 text-2xl">
                    <i class="fa-solid fa-list-music"></i>
                  </div>
                </div>
                <div class="flex-1 min-w-0">
                  <h3 class="font-bold text-white text-base truncate" :title="item.name">{{{{ item.name }}}}</h3>
                  <p v-if="item.owner" class="text-xs text-slate-400 truncate mt-0.5" :title="item.owner">
                    <i class="fa-solid fa-user-circle mr-1 opacity-70"></i>创建者: {{{{ item.owner }}}}
                  </p>
                  <div class="mt-2 flex items-center gap-2">
                    <span 
                      class="text-[10px] px-2 py-0.5 rounded-full border flex items-center gap-1"
                      :class="item.source === 'spotify' ? 'bg-emerald-950/60 text-emerald-400 border-emerald-800/80' : 'bg-slate-800 text-slate-400 border-slate-700'"
                    >
                      <i v-if="item.source === 'spotify'" class="fa-brands fa-spotify text-emerald-400"></i>
                      <span>{{{{ item.source === 'spotify' ? 'Spotify' : 'YouTube Music' }}}}</span>
                    </span>
                    <a v-if="item.url" :href="item.url" target="_blank" class="text-xs text-slate-500 hover:text-emerald-400 transition" title="在网页中打开">
                      <i class="fa-solid fa-arrow-up-right-from-square"></i>
                    </a>
                  </div>
                </div>
              </div>
              <div class="mt-4 pt-3 border-t border-slate-700/50 flex items-center justify-end">
                <button 
                  @click="subscribeEntity(item, 'full')"
                  :disabled="subscribingMap[item.id || item.url]"
                  class="px-4 py-1.5 rounded-lg text-xs font-medium transition flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white"
                >
                  <i v-if="subscribingMap[item.id || item.url]" class="fa-solid fa-spinner fa-spin"></i>
                  <i v-else class="fa-solid fa-rss"></i>
                  <span>一键订阅歌单</span>
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 空状态 -->
      <div v-else-if="searched && !searching" class="glass rounded-2xl p-12 text-center">
        <i class="fa-solid fa-music-slash text-4xl text-slate-600 mb-3"></i>
        <p class="text-slate-400 text-sm">未找到与 "{{{{ searchQuery }}}}" 相关的歌曲、专辑或艺术家，请尝试更换关键词。</p>
      </div>
    </main>

    <!-- TAB 2: Spotify 链接解析与订阅 -->
    <main v-if="activeTab === 'spotify'" class="space-y-6">
      <div class="glass rounded-2xl p-6">
        <h2 class="text-base font-bold text-white mb-2 flex items-center gap-2">
          <i class="fa-brands fa-spotify text-emerald-400"></i>
          Spotify 链接解析与增量订阅
        </h2>
        <p class="text-xs text-slate-400 mb-4">
          支持 Spotify 单曲、专辑、歌单或艺术家链接。特别支持<b>【仅监控新增】</b>模式：首次添加时自动建立存量基准，后续定时巡检仅自动下载新加入的歌曲。
        </p>

        <form @submit.prevent="handleResolveSpotify" class="flex flex-col md:flex-row gap-3">
          <input 
            v-model="spotifyUrl" 
            type="text" 
            placeholder="粘贴 Spotify 链接，例如: https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M" 
            class="flex-1 px-4 py-3.5 bg-slate-800/90 border border-slate-700 rounded-xl text-white placeholder-slate-500 focus:outline-none focus:border-emerald-500 transition text-sm"
            :disabled="resolvingSpotify"
          >
          <button 
            type="submit" 
            :disabled="resolvingSpotify || !spotifyUrl.trim()"
            class="px-8 py-3.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-medium transition flex items-center justify-center gap-2 shadow-lg"
          >
            <i v-if="resolvingSpotify" class="fa-solid fa-spinner fa-spin"></i>
            <i v-else class="fa-solid fa-bolt"></i>
            <span>{{{{ resolvingSpotify ? '解析中...' : '解析链接' }}}}</span>
          </button>
        </form>
      </div>

      <!-- 解析结果展示卡片 -->
      <div v-if="resolvedEntity" class="glass rounded-2xl p-6 space-y-6">
        <div class="flex flex-col md:flex-row gap-6 items-start">
          <div class="w-32 h-32 rounded-2xl overflow-hidden bg-slate-800 flex-shrink-0 shadow-xl">
            <img v-if="resolvedEntity.cover_url" :src="resolvedEntity.cover_url" class="w-full h-full object-cover">
            <div v-else class="w-full h-full flex items-center justify-center text-4xl text-slate-600">
              <i class="fa-brands fa-spotify"></i>
            </div>
          </div>
          <div class="flex-1 space-y-2">
            <div class="flex items-center gap-2">
              <span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                {{{{ (resolvedEntity.type || '').toUpperCase() }}}}
              </span>
              <span v-if="resolvedEntity.type === 'artist' && resolvedEntity.total_releases" class="text-xs text-slate-400">
                共 {{{{ resolvedEntity.total_releases }}}} 张唱片 · {{{{ (resolvedEntity.tracks || []).length }}}} 首曲目
              </span>
              <span v-else class="text-xs text-slate-400">
                共 {{{{ (resolvedEntity.tracks || []).length }}}} 首曲目
              </span>
            </div>
            <h3 class="text-2xl font-bold text-white">{{{{ resolvedEntity.name }}}}</h3>
            <p class="text-xs text-slate-400">Spotify ID: {{{{ resolvedEntity.spotify_id }}}}</p>

            <div v-if="resolvedEntity.type === 'artist' && resolvedEntity.total_releases > 0" class="p-3.5 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-xs text-emerald-300 flex items-start gap-2 mt-2">
              <i class="fa-solid fa-circle-check mt-0.5 text-emerald-400"></i>
              <span>已通过 Spotify GraphQL 接口免凭证全量解析出该艺术家的全部唱片库 ({{{{ resolvedEntity.total_releases }}}} 张唱片 / {{{{ (resolvedEntity.tracks || []).length }}}} 首曲目)。</span>
            </div>

            <!-- 操作选项按钮群 -->
            <div class="pt-4 flex flex-wrap gap-3">
              <button 
                @click="submitSubscription('only_new')"
                :disabled="submittingSub"
                class="px-5 py-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white text-sm font-medium transition flex items-center gap-2 shadow-lg"
              >
                <i v-if="submittingSub" class="fa-solid fa-spinner fa-spin"></i>
                <i v-else class="fa-solid fa-seedling"></i>
                <span>🌿 仅监控新增订阅 (推荐)</span>
              </button>
              <button 
                @click="submitSubscription('all')"
                :disabled="submittingSub"
                class="px-5 py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm font-medium transition flex items-center gap-2 shadow-lg"
              >
                <i v-if="submittingSub" class="fa-solid fa-spinner fa-spin"></i>
                <i v-else class="fa-solid fa-box-archive"></i>
                <span>📦 全量订阅 (立即全下+持续监控)</span>
              </button>
              <button 
                @click="submitSubscription('once')"
                :disabled="submittingSub"
                class="px-5 py-2.5 rounded-xl bg-slate-700 hover:bg-slate-600 disabled:opacity-50 text-slate-200 text-sm font-medium transition flex items-center gap-2"
              >
                <i v-if="submittingSub" class="fa-solid fa-spinner fa-spin"></i>
                <i v-else class="fa-solid fa-download"></i>
                <span>⚡ 单次批量下载 (不建长期订阅)</span>
              </button>
            </div>
          </div>
        </div>

        <!-- 曲目清单预览 (按专辑分组展示全量曲目与专辑封面) -->
        <div class="border-t border-slate-700/50 pt-5 space-y-4">
          <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <h4 class="text-sm font-semibold text-slate-200 flex items-center gap-2">
                <i class="fa-solid fa-compact-disc text-emerald-400"></i>
                曲目列表预览 (共 {{{{ albumGroups.length }}}} 张专辑 / {{{{ (resolvedEntity.tracks || []).length }}}} 首曲目)
              </h4>
              <p class="text-xs text-slate-400 mt-0.5">全量曲目已按专辑分类整理并展示专辑封面，支持展开/折叠与实时筛选</p>
            </div>
            
            <div class="flex items-center gap-2">
              <!-- 快捷筛选搜索框 -->
              <div class="relative w-44 sm:w-56">
                <i class="fa-solid fa-search absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 text-xs"></i>
                <input 
                  v-model="albumSearchQuery"
                  type="text"
                  placeholder="筛选专辑或曲目..."
                  class="w-full pl-8 pr-3 py-1.5 bg-slate-800/80 border border-slate-700/80 rounded-lg text-xs text-white placeholder-slate-500 focus:outline-none focus:border-emerald-500"
                >
              </div>

              <!-- 展开 / 折叠全部按钮 -->
              <button 
                @click="expandAllAlbums"
                class="px-2.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs rounded-lg border border-slate-700 transition flex items-center gap-1"
                title="展开所有专辑"
              >
                <i class="fa-solid fa-angles-down text-[10px]"></i>
                <span class="hidden sm:inline">全部展开</span>
              </button>
              <button 
                @click="collapseAllAlbums"
                class="px-2.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs rounded-lg border border-slate-700 transition flex items-center gap-1"
                title="折叠所有专辑"
              >
                <i class="fa-solid fa-angles-up text-[10px]"></i>
                <span class="hidden sm:inline">全部折叠</span>
              </button>
            </div>
          </div>

          <!-- 专辑卡片分组滚动列表 -->
          <div class="max-h-[640px] overflow-y-auto pr-2 space-y-3">
            <div 
              v-for="album in albumGroups"
              :key="album.name"
              class="glass-card rounded-xl border border-slate-700/60 overflow-hidden"
            >
              <!-- 专辑卡片头部 (封面 + 专辑名 + 发行日期 + 曲目数 + 折叠按钮) -->
              <div 
                @click="toggleAlbum(album.name)"
                class="p-3.5 bg-slate-800/60 hover:bg-slate-800/90 cursor-pointer flex items-center justify-between gap-3 select-none transition"
              >
                <div class="flex items-center gap-3.5 min-w-0">
                  <!-- 专辑封面 -->
                  <div class="w-12 h-12 rounded-lg bg-slate-900 overflow-hidden flex-shrink-0 relative shadow border border-slate-700/50">
                    <img v-if="album.cover_url" :src="album.cover_url" class="w-full h-full object-cover">
                    <div v-else class="w-full h-full flex items-center justify-center text-slate-600 text-xl">
                      <i class="fa-solid fa-compact-disc"></i>
                    </div>
                  </div>

                  <!-- 专辑信息 -->
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center gap-2">
                      <h5 class="font-bold text-white text-sm truncate" :title="album.name">{{{{ album.name }}}}</h5>
                      <span v-if="album.release_date" class="text-[11px] px-2 py-0.5 rounded bg-slate-700/60 text-slate-300 font-mono flex-shrink-0">
                        {{{{ album.release_date.slice(0, 10) }}}}
                      </span>
                    </div>
                    <p class="text-xs text-slate-400 truncate mt-0.5">
                      共 <b class="text-emerald-400">{{{{ album.tracks.length }}}}</b> 首曲目
                    </p>
                  </div>
                </div>

                <div class="flex items-center gap-2 text-slate-400">
                  <span class="text-xs text-slate-500 hidden sm:inline">{{{{ collapsedAlbums[album.name] ? '展开' : '折叠' }}}}</span>
                  <i 
                    class="fa-solid fa-chevron-down transition-transform duration-200"
                    :class="{{ 'rotate-180': !collapsedAlbums[album.name] }}"
                  ></i>
                </div>
              </div>

              <!-- 专辑内曲目列表 -->
              <div v-show="!collapsedAlbums[album.name]" class="p-2.5 bg-slate-900/40 border-t border-slate-800/80 space-y-1">
                <div 
                  v-for="(t, i) in album.tracks" 
                  :key="t.spotify_id || i"
                  class="flex items-center justify-between px-3 py-2 rounded-lg bg-slate-800/30 hover:bg-slate-800/80 text-xs text-slate-300 transition group"
                >
                  <div class="flex items-center gap-3 min-w-0 flex-1">
                    <span class="text-slate-500 font-mono w-6 text-right flex-shrink-0">{{{{ t.track_number || (i + 1) }}}}</span>
                    <span class="font-medium text-white group-hover:text-emerald-300 transition truncate" :title="t.title">{{{{ t.title }}}}</span>
                    <span class="text-slate-400 truncate flex-shrink-0" :title="t.artist"> - {{{{ t.artist }}}}</span>
                  </div>
                  <div class="flex items-center gap-3 flex-shrink-0 text-slate-500 font-mono text-[11px] ml-2">
                    <span v-if="t.duration">{{{{ formatDuration(t.duration) }}}}</span>
                    <a v-if="t.url" :href="t.url" target="_blank" class="hover:text-emerald-400 text-slate-500" title="在 Spotify 中打开">
                      <i class="fa-solid fa-arrow-up-right-from-square"></i>
                    </a>
                  </div>
                </div>
              </div>
            </div>

            <div v-if="albumGroups.length === 0" class="p-8 text-center text-slate-500 text-xs">
              没有匹配到任何专辑或曲目
            </div>
          </div>
        </div>
      </div>
    </main>

    <!-- TAB 3: 实时下载任务与队列监控 -->
    <main v-if="activeTab === 'tasks'" class="space-y-6">
      <div class="glass rounded-2xl p-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 class="text-base font-bold text-white flex items-center gap-2">
            <i class="fa-solid fa-list-check text-emerald-400"></i>
            下载任务与转码队列
          </h2>
          <p class="text-xs text-slate-400 mt-1">自动执行：音频检索 ➔ yt-dlp高品质下载 ➔ 歌词与元数据内嵌 ➔ 目录整理归档</p>
        </div>
        <div class="flex items-center gap-2 flex-wrap">
          <button 
            v-if="failedTaskCount > 0"
            @click="retryFailedTasks" 
            :disabled="retryingFailed"
            class="px-3 py-2 rounded-xl bg-rose-950/60 hover:bg-rose-900/80 border border-rose-800 text-xs text-rose-300 transition flex items-center gap-1.5"
            title="重试所有失败任务"
          >
            <i class="fa-solid fa-rotate-left" :class="{{ 'fa-spin': retryingFailed }}"></i>
            重试失败 ({{{{ failedTaskCount }}}})
          </button>
          <button 
            v-if="completedTaskCount > 0"
            @click="clearCompletedTasks" 
            :disabled="clearingCompleted"
            class="px-3 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 border border-slate-700 text-xs text-slate-300 transition flex items-center gap-1.5"
            title="清理已完成的历史任务"
          >
            <i class="fa-solid fa-broom"></i>
            清理已完成
          </button>
          <button @click="fetchTasks" class="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs text-slate-300 transition flex items-center gap-2">
            <i class="fa-solid fa-rotate" :class="{{ 'fa-spin': refreshingTasks }}"></i>
            刷新
          </button>
        </div>
      </div>

      <!-- 任务列表 -->
      <div v-if="tasks.length > 0" class="space-y-3">
        <div 
          v-for="task in tasks" 
          :key="task.id"
          class="glass-card rounded-2xl p-4 flex flex-col md:flex-row md:items-center justify-between gap-4"
        >
          <div class="flex items-center gap-4 flex-1 min-w-0">
            <div class="w-12 h-12 rounded-xl bg-slate-800 flex items-center justify-center text-xl flex-shrink-0 overflow-hidden">
              <img v-if="task.cover_url" :src="task.cover_url" class="w-full h-full object-cover">
              <i v-else class="fa-solid fa-music text-slate-600"></i>
            </div>
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2">
                <h3 class="font-bold text-white text-sm truncate">{{{{ task.title }}}}</h3>
                <span class="text-xs text-emerald-400 truncate"> - {{{{ task.artist }}}}</span>
              </div>
              <p class="text-xs text-slate-400 truncate mt-0.5">
                {{{{ task.album || '单曲下载' }}}} 
                <span v-if="task.playlist_name" class="ml-1 text-slate-500 font-normal">({{{{ task.playlist_name }}}})</span>
              </p>
            </div>
          </div>

          <!-- 进度与状态 -->
          <div class="flex items-center gap-4 md:w-80 justify-between md:justify-end">
            <div class="flex-1 max-w-[180px]">
              <div class="flex items-center justify-between text-[11px] mb-1">
                <span class="text-slate-400">{{{{ getStatusText(task.status) }}}}</span>
                <span class="font-mono text-slate-300 font-bold">{{{{ Math.round(task.progress || 0) }}}}%</span>
              </div>
              <div class="w-full bg-slate-700/60 rounded-full h-2 overflow-hidden">
                <div 
                  class="h-full rounded-full transition-all duration-300"
                  :class="task.status === 'completed' ? 'bg-emerald-500' : (task.status === 'failed' ? 'bg-rose-500' : 'bg-amber-500')"
                  :style="{{ width: (task.progress || 0) + '%' }}"
                ></div>
              </div>
              <p v-if="task.error_msg || task.error" class="text-[10px] text-rose-400 truncate mt-1" :title="task.error_msg || task.error">{{{{ task.error_msg || task.error }}}}</p>
            </div>

            <span 
              class="px-2.5 py-1 rounded-lg text-xs font-semibold"
              :class="getStatusBadgeClass(task.status)"
            >
              {{{{ task.status.toUpperCase() }}}}
            </span>
          </div>
        </div>
      </div>

      <div v-else class="glass rounded-2xl p-12 text-center text-slate-400 text-sm">
        <i class="fa-solid fa-inbox text-4xl text-slate-600 mb-3"></i>
        <p>暂无下载任务记录。可以在“音乐搜索”或“Spotify 订阅”中添加任务！</p>
      </div>
    </main>

    <!-- TAB 4: 订阅管理 -->
    <main v-if="activeTab === 'subs'" class="space-y-6">
      <div class="glass rounded-2xl p-6 flex items-center justify-between">
        <div>
          <h2 class="text-base font-bold text-white flex items-center gap-2">
            <i class="fa-solid fa-satellite-dish text-emerald-400"></i>
            已激活的 Spotify 订阅列表
          </h2>
          <p class="text-xs text-slate-400 mt-1">系统将按设定的巡检周期自动扫描更新，仅下载新加入的曲目</p>
        </div>
        <button @click="fetchSubscriptions" class="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs text-slate-300 transition flex items-center gap-2">
          <i class="fa-solid fa-rotate"></i>
          刷新列表
        </button>
      </div>

      <div v-if="subscriptions.length > 0" class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div 
          v-for="sub in subscriptions" 
          :key="sub.id"
          class="glass-card rounded-2xl p-5 space-y-4"
        >
          <div class="flex items-start gap-4">
            <div class="w-16 h-16 rounded-xl overflow-hidden bg-slate-800 flex-shrink-0 shadow">
              <img v-if="sub.cover_url" :src="sub.cover_url" class="w-full h-full object-cover">
              <div v-else class="w-full h-full flex items-center justify-center text-2xl text-slate-600">
                <i class="fa-solid fa-music"></i>
              </div>
            </div>
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2">
                <span class="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-800 text-slate-300 border border-slate-700">
                  {{{{ (sub.type || '').toUpperCase() }}}}
                </span>
                <span 
                  class="px-2 py-0.5 rounded text-[10px] font-bold"
                  :class="sub.sync_mode === 'only_new' ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' : (sub.sync_mode === 'once' ? 'bg-amber-500/20 text-amber-400 border border-amber-500/30' : 'bg-blue-500/20 text-blue-400 border border-blue-500/30')"
                >
                  {{{{ sub.sync_mode === 'only_new' ? '🌿 仅监控新增' : (sub.sync_mode === 'once' ? '⚡ 单次下载' : '📦 全量订阅') }}}}
                </span>
              </div>
              <h3 class="font-bold text-white text-base truncate mt-1" :title="sub.name">{{{{ sub.name }}}}</h3>
              <p class="text-xs text-slate-400 mt-0.5">巡检周期: {{{{ sub.interval_minutes }}}} 分钟</p>
            </div>
          </div>

          <div class="pt-3 border-t border-slate-700/50 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400">
            <div class="flex items-center gap-3">
              <span v-if="sub.sync_mode === 'only_new'">已下载: <b class="text-white">{{{{ sub.downloaded_tracks || 0 }}}}</b> 首</span>
              <span v-else>已下载: <b class="text-white">{{{{ sub.downloaded_tracks || 0 }}}}</b> / {{{{ Math.max(sub.total_tracks || 0, sub.downloaded_tracks || 0) }}}} 首</span>
              <span v-if="sub.sync_mode === 'only_new' && (sub.skipped_tracks || sub.total_tracks)" class="px-2 py-0.5 rounded bg-slate-800 text-emerald-400 border border-emerald-500/20 text-[11px]" :title="'仅监控新增模式已跳过存量基准曲目 ' + (sub.skipped_tracks || sub.total_tracks) + ' 首'">
                已跳过存量: {{{{ sub.skipped_tracks || sub.total_tracks }}}} 首
              </span>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-[11px] text-slate-500 hidden sm:inline" :title="'UTC: ' + (sub.last_checked || sub.last_check || '')">
                上次: {{{{ formatTime(sub.last_checked || sub.last_check) }}}}
              </span>
              <button 
                @click="inspectSubscription(sub)" 
                class="px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white border border-slate-700 transition text-[11px] flex items-center gap-1"
                title="查看专辑与曲目预览"
              >
                <i class="fa-solid fa-eye"></i> 预览
              </button>
              <button 
                @click="syncSubscription(sub)" 
                :disabled="syncingSubMap[sub.id]"
                class="px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-emerald-400 hover:text-emerald-300 border border-slate-700 transition text-[11px] flex items-center gap-1"
                title="立即触发增量检查"
              >
                <i class="fa-solid fa-rotate" :class="{{ 'fa-spin': syncingSubMap[sub.id] }}"></i> 立即检查
              </button>
              <button 
                @click="deleteSubscription(sub)" 
                :disabled="deletingSubMap[sub.id]"
                class="px-2.5 py-1 rounded bg-rose-950/40 hover:bg-rose-900/60 text-rose-400 hover:text-rose-300 border border-rose-900/50 transition text-[11px] flex items-center gap-1"
                title="删除此订阅"
              >
                <i class="fa-solid fa-trash-can"></i> 删除
              </button>
            </div>
          </div>
        </div>
      </div>

      <div v-else class="glass rounded-2xl p-12 text-center text-slate-400 text-sm">
        <i class="fa-solid fa-seedling text-4xl text-slate-600 mb-3"></i>
        <p>暂无活跃订阅。可以在“Spotify 订阅”选项卡中粘贴链接添加！</p>
      </div>
    </main>
  </div>

  <script>
    const {{ createApp, ref, onMounted, computed }} = Vue;

    createApp({{
      setup() {{
        const apiPrefix = '{api_prefix}';
        const defaultToken = '{default_token}';
        const urlParams = new URLSearchParams(window.location.search);
        const token = ref(urlParams.get('token') || urlParams.get('apikey') || defaultToken || localStorage.getItem('mp_api_token') || '');
        if (token.value) {{
          localStorage.setItem('mp_api_token', token.value);
        }}

        const showTokenModal = ref(!token.value);
        const tokenInput = ref(token.value);

        const copyToken = async () => {{
          if (!token.value) {{
            showToast('当前无可用 Token', 'error');
            return;
          }}
          try {{
            if (navigator.clipboard && navigator.clipboard.writeText) {{
              await navigator.clipboard.writeText(token.value);
            }} else {{
              const textarea = document.createElement('textarea');
              textarea.value = token.value;
              document.body.appendChild(textarea);
              textarea.select();
              document.execCommand('copy');
              document.body.removeChild(textarea);
            }}
            showToast('API-Key 凭证已成功复制到剪贴板！');
          }} catch (err) {{
            showToast('复制失败，请手动复制', 'error');
          }}
        }};

        const saveToken = () => {{
          if (!tokenInput.value.trim()) return;
          token.value = tokenInput.value.trim();
          localStorage.setItem('mp_api_token', token.value);
          showTokenModal.value = false;
          showToast('API Token 已保存！');
          fetchTasks();
          fetchSubscriptions();
        }};

        const tabs = [
          {{ id: 'search', name: '单曲搜索下载', icon: 'fa-solid fa-magnifying-glass' }},
          {{ id: 'spotify', name: 'Spotify 链接订阅', icon: 'fa-brands fa-spotify' }},
          {{ id: 'tasks', name: '下载任务监控', icon: 'fa-solid fa-list-check' }},
          {{ id: 'subs', name: '订阅管理', icon: 'fa-solid fa-satellite-dish' }},
        ];
        const activeTab = ref('search');

        // Toast 提示
        const toast = ref({{ show: false, message: '', type: 'success' }});
        const showToast = (msg, type = 'success') => {{
          toast.value = {{ show: true, message: msg, type }};
          setTimeout(() => {{ toast.value.show = false; }}, 3500);
        }};

        // 通用 API 请求封装
        const request = async (path, options = {{}}) => {{
          const headers = {{
            'Content-Type': 'application/json',
            ...(options.headers || {{}})
          }};
          if (token.value) {{
            headers['X-API-Key'] = token.value;
          }}
          const delim = path.includes('?') ? '&' : '?';
          const fullUrl = `${{apiPrefix}}${{path}}${{token.value ? delim + 'token=' + encodeURIComponent(token.value) : ''}}`;
          try {{
            const res = await fetch(fullUrl, {{ ...options, headers }});
            if (res.status === 401) {{
              showTokenModal.value = true;
              showToast('请配置 MoviePilot API Token 以进行鉴权', 'error');
              throw new Error('未授权 (401)');
            }}
            let data;
            try {{
              data = await res.json();
            }} catch (jsonErr) {{
              data = null;
            }}
            if (!res.ok) {{
              const errMsg = (data && (data.detail || data.message)) || `请求失败 (${{res.status}})`;
              throw new Error(errMsg);
            }}
            if (data && data.success === false) {{
              throw new Error(data.message || '操作失败');
            }}
            return data;
          }} catch (err) {{
            console.error('API Error:', err);
            throw err;
          }}
        }};

        // TAB 1: 搜索
        const searchQuery = ref('');
        const searching = ref(false);
        const searched = ref(false);
        const searchResults = ref({{ tracks: [], albums: [], artists: [], playlists: [] }});
        const searchCategory = ref('all');
        const downloadingMap = ref({{}});
        const downloadedMap = ref({{}});
        const subscribingMap = ref({{}});

        const totalResultsCount = computed(() => {{
          const r = searchResults.value || {{}};
          return (r.tracks?.length || 0) + (r.albums?.length || 0) + (r.artists?.length || 0) + (r.playlists?.length || 0);
        }});

        const handleSearch = async () => {{
          if (!searchQuery.value.trim()) return;
          searching.value = true;
          searched.value = true;
          searchCategory.value = 'all';
          try {{
            const res = await request(`/search/query?query=${{encodeURIComponent(searchQuery.value)}}`);
            const d = (res && res.data) ? res.data : {{}};
            if (Array.isArray(d)) {{
              searchResults.value = {{ tracks: d, albums: [], artists: [], playlists: [] }};
            }} else {{
              searchResults.value = {{
                tracks: d.tracks || [],
                albums: d.albums || [],
                artists: d.artists || [],
                playlists: d.playlists || [],
              }};
            }}
            if (totalResultsCount.value === 0) {{
              showToast('未找到匹配内容，请尝试更换关键词', 'error');
            }}
          }} catch (e) {{
            showToast('搜索失败：' + (e.message || e), 'error');
          }} finally {{
            searching.value = false;
          }}
        }};

        const subscribeEntity = async (item, mode = 'full') => {{
          const key = item.id || item.url;
          subscribingMap.value[key] = true;
          try {{
            await request('/subscriptions/add', {{
              method: 'POST',
              body: JSON.stringify({{
                url: item.url,
                sync_mode: mode,
              }})
            }});
            showToast(`已成功添加《${{item.title || item.name}}》订阅！`);
            fetchSubscriptions();
          }} catch (e) {{
            showToast('添加订阅失败: ' + (e.message || e), 'error');
          }} finally {{
            subscribingMap.value[key] = false;
          }}
        }};

        const downloadTrack = async (item) => {{
          downloadingMap.value[item.id] = true;
          try {{
            await request('/download/single', {{
              method: 'POST',
              body: JSON.stringify({{
                title: item.title,
                artist: item.artist,
                album: item.album || '',
                album_artist: item.album_artist || item.artist,
                cover_url: item.cover_url || '',
                duration: item.duration || 0,
              }})
            }});
            downloadedMap.value[item.id] = true;
            showToast(`已将《${{item.title}}》推入后台下载队列！`);
            fetchTasks();
          }} catch (e) {{
            showToast('提交下载失败: ' + e.message, 'error');
          }} finally {{
            downloadingMap.value[item.id] = false;
          }}
        }};

        // TAB 2: Spotify
        const spotifyUrl = ref('');
        const resolvingSpotify = ref(false);
        const resolvedEntity = ref(null);
        const submittingSub = ref(false);
        const albumSearchQuery = ref('');
        const collapsedAlbums = ref({{}});

        const toggleAlbum = (albumName) => {{
          collapsedAlbums.value[albumName] = !collapsedAlbums.value[albumName];
        }};

        const expandAllAlbums = () => {{
          collapsedAlbums.value = {{}};
        }};

        const collapseAllAlbums = () => {{
          const map = {{}};
          for (const g of albumGroups.value) {{
            map[g.name] = true;
          }}
          collapsedAlbums.value = map;
        }};

        const formatDuration = (sec) => {{
          if (!sec || isNaN(sec)) return '--:--';
          const m = Math.floor(sec / 60);
          const s = Math.floor(sec % 60);
          return `${{m.toString().padStart(2, '0')}}:${{s.toString().padStart(2, '0')}}`;
        }};

        const formatTime = (isoStr) => {{
          if (!isoStr) return '未执行';
          try {{
            let normalized = String(isoStr).trim();
            if (normalized.length === 19 && normalized.indexOf('T') === 10) {{
              normalized += 'Z';
            }}
            const d = new Date(normalized);
            if (isNaN(d.getTime())) {{
              return normalized.slice(0, 16).replace('T', ' ');
            }}
            const pad = (n) => n.toString().padStart(2, '0');
            const y = d.getFullYear();
            const m = pad(d.getMonth() + 1);
            const date = pad(d.getDate());
            const h = pad(d.getHours());
            const min = pad(d.getMinutes());
            return y + '-' + m + '-' + date + ' ' + h + ':' + min;
          }} catch (e) {{
            return String(isoStr).slice(0, 16).replace('T', ' ');
          }}
        }};

        const albumGroups = computed(() => {{
          if (!resolvedEntity.value || !resolvedEntity.value.tracks) return [];
          const query = albumSearchQuery.value.trim().toLowerCase();
          const groups = [];
          const map = new Map();

          for (const t of resolvedEntity.value.tracks) {{
            const albumName = t.album || resolvedEntity.value.name || '单曲与精选';
            let group = map.get(albumName);
            if (!group) {{
              group = {{
                name: albumName,
                cover_url: t.cover_url || resolvedEntity.value.cover_url || '',
                release_date: t.release_date || '',
                tracks: [],
              }};
              map.set(albumName, group);
              groups.push(group);
            }} else {{
              if (!group.cover_url && t.cover_url) {{
                group.cover_url = t.cover_url;
              }}
              if (!group.release_date && t.release_date) {{
                group.release_date = t.release_date;
              }}
            }}
            group.tracks.push(t);
          }}

          if (!query) {{
            return groups;
          }}

          const filtered = [];
          for (const g of groups) {{
            const albumMatch = g.name.toLowerCase().includes(query);
            const matchedTracks = g.tracks.filter(t => 
              (t.title && t.title.toLowerCase().includes(query)) ||
              (t.artist && t.artist.toLowerCase().includes(query))
            );
            if (albumMatch) {{
              filtered.push(g);
            }} else if (matchedTracks.length > 0) {{
              filtered.push({{
                ...g,
                tracks: matchedTracks,
              }});
            }}
          }}
          return filtered;
        }});

        const inspectSubscription = (sub) => {{
          if (!sub || !sub.url) return;
          spotifyUrl.value = sub.url;
          activeTab.value = 'spotify';
          handleResolveSpotify();
        }};

        const handleResolveSpotify = async () => {{
          if (!spotifyUrl.value.trim()) return;
          resolvingSpotify.value = true;
          try {{
            const res = await request(`/resolve?url=${{encodeURIComponent(spotifyUrl.value)}}`);
            if (res && res.data) {{
              resolvedEntity.value = res.data;
              showToast(`成功解析：${{res.data.name}} (${{(res.data.tracks || []).length}} 首曲目)`);
            }}
          }} catch (e) {{
            showToast('解析 Spotify 链接失败: ' + e.message, 'error');
          }} finally {{
            resolvingSpotify.value = false;
          }}
        }};

        const submitSubscription = async (mode) => {{
          if (!resolvedEntity.value) return;
          submittingSub.value = true;
          try {{
            await request('/subscribe', {{
              method: 'POST',
              body: JSON.stringify({{
                url: spotifyUrl.value,
                sync_mode: mode,
                interval_minutes: 60
              }})
            }});
            showToast(mode === 'only_new' ? '🌿 仅监控新增订阅添加成功！已建立存量基准。' : '订阅任务已成功创建！');
            fetchTasks();
            fetchSubscriptions();
          }} catch (e) {{
            showToast('添加订阅失败: ' + e.message, 'error');
          }} finally {{
            submittingSub.value = false;
          }}
        }};

        // TAB 3: 任务
        const tasks = ref([]);
        const refreshingTasks = ref(false);
        const clearingCompleted = ref(false);
        const retryingFailed = ref(false);

        const activeTaskCount = computed(() => {{
          return tasks.value.filter(t => t.status === 'pending' || t.status === 'downloading' || t.status === 'tagging' || t.status === 'matching' || t.status === 'processing').length;
        }});
        const completedTaskCount = computed(() => {{
          return tasks.value.filter(t => t.status === 'completed').length;
        }});
        const failedTaskCount = computed(() => {{
          return tasks.value.filter(t => t.status === 'failed').length;
        }});

        const fetchTasks = async () => {{
          refreshingTasks.value = true;
          try {{
            const res = await request('/tasks');
            if (res && res.data) {{
              tasks.value = res.data;
            }}
          }} catch (e) {{
            // 静默失败
          }} finally {{
            refreshingTasks.value = false;
          }}
        }};

        const clearCompletedTasks = async () => {{
          clearingCompleted.value = true;
          try {{
            const res = await request('/tasks/clear_completed', {{ method: 'POST' }});
            showToast(res.message || '已清理已完成任务');
            fetchTasks();
          }} catch (e) {{
            showToast('清理失败: ' + e.message, 'error');
          }} finally {{
            clearingCompleted.value = false;
          }}
        }};

        const retryFailedTasks = async () => {{
          retryingFailed.value = true;
          try {{
            const res = await request('/tasks/retry_failed', {{ method: 'POST' }});
            showToast(res.message || '已重新排队失败任务');
            fetchTasks();
          }} catch (e) {{
            showToast('重试失败: ' + e.message, 'error');
          }} finally {{
            retryingFailed.value = false;
          }}
        }};

        const getStatusText = (st) => {{
          const map = {{
            pending: '排队中',
            matching: '音轨检索中',
            downloading: '正在下载音频',
            processing: '正在整理归档',
            tagging: '内嵌元数据与歌词',
            completed: '已完成并归档',
            failed: '处理失败'
          }};
          return map[st] || st;
        }};

        const getStatusBadgeClass = (st) => {{
          const map = {{
            pending: 'bg-slate-700 text-slate-300',
            matching: 'bg-purple-500/20 text-purple-300 border border-purple-500/30',
            downloading: 'bg-amber-500/20 text-amber-300 border border-amber-500/30',
            processing: 'bg-blue-500/20 text-blue-300 border border-blue-500/30',
            tagging: 'bg-blue-500/20 text-blue-300 border border-blue-500/30',
            completed: 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30',
            failed: 'bg-rose-500/20 text-rose-400 border border-rose-500/30'
          }};
          return map[st] || 'bg-slate-700 text-slate-300';
        }};

        // TAB 4: 订阅列表
        const subscriptions = ref([]);
        const syncingSubMap = ref({{}});
        const deletingSubMap = ref({{}});

        const fetchSubscriptions = async () => {{
          try {{
            const res = await request('/subscriptions');
            if (res && res.data) {{
              subscriptions.value = res.data;
            }}
          }} catch (e) {{
            // 静默
          }}
        }};

        const syncSubscription = async (sub) => {{
          if (!sub || !sub.id) return;
          syncingSubMap.value[sub.id] = true;
          try {{
            const res = await request(`/subscriptions/${{sub.id}}/sync`, {{ method: 'POST' }});
            showToast(res.message || `正在后台检查【${{sub.name}}】`);
            setTimeout(fetchTasks, 1000);
          }} catch (e) {{
            showToast('触发检查失败: ' + e.message, 'error');
          }} finally {{
            syncingSubMap.value[sub.id] = false;
          }}
        }};

        const deleteSubscription = async (sub) => {{
          if (!sub || !sub.id) return;
          if (!confirm(`确定要删除订阅【${{sub.name}}】吗？此操作不会删除已下载的本地音乐文件。`)) {{
            return;
          }}
          deletingSubMap.value[sub.id] = true;
          try {{
            await request(`/subscriptions/${{sub.id}}`, {{ method: 'DELETE' }});
            showToast(`已成功删除订阅【${{sub.name}}】`);
            fetchSubscriptions();
          }} catch (e) {{
            showToast('删除订阅失败: ' + e.message, 'error');
          }} finally {{
            deletingSubMap.value[sub.id] = false;
          }}
        }};

        onMounted(() => {{
          fetchTasks();
          fetchSubscriptions();
          // 定时轮询任务进度
          setInterval(() => {{
            if (activeTab.value === 'tasks' || activeTaskCount.value > 0) {{
              fetchTasks();
            }}
          }}, 3000);
        }});

        return {{
          tabs,
          activeTab,
          toast,
          searchQuery,
          searching,
          searched,
          searchResults,
          searchCategory,
          totalResultsCount,
          subscribingMap,
          subscribeEntity,
          downloadingMap,
          downloadedMap,
          handleSearch,
          downloadTrack,
          spotifyUrl,
          resolvingSpotify,
          resolvedEntity,
          submittingSub,
          handleResolveSpotify,
          submitSubscription,
          albumSearchQuery,
          collapsedAlbums,
          toggleAlbum,
          expandAllAlbums,
          collapseAllAlbums,
          formatDuration,
          formatTime,
          albumGroups,
          inspectSubscription,
          tasks,
          refreshingTasks,
          clearingCompleted,
          retryingFailed,
          activeTaskCount,
          completedTaskCount,
          failedTaskCount,
          fetchTasks,
          clearCompletedTasks,
          retryFailedTasks,
          getStatusText,
          getStatusBadgeClass,
          subscriptions,
          syncingSubMap,
          deletingSubMap,
          fetchSubscriptions,
          syncSubscription,
          deleteSubscription,
          showTokenModal,
          tokenInput,
          saveToken,
          token,
          copyToken,
        }};
      }}
    }}).mount('#app');
  </script>
</body>
</html>
"""
