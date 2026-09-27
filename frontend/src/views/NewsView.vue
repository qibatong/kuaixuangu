<template>
  <div class="page-shell">
    <h1 class="visually-hidden">盘前资讯</h1>

    <div class="news-head">
      <span class="news-title"><i class="fa fa-newspaper-o"></i> 盘前资讯</span>
      
      <span class="news-time">{{ bjTime }}</span>
    </div>

    <div class="nv-cols">
    <!-- ① 热门个股 -->
    <HotRankMulti />

    <!-- ===== ② 7×24 快讯 ===== -->
    <section class="nv-card">
      <div class="nv-head">
        <span class="nv-title"><i class="fa fa-bolt"></i> 7×24 快讯</span>
        <span v-if="flashUpdated" class="nv-time">{{ flashUpdated }}</span>
        <button class="news-refresh" :disabled="flashLoading" title="刷新" @click="loadFlash(true)">
          <i class="fa fa-refresh" :class="{ spin: flashLoading }"></i>
        </button>
      </div>
      <div v-if="flashDegraded.length" class="news-degraded">
        <i class="fa fa-exclamation-triangle"></i> 数据源暂缺：{{ flashDegraded.join('、') }}。以下为其余来源。
      </div>
      <div v-if="flashLoading && !flashList.length" class="loading-placeholder"><div class="spinner"></div><div>加载资讯中...</div></div>
      <div v-else-if="flashErr" class="empty-state">{{ flashErr }}<button class="news-retry" @click="loadFlash(true)">点我重试</button></div>
      <div v-else-if="!flashList.length" class="empty-state">暂无快讯</div>
      <div v-else class="nf-list">
        <template v-for="(it, i) in flashRows" :key="it.id || i">
          <div v-if="it.showDay" class="nf-day"><span class="nf-day-tag">{{ it.date || '--' }}</span></div>
          <div class="nf-item">
            <div class="nf-line">
              <span class="nf-time">{{ it.time_label || '--:--' }}</span>
              <span class="nf-src" :class="'src-' + it.kind">{{ it.source }}</span>
              <span class="nf-title">{{ it.title }}</span>
            </div>
            <div v-if="it.summary && it.summary !== it.title" class="nf-summary">{{ it.summary }}</div>
            <div class="nf-foot">
              <a v-if="it.url" class="nf-link" :href="it.url" target="_blank" rel="noopener noreferrer">查看原文 <i class="fa fa-external-link"></i></a>
              <span v-if="it.stocks && it.stocks.length" class="nf-stocks">相关：{{ stockNames(it.stocks) }}</span>
            </div>
          </div>
        </template>
      </div>
    </section>

    <!-- ===== ③ 盘前精选 ===== -->
    <section class="nv-card">
      <div class="nv-head">
        <span class="nv-title"><i class="fa fa-star"></i> 盘前精选</span>
        <button class="news-refresh" :disabled="pmLoading" title="刷新" @click="loadPremarket(true)">
          <i class="fa fa-refresh" :class="{ spin: pmLoading }"></i>
        </button>
      </div>
      <div v-if="pmLoading && !pm" class="loading-placeholder"><div class="spinner"></div><div>加载盘前精选...</div></div>
      <div v-else-if="pmErr" class="empty-state">{{ pmErr }}</div>
      <template v-else-if="pm">
        <div class="pm-block">
          <div class="pm-block-head"><i class="fa fa-star"></i> 开盘啦头条 <span v-if="pm.top && pm.top.length" class="pm-block-sub">{{ pm.top[0].date }}</span></div>
          <div v-if="!pm.top || !pm.top.length" class="pm-empty">暂无头条</div>
          <div v-for="t in pm.top" :key="t.cid" class="pm-top-item">
            <div class="pm-top-title">{{ t.title }}</div>
            <div class="pm-top-body" :class="{ collapsed: !expandedTop[t.cid] }" v-html="safeHtml(t.content_html)"></div>
            <button class="pm-more" @click="toggleTop(t.cid)">{{ expandedTop[t.cid] ? '收起' : '展开全文' }} <i class="fa" :class="expandedTop[t.cid] ? 'fa-angle-up' : 'fa-angle-down'"></i></button>
          </div>
        </div>
        <div class="pm-block">
          <div class="pm-block-head"><i class="fa fa-lightbulb-o"></i> 明天炒什么 <span v-if="pm.topics && pm.topics.day" class="pm-block-sub">选题日 {{ pm.topics.day }}</span></div>
          <div v-if="!pm.topics || !pm.topics.items.length" class="pm-empty">暂无选题</div>
          <div v-for="it in (pm.topics ? pm.topics.items : [])" :key="it.id" class="pm-topic" @click="openTopic(it)">
            <span class="pm-topic-title">{{ it.title }}</span>
            <span class="pm-topic-hot"><i class="fa fa-fire"></i> {{ fmtHot(it.hot_val) }}</span>
          </div>
        </div>
      </template>
    </section>

    <!-- ===== ④ 实时小作文(知识星球, 待接入) ===== -->
    <section class="nv-card">
      <div class="nv-head">
        <span class="nv-title"><i class="fa fa-comments"></i> 实时小作文</span>
        <span class="nv-tag">知识星球 · 待接入</span>
      </div>
      <div class="nv-empty">知识星球盘中小作文实时更新暂未接入，接入后在此按时间流展示。</div>
    </section>


    </div><!-- /nv-cols -->

    <!-- 明天炒什么 正文弹层 -->
    <div v-if="topicOpen" class="modal-mask" @click.self="closeTopic">
      <div class="modal-box">
        <div class="modal-head">
          <span class="modal-title">{{ topic.title || '加载中...' }}</span>
          <button class="modal-close" @click="closeTopic"><i class="fa fa-times"></i></button>
        </div>
        <div class="modal-body">
          <div v-if="topicLoading" class="loading-placeholder"><div class="spinner"></div></div>
          <div v-else-if="topicErr" class="empty-state">{{ topicErr }}</div>
          <template v-else>
            <div class="topic-src">
              <span v-if="topic.source">{{ topic.source }}</span>
              <span v-if="topic.ts">{{ fmtTs(topic.ts) }}</span>
              <span v-if="topic.hot_val">热度 {{ fmtHot(topic.hot_val) }}</span>
            </div>
            <div class="topic-content">{{ topic.content }}</div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
// 2026-09-27 v4.11.59: 「盘前资讯」页 —— 归入「竞价」一级分组（盘前时段）。
//
// 数据来源(全部经后端 services/news_feed.py 聚合, 前端不知道上游细节):
//   7x24 快讯  = 猫爪 apiname=news + 开盘啦 doc96, 合并去重按时间倒序
//   盘前精选   = 开盘啦 doc95 头条 + doc97 明天炒什么(点开 doc99 正文)
//   大V复盘   = **复用现有 /api/summary/history**(不重复实现, 不复制后端逻辑)
//
// ⚠️ 上游降级语义: 后端在上游挂掉时返回 ok=true + degraded=[...] + 剩余数据。
//    页面必须把 degraded 显示成「数据源暂缺」, 不能把空列表渲染成「今天没资讯」——
//    这正是 9 月 auc_vol_ratio 恒 0 一周没人发现的那类事故的同型诱因。
import { computed, onMounted, reactive, ref, watch } from 'vue'
import HotRankMulti from '../components/HotRankMulti.vue'
import { useRoute, useRouter } from 'vue-router'
import { guzhangFlash, newsPremarket, newsTopicDetail } from '../api/news'
import { summaryHistory } from '../api/summary'
import { trackUsageOnce } from '../api/activity'
import { usePolling } from '../composables/usePolling'
import { bjTimeStr } from '../utils/time'

const route = useRoute()
const router = useRouter()

const TABS = [
  { key: 'flash', label: '7×24 快讯', icon: 'fa-bolt' },
  { key: 'premarket', label: '盘前精选', icon: 'fa-star-o' },
  { key: 'bigv', label: '大V复盘', icon: 'fa-users' },
]
const TAB_KEYS = TABS.map((t) => t.key)

// tab 写进 URL query: 支持前进/后退/分享（与 /market 的 ?src= 同一套做法）
const tab = ref(TAB_KEYS.includes(route.query.tab) ? route.query.tab : 'flash')
watch(() => route.query.tab, (v) => {
  if (TAB_KEYS.includes(v) && v !== tab.value) tab.value = v
})
function switchTab(k) {
  if (tab.value === k) return
  tab.value = k
  router.replace({ name: 'news', query: k === 'flash' ? {} : { tab: k } })
}

const bjTime = ref('--:--:--')

// ---------------- 7x24 快讯 ----------------
const flashList = ref([])
const flashLoading = ref(false)
const flashErr = ref('')
const flashUpdated = ref('')
const flashDegraded = ref([])

// 快讯时间线: 只在跨天处插日期分隔条
const flashRows = computed(() => {
  let prev = ''
  return flashList.value.map((it) => {
    const showDay = it.date && it.date !== prev
    if (showDay) prev = it.date
    return { ...it, showDay }
  })
})

async function loadFlash(silent = false) {
  if (!silent) flashLoading.value = true
  try {
    const d = await guzhangFlash()
    flashList.value = (d.list || []).slice(0, 20)
    flashUpdated.value = d.updated || ''
    flashDegraded.value = d.degraded || []
    flashErr.value = ''
  } catch (e) {
    flashErr.value = e.message || '快讯加载失败'
  } finally {
    flashLoading.value = false
  }
}

// ---------------- 盘前精选 ----------------
const pm = ref(null)
const pmLoading = ref(false)
const pmErr = ref('')
const expandedTop = reactive({})

async function loadPremarket() {
  pmLoading.value = true
  try {
    pm.value = await newsPremarket()
    pmErr.value = ''
    // 头条是每天一篇的长文: 默认只展开第一篇, 其余折叠
    const tops = pm.value.top || []
    tops.forEach((t, i) => { expandedTop[t.cid] = i === 0 })
  } catch (e) {
    pmErr.value = e.message || '盘前精选加载失败'
  } finally {
    pmLoading.value = false
  }
}
function toggleTop(cid) { expandedTop[cid] = !expandedTop[cid] }

// ---------------- 明天炒什么 正文 ----------------
const topicOpen = ref(false)
const topicLoading = ref(false)
const topicErr = ref('')
const topic = reactive({ title: '', content: '', source: '', ts: 0, hot_val: 0 })

async function openTopic(it) {
  topicOpen.value = true
  topicLoading.value = true
  topicErr.value = ''
  topic.title = it.title
  topic.content = ''
  topic.source = ''
  topic.ts = 0
  topic.hot_val = 0
  try {
    const d = await newsTopicDetail(it.id)
    topic.title = d.title || it.title
    topic.content = d.content || ''
    topic.source = d.source || ''
    topic.ts = d.ts || 0
    topic.hot_val = d.hot_val || 0
  } catch (e) {
    topicErr.value = e.message || '正文加载失败'
  } finally {
    topicLoading.value = false
  }
}
function closeTopic() { topicOpen.value = false }

// ---------------- 大V复盘(复用现有接口) ----------------
const bigvDays = ref([])
const bigvLoading = ref(false)
const bigvErr = ref('')
const BIGV_DAYS = 5

async function loadBigv() {
  bigvLoading.value = true
  try {
    const d = await summaryHistory()
    bigvDays.value = (d.days || []).slice(0, BIGV_DAYS)
    bigvErr.value = ''
  } catch (e) {
    bigvErr.value = e.message || '大V复盘加载失败'
  } finally {
    bigvLoading.value = false
  }
}

// ---------------- 轮询(只轮当前 tab, 不空跑) ----------------
const curLoading = computed(() => (
  tab.value === 'flash' ? flashLoading.value
    : tab.value === 'premarket' ? pmLoading.value : bigvLoading.value
))
function reloadCur(manual = false) {
  if (tab.value === 'flash') return loadFlash(manual)
  if (tab.value === 'premarket') return loadPremarket(manual)
  return loadBigv(manual)
}

const degradedList = computed(() => (
  tab.value === 'flash' ? (flashDegraded.value || []) : []
))

// ---------------- 展示工具 ----------------
function stockNames(stocks) {
  return (stocks || []).slice(0, 3).map((s) => {
    if (Array.isArray(s)) return s[1] || s[0]
    return (s && (s.name || s.code)) || ''
  }).filter(Boolean).join('、')
}
function fmtHot(v) {
  const n = Number(v) || 0
  return n >= 10000 ? (n / 10000).toFixed(1) + '万' : String(n)
}
function fmtTs(ts) {
  if (!ts) return ''
  try {
    return new Date(ts * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })
  } catch (e) { return '' }
}

// 开盘啦头条是第三方富文本 ⇒ 过一遍保守清洗再 v-html。
// 不用 DOMPurify(项目没这个依赖, 不为一个字段引包), 只做「去掉可执行面」这一件事。
function safeHtml(html) {
  let s = String(html || '')
  s = s.replace(/<\s*(script|style|iframe|object|embed|link|meta)[\s\S]*?<\s*\/\s*\1\s*>/gi, '')
  s = s.replace(/<\s*(script|style|iframe|object|embed|link|meta)\b[^>]*>/gi, '')
  s = s.replace(/\son[a-z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, '')
  s = s.replace(/(href|src)\s*=\s*(["']?)javascript:[^"'>\s]*\2/gi, '$1="#"')
  return s
}

onMounted(() => {
  bjTime.value = bjTimeStr()

  // 埋点: 打开即算一次 —— feature="news" 已加入后端 services/activity.FEATURES 白名单
  trackUsageOnce('news')

  // 五段垂直布局: 全部同时加载
  loadFlash()
  loadPremarket()
  loadBigv()
})

// ⚠️ 轮询**必须在 setup 顶层注册**, 不能放进 onMounted 回调里:
//    Vue 3.5 的 flushPostFlushCbs 调用 mounted 回调时**没有** setCurrentInstance
//    (见 @vue/runtime-core/dist/runtime-core.cjs.js `function flushPostFlushCbs`) ⇒
//    currentInstance 为 null ⇒ usePolling 里的 onBeforeUnmount **静默注册失败**
//    (生产构建连 warning 都没有) ⇒ 定时器与 visibilitychange 监听永不清理,
//    用户离开页面后仍按原频率打接口。开盘啦是 8 万次/日**付费配额**, 这是真金白银。
//    (2026-09-27 v4.11.59 全站排查: 8 个组件都是这个写法, 同批修正。)
//    初值仍由上面 onMounted 显式拉一次, 所以这里一律 immediate:false —— 顺带消掉
//    「immediate 首跳 + onMounted 显式拉」造成的**首屏双请求**。
usePolling(() => { bjTime.value = bjTimeStr() }, 1000, { immediate: false })
// 快讯 60s(与后端缓存 TTL 一致, 更快只会命中缓存); 盘前精选 / 大V 各 10min
usePolling(() => (tab.value === 'flash' ? loadFlash(true) : undefined),
           60 * 1000, { immediate: false, backoff: true })
usePolling(() => (tab.value === 'premarket' ? loadPremarket() : undefined),
           10 * 60 * 1000, { immediate: false, backoff: true })
usePolling(() => (tab.value === 'bigv' ? loadBigv() : undefined),
           10 * 60 * 1000, { immediate: false, backoff: true })

// 切到某 tab 时才首次拉它的数据(懒加载, 避免一次进页打三个接口)
watch(tab, (k) => {
  if (k === 'premarket' && !pm.value && !pmLoading.value) loadPremarket()
  if (k === 'bigv' && !bigvDays.value.length && !bigvLoading.value && !bigvErr.value) loadBigv()
})
</script>

<style scoped>
.news-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.news-title { font-size: 1.25rem; font-weight: 700; color: #ffe0a0; }
.news-title .fa { color: #ffb400; }
.news-sub { color: var(--text-muted); font-size: 0.8125rem; }
.news-time { margin-left: auto; color: var(--text-dim); font-size: 0.875rem; font-variant-numeric: tabular-nums; }

.news-tabs { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.news-tab {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 14px; border-radius: 999px; cursor: pointer;
  background: transparent; border: 1px solid var(--border-soft);
  color: var(--text-muted); font-size: 0.875rem; line-height: 1;
}
.news-tab.active { background: rgba(255, 90, 60, 0.14); border-color: #ff5a3c; color: #ff8a6a; font-weight: 600; }
.news-tab .fa { font-size: 0.875rem; }
.news-badge {
  background: rgba(255, 90, 60, 0.22); color: #ff8a6a;
  border-radius: 8px; padding: 1px 6px; font-size: 0.6875rem;
}
.news-refresh {
  margin-left: auto; background: transparent; border: 1px solid var(--border-soft);
  color: var(--text-muted); border-radius: 8px; padding: 6px 10px; cursor: pointer;
}
.news-refresh:disabled { opacity: 0.5; cursor: default; }
.news-refresh .spin { animation: news-spin 1s linear infinite; display: inline-block; }
@keyframes news-spin { to { transform: rotate(360deg); } }

.news-degraded {
  margin-bottom: 10px; padding: 8px 12px; border-radius: 8px; font-size: 0.8125rem;
  background: rgba(255, 170, 0, 0.12); border: 1px solid rgba(255, 170, 0, 0.35); color: #ffc861;
}
.news-meta { color: var(--text-dim); font-size: 0.75rem; margin-bottom: 8px; }
.news-retry {
  margin-left: 8px; background: transparent; border: 1px solid var(--border-soft);
  color: var(--text-muted); border-radius: 6px; padding: 2px 10px; cursor: pointer;
}
.bigv-all { color: #ff8a6a; text-decoration: none; }

/* ---------- 快讯时间线 ---------- */
.nf-list { display: flex; flex-direction: column; }
.nf-day { display: flex; align-items: center; gap: 10px; margin: 10px 0 6px; }
.nf-day-tag {
  background: rgba(255, 255, 255, 0.06); border: 1px solid var(--border-soft);
  border-radius: 6px; padding: 1px 8px; font-size: 0.75rem; color: var(--text-muted);
}
.nf-item {
  padding: 9px 12px; border-bottom: 1px solid var(--border-soft);
}
.nf-item:last-child { border-bottom: none; }
.nf-line { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.nf-time { color: var(--text-dim); font-size: 0.75rem; font-variant-numeric: tabular-nums; flex-shrink: 0; }
.nf-src {
  flex-shrink: 0; font-size: 0.6875rem; padding: 1px 6px; border-radius: 4px;
  border: 1px solid var(--border-soft); color: var(--text-muted);
}
.nf-src.src-kpl { border-color: rgba(255, 90, 60, 0.45); color: #ff8a6a; }
.nf-src.src-meoz { border-color: rgba(80, 160, 255, 0.45); color: #7cb6ff; }
.nf-title { font-size: 0.9375rem; color: var(--text-main, #e8e8e8); font-weight: 500; }
.nf-summary { margin: 4px 0 0 52px; color: var(--text-muted); font-size: 0.8125rem; line-height: 1.55; }
.nf-foot { margin: 4px 0 0 52px; display: flex; gap: 14px; flex-wrap: wrap; }
.nf-link { color: #7cb6ff; font-size: 0.75rem; text-decoration: none; }
.nf-stocks { color: var(--text-dim); font-size: 0.75rem; }

/* ---------- 盘前精选 ---------- */
.pm-block {
  border: 1px solid var(--border-soft); border-radius: 10px;
  padding: 12px 14px; margin-bottom: 12px;
}
.pm-block-head {
  display: flex; align-items: baseline; gap: 8px; font-weight: 600;
  color: #ffe0a0; margin-bottom: 8px; font-size: 0.9375rem;
}
.pm-block-head .fa { color: #ffb400; }
.pm-block-sub { color: var(--text-dim); font-size: 0.75rem; font-weight: 400; }
.pm-empty { color: var(--text-dim); font-size: 0.8125rem; }
.pm-top-item { margin-bottom: 10px; }
.pm-top-title { font-size: 1rem; font-weight: 600; color: var(--text-main, #e8e8e8); margin-bottom: 6px; }
.pm-top-body { color: var(--text-muted); font-size: 0.875rem; line-height: 1.7; overflow: hidden; }
.pm-top-body.collapsed { max-height: 5.1em; }
.pm-top-body :deep(p) { margin: 0 0 0.6em; }
.pm-more {
  background: transparent; border: none; color: #ff8a6a; cursor: pointer;
  font-size: 0.8125rem; padding: 4px 0; text-decoration: none;
}
.pm-topic {
  display: flex; align-items: center; gap: 10px; cursor: pointer;
  padding: 8px 4px; border-bottom: 1px dashed var(--border-soft);
}
.pm-topic:last-child { border-bottom: none; }
.pm-topic:hover .pm-topic-title { color: #ff8a6a; }
.pm-topic-title { flex: 1; min-width: 0; font-size: 0.9375rem; color: var(--text-main, #e8e8e8); }
.pm-topic-hot { color: #ffb400; font-size: 0.75rem; flex-shrink: 0; }
.pm-bigv-entry { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; color: var(--text-muted); font-size: 0.8125rem; }

/* ---------- 大V复盘 ---------- */
.bigv-day { margin-bottom: 12px; }
.bigv-day-badge {
  display: inline-block; margin-bottom: 6px; padding: 2px 10px; border-radius: 6px;
  background: rgba(255, 255, 255, 0.06); border: 1px solid var(--border-soft);
  font-size: 0.8125rem; color: var(--text-muted);
}
.bigv-slots { display: flex; gap: 8px; flex-wrap: wrap; }
.bigv-slot {
  flex: 1 1 190px; min-width: 150px; text-decoration: none;
  border: 1px solid var(--border-soft); border-radius: 8px; padding: 8px 10px;
  display: flex; flex-direction: column; gap: 4px;
}
.bigv-slot:hover { border-color: #ff5a3c; }
.bigv-slot-tag { font-size: 0.6875rem; color: #ff8a6a; }
.bigv-slot-title {
  font-size: 0.8125rem; color: var(--text-main, #e8e8e8);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.bigv-slot-meta { font-size: 0.6875rem; color: var(--text-dim); }

/* ---------- 正文弹层 ---------- */
.modal-mask {
  position: fixed; inset: 0; background: rgba(0, 0, 0, 0.6);
  display: flex; align-items: center; justify-content: center; z-index: 900; padding: 16px;
}
.modal-box {
  background: var(--bg-panel-solid, #1b1b1f); border: 1px solid var(--border-soft);
  border-radius: 12px; width: min(760px, 100%); max-height: 82vh; display: flex; flex-direction: column;
}
.modal-head {
  display: flex; align-items: center; gap: 10px; padding: 12px 16px;
  border-bottom: 1px solid var(--border-soft);
}
.modal-title { flex: 1; min-width: 0; font-size: 1rem; font-weight: 600; color: #ffe0a0; }
.modal-close { background: transparent; border: none; color: var(--text-muted); cursor: pointer; font-size: 1rem; }
.modal-body { padding: 14px 16px; overflow-y: auto; }
.topic-src { display: flex; gap: 14px; color: var(--text-dim); font-size: 0.75rem; margin-bottom: 10px; }
.topic-content { color: var(--text-muted); font-size: 0.875rem; line-height: 1.8; white-space: pre-wrap; }

/* ---------- 浅色主题 ---------- */
body[data-bg="light"] .news-title { color: #8a5500; }
body[data-bg="light"] .news-title .fa { color: #c79100; }
body[data-bg="light"] .news-tab.active { background: rgba(216, 60, 30, 0.10); border-color: #d83c1e; color: #c03318; }
body[data-bg="light"] .news-badge { background: rgba(216, 60, 30, 0.14); color: #c03318; }
body[data-bg="light"] .nf-title,
body[data-bg="light"] .pm-top-title,
body[data-bg="light"] .pm-topic-title,
body[data-bg="light"] .bigv-slot-title { color: #22262b; }
body[data-bg="light"] .nf-src.src-kpl { border-color: rgba(216, 60, 30, 0.4); color: #c03318; }
body[data-bg="light"] .nf-src.src-meoz { border-color: rgba(30, 110, 200, 0.4); color: #1e6ec8; }
body[data-bg="light"] .nf-link { color: #1e6ec8; }
body[data-bg="light"] .nf-day-tag,
body[data-bg="light"] .bigv-day-badge { background: rgba(0, 0, 0, 0.04); }
body[data-bg="light"] .news-degraded { background: rgba(200, 130, 0, 0.10); border-color: rgba(200, 130, 0, 0.35); color: #8a5a00; }
body[data-bg="light"] .pm-block-head { color: #8a5500; }
body[data-bg="light"] .pm-block-head .fa { color: #c79100; }
body[data-bg="light"] .news-retry,
body[data-bg="light"] .news-refresh { color: #55595f; }
body[data-bg="light"] .modal-box { background: #fff; }
body[data-bg="light"] .modal-title { color: #8a5500; }
body[data-bg="light"] .bigv-all,
body[data-bg="light"] .pm-more { color: #c03318; }

/* ---------- 手机端 ---------- */
@media (max-width: 768px) {
  .news-title { font-size: 1.0625rem; }
  .news-sub { font-size: 0.75rem; }
  .news-time { margin-left: 0; font-size: 0.75rem; }
  .news-tabs { flex-wrap: nowrap; overflow-x: auto; -webkit-overflow-scrolling: touch; }
  .news-tab { flex-shrink: 0; padding: 6px 12px; font-size: 0.8125rem; }
  .nf-item { padding: 8px 2px; }
  .nf-summary, .nf-foot { margin-left: 0; }
  .nf-title { font-size: 0.875rem; }
  .pm-top-body { font-size: 0.8125rem; }
  .bigv-slots { flex-direction: column; }
  .bigv-slot { min-width: 0; }
  .modal-box { max-height: 86vh; }
  .topic-content { font-size: 0.8125rem; }
}

/* ===== 五段垂直布局卡片 ===== */
.nv-card { background: var(--card, #111826); border: 1px solid var(--border-soft, #1f2937); border-radius: 12px; padding: 14px; margin-bottom: 14px; }
.nv-head { display: flex; align-items: center; margin-bottom: 12px; }
.nv-title { font-size: 0.9375rem; font-weight: 700; color: var(--text-main, #f3f4f6); }
.nv-title i { color: var(--accent, #ff7a5c); margin-right: 8px; }
.nv-time { margin-left: 10px; font-size: 0.6875rem; color: var(--text-muted, #6b7280); }
.nv-tag { margin-left: 10px; font-size: 0.6875rem; color: #b98aff; background: rgba(185,138,255,.1); padding: 2px 8px; border-radius: 4px; }
.nv-more { margin-left: auto; font-size: 0.75rem; color: var(--text-muted, #6b7280); text-decoration: none; }
.nv-head .news-refresh { margin-left: auto; }
.nv-empty { padding: 20px; text-align: center; color: var(--text-muted, #6b7280); font-size: 0.8125rem; }
.nv-cols { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; align-items: start; }
.nv-cols > * { min-width: 0; margin-bottom: 0; }
@media (max-width: 900px) { .nv-cols { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 560px) { .nv-cols { grid-template-columns: 1fr; } }
</style>
