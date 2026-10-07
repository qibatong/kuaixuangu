<template>
  <!--
    盘中盯盘台（2026-09-27 v4.11.62 · 工单 批次二）
    —— 一级分组「盘中」的唯一落地面，做成**一个滚动盯屏**，从上到下：
         0. 大盘温度（复用 components/SentimentPanel.vue，不新写）
         ① 财经快讯滚动条   FlashTicker      ← /api/news/flash(60s)
         ② 昨日涨停今日表现 YestZtPanel      ← /api/kpl/yest-zt + index-brief 的情绪值
         ③ 最强资金 TOP     MoneyTopStrip    ← 复用板块榜(不额外发请求)
         ④ 今日票战报       TodayPicksPanel  ← /api/history 当日批次 + /api/quotes 实时价
         ⑤ 题材榜           MarketBoardPanel ← 板块榜 + 涨停梯队题材分组(合并原「市场雷达」+「题材异动」)
    —— 🔴 2026-09-28：原「⑥ 实时异动流 YidongFlow」已**整层移除**（模板早搬到 `/yidong`，
       这里只剩 `loadYidong()` 死代码 + 未使用 import，还每分钟白打一次付费接口）。
       ⚠️ 2026-09-28 晚些：`/yidong` 置顶的那块也按主人要求去掉了 ⇒ 该功能**全站已无入口**
       （组件 `YidongFlow.vue` 已删除）。注意 `kplYidongRealtime` 接口**仍在用**：
       `composables/useYidongMonitor.js` + `/yidong` 里「交易所已公布异动」折叠表。
    —— 原「板块」的全部既有能力**一件不丢**，收进下方「板块进阶数据」折叠区：
         板块强度明细(11 列全字段 + 日期回看) / 板块轮动历史 / 人气热榜。
    —— 🔴 请求纪律：
         · 五层共用一个 60s tick（集中拉取），不各拉各的；
         · **只在盘中轮询**（isIntradayNow）—— 旧版 60s 轮询不看时段，凌晨挂着也在打接口，
           而开盘啦是 8 万次/日付费配额；
         · 所有 usePolling 注册在 **setup 顶层**（写在 onMounted 回调里会因 currentInstance
           为 null 而静默注册失败 → 定时器永不清理，v4.11.59 修过的坑）。
  -->
  <div class="page-shell">
    <h1 class="visually-hidden">{{ homeMode ? '首页盯盘台' : '盘中盯盘台' }}</h1>

    <!-- 2026-10-01 主人拍板: 手机端**首页**复用本页做「盯盘台」(见 views/StockView.vue 的 isHomeDash)。
         首页只要 10 格宫格 + 指数 + 快讯 + 最强资金 + 昨涨停今表现 + 今日战报
         => 隐藏本页标题条与题材榜，其余一字未改。 -->
    <QuickGrid v-if="homeMode" />

    <!-- 大盘温度: 指数带 + 涨跌家数/成交额（与首页同一组件，不新写图表） -->
    <SentimentPanel />

    <div v-if="!homeMode" class="mk-bar">
      <span class="mk-bar-title"><i class="fa fa-desktop"></i> 盘中盯盘台</span>
      <span class="mk-bar-sub">快讯 · 昨涨停表现 · 最强资金 · 今日战报 · 题材榜 · 异动</span>
      <button class="mk-refresh" :disabled="ticking" title="手动刷新" @click="tick()">
        <i class="fa fa-refresh" :class="{ spin: ticking }"></i>
      </button>
      <span class="mk-updated" :class="{ 'mk-stale': stale }">{{ updatedAt ? '更新于 ' + updatedAt : bjTime }}</span>
      <span v-if="stale" class="mk-stale-tag" title="超过 5 分钟未成功刷新，数据可能停滞">
        <i class="fa fa-exclamation-triangle"></i> 已超 5 分钟未更新
      </span>
    </div>

    <div class="mk-stack">
      <!-- ① 财经快讯滚动条 -->
      <FlashTicker :items="flashList" :loading="flashLoading" :degraded="flashDegraded" />

      <!-- ② 最强资金 TOP（移到快讯下） -->
      <MoneyTopStrip
        :boards="boardList" :loading="boardLoading" :failed="boardFailed"
        :active-code="hotBoard.boardCode" :active-name="hotBoard.name"
        @select="onMoneySelect"
      />

      <!-- ③ 昨日涨停今日表现 -->
      <YestZtPanel
        :count="yestCount" :avg-open="yestAvgOpen" :avg-now="yestAvgNow"
        :max-ladder="emoLadder" :broken-rate="emoBroken" :date="yestDate" :loading="yestLoading"
      />

      <!-- ④ 今日票战报 -->
      <TodayPicksPanel
        :stocks="pickRows" :summary="pickSummary" :date="pickDate" :is-today="pickIsToday"
        :loading="pickLoading" :failed="pickFailed" :empty-msg="pickEmptyMsg"
      />

      <!-- ⑤ 题材榜（首页盯盘台不显示；盘中页照旧） -->
      <MarketBoardPanel v-if="!homeMode"
        ref="boardPanelRef"
        :boards="boardRows" :src="src" :loading="boardLoading" :failed="boardFailed"
        :fail-msg="boardFailMsg" :date="datePicker"
        @update:src="switchSrc"
        @stock-click="openStockDetail"
      />

      <!-- ⑥ 实时异动流已搬到复盘页 -->
    </div>

    <!-- 个股详情弹层（带评分） -->
    <div v-if="detailCode" class="mrk-modal-mask" @click.self="detailCode=''">
      <div class="mrk-modal" style="max-width: 560px;">
        <div class="mrk-modal-head">
          <div class="mrk-modal-title"><i class="fa fa-line-chart"></i> {{ detailName }} <span class="mrk-modal-code">{{ detailCode }}</span></div>
          <button class="mrk-modal-close" @click="detailCode=''"><i class="fa fa-times"></i></button>
        </div>
        <StockDetailPanel :code="detailCode" :name="detailName" :quote="detailQuote" />
      </div>
    </div>

    <!-- 板块进阶数据(强度明细/轮动/热榜)已搬到盘前与复盘 -->


    <!-- 板块成分股弹层（层⑤与强度明细共用，按 src 选接口） -->
    <div v-if="stocksOpen" class="mrk-modal-mask" @click.self="closeBoardStocks">
      <div class="mrk-modal">
        <div class="mrk-modal-head">
          <div class="mrk-modal-title"><i class="fa fa-th-large"></i> {{ currentBoard?.name }} <span class="mrk-modal-code">{{ currentBoard?.boardCode }}</span><span class="mrk-modal-sub">成分股 Top{{ boardStocks.length }}</span></div>
          <button class="mrk-modal-close" @click="closeBoardStocks"><i class="fa fa-times"></i></button>
        </div>
        <div v-if="stocksLoading" class="loading-placeholder"><div class="spinner"></div><div>加载成分股...</div></div>
        <div v-else-if="!boardStocks.length" class="empty-state">该板块暂无成分股数据（午休时段/接口暂不可用，交易时段或收盘后重试）</div>
        <div v-else class="mrk-modal-body">
          <table class="stock-table mrk-modal-table">
            <thead>
              <tr>
                <th>代码</th>
                <th>名称</th>
                <th>最新价</th>
                <th class="sortable" :class="{ active: stockSort.keyOf('change') }" @click="stockSort.onSort('change')">涨幅%<span class="sort-ind">{{ stockSort.ind('change') }}</span></th>
                <th class="sortable" :class="{ active: stockSort.keyOf('turnover') }" @click="stockSort.onSort('turnover')">换手%<span class="sort-ind">{{ stockSort.ind('turnover') }}</span></th>
                <th class="sortable" :class="{ active: stockSort.keyOf('amount') }" @click="stockSort.onSort('amount')">成交额(亿)<span class="sort-ind">{{ stockSort.ind('amount') }}</span></th>
                <th class="sortable" :class="{ active: stockSort.keyOf('mainNet') }" @click="stockSort.onSort('mainNet')">主力净额(亿)<span class="sort-ind">{{ stockSort.ind('mainNet') }}</span></th>
                <th class="sortable" :class="{ active: stockSort.keyOf('floatMv') }" @click="stockSort.onSort('floatMv')">流通(亿)<span class="sort-ind">{{ stockSort.ind('floatMv') }}</span></th>
                <th>涨停标识</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in stockSort.sorted(boardStocks)" :key="s.code">
                <td class="code-click" @click="linkToSoftware(s.code)">{{ s.code }}</td>
                <td class="name-col"><div class="name-main">{{ s.name }}</div></td>
                <td>{{ s.price ? s.price.toFixed(2) : '-' }}</td>
                <td :class="s.change > 0 ? 'up' : s.change < 0 ? 'down' : 'dim'">{{ s.change ? signed(s.change) + '%' : '-' }}</td>
                <td>{{ s.turnover ? s.turnover.toFixed(2) : '-' }}</td>
                <td>{{ s.amount ? yi(s.amount) : '-' }}</td>
                <td :class="s.mainNet > 0 ? 'up' : s.mainNet < 0 ? 'down' : 'dim'">{{ s.mainNet ? yi(s.mainNet) : '-' }}</td>
                <td>{{ s.floatMv ? yi(s.floatMv) : '-' }}</td>
                <td><span v-if="s.limitTag || s.ladder" class="lb-badge">{{ s.limitTag || s.ladder }}</span><span v-else class="dim">-</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, reactive, watch } from 'vue'

// 2026-10-01: `homeMode` = 首页盯盘台模式（手机端首页复用本页组件；桌面端首页不受影响）。
//   只影响**模板显隐**（顶部宫格 / 标题条 / 题材榜），取数与轮询逻辑一字未动
//   => 无重复实现、无死代码，verify 的 no-unused-vars 也不会被牵连。
defineProps({ homeMode: { type: Boolean, default: false } })
import { useRoute, useRouter } from 'vue-router'
import { usePolling } from '../composables/usePolling'
import {
  kplBoardRank, kplBoardStocks, kplHotRank, sectorRotation, emBoardMembers,
  emConceptRank, kplIndexBrief, kplYestZt, kplZtEchelon,
} from '../api/kpl'
import { newsFlash } from '../api/news'
import { listBatches } from '../api/history'
import { fetchQuotes } from '../api/stocks'
import { trackUsageOnce } from '../api/activity'
import { linkToSoftware } from '../utils/tdx'
import { bjTimeStr, todayBj, isIntradayNow } from '../utils/time'
import { useSortable } from '../composables/useSortable'
import { yi, signed } from '../utils/format'
import { mergeLimitCount } from '../utils/boards'
import { summarizePicks, avgOf } from '../utils/picks'
import { pickReportBatch } from '../utils/batches'
import RotCharts from '../components/RotCharts.vue'
import PoolHoverBtn from '../components/PoolHoverBtn.vue'
import QuickGrid from '../components/QuickGrid.vue'
import SentimentPanel from '../components/SentimentPanel.vue'
import FlashTicker from '../components/FlashTicker.vue'
import YestZtPanel from '../components/YestZtPanel.vue'
import MoneyTopStrip from '../components/MoneyTopStrip.vue'
import TodayPicksPanel from '../components/TodayPicksPanel.vue'
import MarketBoardPanel from '../components/MarketBoardPanel.vue'
import StockDetailPanel from '../components/StockDetailPanel.vue'

const route = useRoute()
const router = useRouter()

// ===================== 数据源切换 =====================
// kpl = 开盘啦强度榜(原市场雷达)；em = 东财概念榜(原「题材异动」页)
// /concept 路由重定向到 /market?src=em，因此 src 以 query 为唯一真源。
const src = ref(route.query.src === 'em' ? 'em' : 'kpl')

function switchSrc(s) {
  if (s === src.value) return
  src.value = s
  // 写回 URL：既让 /concept→/market?src=em 这条链路自洽，也让切换后的地址可分享/可后退
  router.replace({ name: 'market', query: s === 'em' ? { src: 'em' } : {} })
  // 🔴 2026-09-28 v4.11.79: 原为 `if (s === 'em' && !conceptList.value.length) loadConcept()`
  //   —— 「已有缓存就不再拉」⇒ 从 kpl 切回 em 时看到的是**很久以前**那次拉的榜单
  //   （板块强度/主力净额全是旧的），用户观感就是"东财这个 tab 是坏的"。
  //   改为**每次切入都重拉**：左栏本就该跟着市场走；后端 30s TTL 缓存兜住上游压力。
  if (s === 'em') loadConcept()
}

// 支持浏览器前进/后退：query 变了要跟着切（switchSrc 自己改 query 时这里是无操作）
watch(() => route.query.src, (v) => {
  const want = v === 'em' ? 'em' : 'kpl'
  if (want !== src.value) src.value = want
})

const tab = ref('board')
const boardList = ref([])
const conceptList = ref([])
const hotList = ref([])
const hotSourceFailed = ref(false)
const boardLoading = ref(true)
const boardFailed = ref(false)
const hotLoading = ref(true)
const hotSource = ref(localStorage.getItem('kuaixuan_hot_source') || 'kpl')
const datePicker = ref('')
const boardDataDate = ref('')
const hotDataDate = ref('')
const bjTime = ref('--:--:--')
const updatedAt = ref('')
const updatedMs = ref(0)   // 最近一次成功 tick 的时刻(ms)，供数据新鲜度超时检测
const stale = ref(false)   // 盘中超过 5 分钟未成功刷新 → 标灰 + 警告
let staleTimer = null

// 各表独立排序实例
const boardSort = useSortable()
// 板块成分股弹层（两个数据源共用）
const stocksOpen = ref(false)
const stocksLoading = ref(false)
const currentBoard = ref(null)
const boardStocks = ref([])
const stockSort = useSortable()
const hotSort = useSortable()

// 切 Tab 清排序
function switchTab(t) {
  tab.value = t
  boardSort.clear(); hotSort.clear()
}

async function loadBoard() {
  try {
    const d = await kplBoardRank(datePicker.value)
    boardList.value = d.list || []
    boardDataDate.value = d.date || ''
    boardFailed.value = false
  } catch (e) { boardFailed.value = true } finally {
    boardLoading.value = false
  }
}

/** 东财概念榜（层⑤ em 源的数据；每页只拉一次，之后靠 tick 里的 loadBoard 分支复用） */
async function loadConcept() {
  try {
    const d = await emConceptRank()
    conceptList.value = d.list || []
    boardFailed.value = false
  } catch (e) { boardFailed.value = true }
}

/**
 * 打开成分股弹层。两个数据源共用同一个弹层，按 src 选对应接口：
 *   kpl → /api/kpl/board-stocks（含主力净额/涨停标识）
 *   em  → /api/kpl/em-board-members（只有现价/涨跌/换手/成交额/自由流通市值）
 * 字段缺失的那几列在弹层里显示「-」，这是数据源本身的差异，不是 bug。
 */
async function openBoard(b) {
  currentBoard.value = b
  stocksOpen.value = true
  stocksLoading.value = true
  stockSort.clear()
  try {
    const d = src.value === 'em'
      ? await emBoardMembers(b.boardCode)
      : await kplBoardStocks(b.boardCode, datePicker.value)
    boardStocks.value = d.list || []
  } catch (e) { boardStocks.value = [] } finally {
    stocksLoading.value = false
  }
}

function closeBoardStocks() {
  stocksOpen.value = false
  boardStocks.value = []
  currentBoard.value = null
}

function clearDate(which) {
  datePicker.value = ''
  if (which === 'board') { boardLoading.value = true; loadBoard() }
  else { hotLoading.value = true; loadHot() }
}

async function loadHot() {
  hotLoading.value = true
  try {
    const d = await kplHotRank(hotSource.value, datePicker.value)
    hotList.value = d.list || []
    hotDataDate.value = d.date || ''
    hotSourceFailed.value = !!(d && d.source_failed)
  } catch (e) { hotSourceFailed.value = false } finally {
    hotLoading.value = false
  }
}

function switchHotSource(src2) {
  if (src2 === hotSource.value) return
  hotSource.value = src2
  localStorage.setItem('kuaixuan_hot_source', src2)
  hotLoading.value = true
  loadHot()
}

// ===================== 板块轮动历史 =====================
const rotDays = ref(10)
const rotSource = ref(localStorage.getItem('kuaixuan_sector_source') || 'kpl')
const sourceOptions = [
  // 切换按钮不暴露第三方厂商名(非官方数据源, 页面不体现来源)
  { key: 'kpl', label: '源1', icon: 'fa fa-bullseye' },
  { key: 'em',  label: '源2', icon: 'fa fa-bar-chart' },
  { key: 'ths', label: '源3', icon: 'fa fa-line-chart' },
]
const rotLoading = ref(false)
const rotSourceFailed = ref(false)
const rot = reactive({ dates: [], days: [], windows: [], common_names: [], source: 'kpl' })

const historyDates = computed(() => [...rot.dates].reverse())

const rotMap = computed(() => {
  const m = {}
  for (const day of rot.days) {
    m[day.date] = day.boards || []
  }
  return m
})
// 出现 >= 2 次的板块按频次降序分配 8 色(红/橙/黄/靛蓝/天蓝/深蓝/紫/粉, 无绿系)
const colorMap = computed(() => {
  const cnt = {}
  for (const day of rot.days) {
    for (const b of day.boards || []) {
      cnt[b.name] = (cnt[b.name] || 0) + 1
    }
  }
  const ranks = Object.entries(cnt)
    .filter(([, c]) => c >= 2)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
  const m = {}
  ranks.forEach(([name], i) => { m[name] = (i % 8) + 1 })
  return m
})

function boardAt(date, rank) {
  const day = rotMap.value[date] || []
  return day.filter(b => b.rank === rank)
}

async function loadHistory() {
  rotLoading.value = true
  try {
    const d = await sectorRotation(rotDays.value, rotSource.value)
    rot.source = (d && d.source) || rotSource.value
    rot.dates = (d && d.dates) || []
    rot.days = (d && d.rotation && d.rotation.days) || []
    rot.windows = (d && d.windows && d.windows.windows) || []
    rot.common_names = (d && d.windows && d.windows.common_names) || []
    rotSourceFailed.value = !!(d && d.source_failed)
  } catch (e) { rotSourceFailed.value = false }
  finally { rotLoading.value = false }
}

function switchSource(s) {
  if (s === rotSource.value) return
  rotSource.value = s
  localStorage.setItem('kuaixuan_sector_source', s)
  loadHistory()
}

// ===================== 盯盘台五层 =====================
const ticking = ref(false)

// ① 快讯
const flashList = ref([])
const flashLoading = ref(true)
const flashDegraded = ref([])
async function loadFlash() {
  try {
    const d = await newsFlash(40)
    // 盘中快讯只接财联社电报(开盘啦 doc96 即财联社, kind=kpl)
    flashList.value = (d.list || []).filter(x => x.kind === 'kpl').slice(0, 20)
    flashDegraded.value = d.degraded || []
  } catch (e) { /* 保留旧值; 首拉失败则由组件显示降级文案 */ } finally {
    flashLoading.value = false
  }
}

// ② 昨日涨停今日表现（平均高开/现溢价前端聚合; 连板高度/炸板率取情绪接口）
const yestCount = ref(0)
const yestAvgOpen = ref(null)
const yestAvgNow = ref(null)
const yestDate = ref('')
const yestLoading = ref(true)
const emoLadder = ref(null)
const emoBroken = ref(null)
async function loadYestZt() {
  try {
    const d = await kplYestZt()
    const lst = d.list || []
    yestCount.value = lst.length
    yestDate.value = d.date || ''
    yestAvgOpen.value = avgOf(lst.map(it => it.bidChange))
    yestAvgNow.value = avgOf(lst.map(it => it.change))
  } catch (e) { /* 非会员/源暂缺 → 保持 null, 组件显示 -- */ } finally {
    yestLoading.value = false
  }
}
async function loadEmo() {
  try {
    const d = await kplIndexBrief()
    const e = d && d.emo
    if (!e) return
    if (e.l17 !== undefined && e.l17 !== null) emoLadder.value = Number(e.l17)
    if (e.fp108 !== undefined && e.fp108 !== null) emoBroken.value = Number(e.fp108)
  } catch (e) { /* 保留旧值 */ }
}

// ⑤ 涨停数（题材分组，与板块榜按名称归一化合并）
const ztBoards = ref([])
async function loadZtEchelon() {
  try {
    const d = await kplZtEchelon()
    ztBoards.value = (d && d.boards) || []
  } catch (e) { /* 匹配不上就显示 — */ }
}
const boardRows = computed(() => mergeLimitCount(
  src.value === 'em' ? conceptList.value : boardList.value,
  ztBoards.value,
))
const boardFailMsg = computed(() => (boardFailed.value
  ? '数据源暂不可用，可切换另一个源查看'
  : ''))

// ③ 最强资金点卡 → 联动层⑤（工单二期体验 5）
const hotBoard = ref({ name: '', boardCode: '' })
const boardPanelRef = ref(null)
const detailCode = ref('')
const detailName = ref('')
const detailQuote = ref(null)
function openStockDetail(s) {
  detailCode.value = s.code
  detailName.value = s.name || ''
  detailQuote.value = s
}
function onMoneySelect(b) {
  hotBoard.value = { name: (b && b.name) || '', boardCode: (b && b.boardCode) || '' }
  // 联动：下面板块列表自动选中该板块
  if (b && boardPanelRef.value && boardRows.value) {
    const found = boardRows.value.find(x => (x.boardCode||x.code) === (b.boardCode||b.code) || x.name === b.name)
    if (found) boardPanelRef.value.select(found)
  }
  nextTick(() => {
    const el = document.querySelector('.mb-row.hot')
    if (el && el.scrollIntoView) el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  })
}

// ④ 今日票战报
const pickRows = ref([])
const pickDate = ref('')
const pickIsToday = ref(false)
const pickPending = ref(false)
const pickLoading = ref(true)
const pickFailed = ref(false)
const pickSummary = computed(() => summarizePicks(pickRows.value))
const pickEmptyMsg = computed(() => (pickPending.value
  ? '今日名单尚未生成（9:25 定格落库后自动出现）'
  : ''))
async function loadPicks() {
  try {
    const d = await listBatches()
    const { batch, isToday, pending } = pickReportBatch(d.batches || [], todayBj())
    pickPending.value = pending
    pickIsToday.value = isToday
    if (!batch) { pickRows.value = []; pickDate.value = ''; pickFailed.value = false; return }
    const detail = await listBatches(batch.id)
    const rows = detail.stocks || []
    pickDate.value = batch.batch_date || ''
    let qmap = {}
    const codes = rows.map(s => s.code).filter(Boolean)
    if (codes.length) {
      try {
        const q = await fetchQuotes(codes)
        qmap = (q && q.quotes) || {}
      } catch (e) { qmap = {} }
    }
    pickRows.value = rows.map((s) => {
      const q = qmap[s.code] || {}
      const chg = (q.realChange !== undefined && q.realChange !== null) ? q.realChange
        : (s.realChange !== undefined ? s.realChange : null)
      return {
        code: s.code,
        name: s.name,
        price: (q.price !== undefined && q.price !== null) ? q.price : (s.price ?? null),
        change: chg,
        // 数据源当前不提供当日最高涨幅 ⇒ 留 undefined，utils/picks 据此判「炸板不可知」
        peakChange: undefined,
      }
    })
    pickFailed.value = false
  } catch (e) { pickFailed.value = true } finally {
    pickLoading.value = false
  }
}

// ⑥ 实时异动流已搬到复盘页（`/yidong` 置顶）——
// 🔴 2026-09-28 死代码清理：旧版这里**只删了模板**却留着 `yidongList` 状态、
//    `loadYidong()` 函数与 30 秒轮询调用，且保留 `kplYidongRealtime` / `YidongFlow` 两个
//    未使用的 import ⇒ 每分钟白打一次开盘啦接口（8 万次/日付费配额）且 ESLint no-unused 报警。
//    已整块删除；⚠️ 2026-09-28 晚些 `/yidong` 置顶的那块也按主人要求去掉 ⇒ 该功能全站已无入口。

/**
 * 五层统一 tick：一轮把上游集中拉完，只打一次「更新于」。
 * ⚠️ datePicker 有值（用户在回看历史）时不刷新板块实时数据，避免手动选的日期被 tick 冲掉。
 * ★ 2026-09-28：原为「六层」（含 loadYidong），该层已整块移除（模板早删、代码是死代码）⇒ 改「五层」。
 */
async function tick() {
  if (ticking.value) return
  ticking.value = true
  try {
    const jobs = [
      loadFlash(), loadYestZt(), loadEmo(), loadZtEchelon(), loadPicks(),
      src.value === 'em' ? loadConcept() : Promise.resolve(),
    ]
    if (!datePicker.value) jobs.push(loadBoard())
    await Promise.allSettled(jobs)
    updatedAt.value = bjTimeStr()
    updatedMs.value = Date.now()
    stale.value = false
  } finally {
    ticking.value = false
  }
}

// ⚠️ 2026-09-27 v4.11.59/62 修: usePolling 必须注册在 setup 顶层 ——
//    Vue 调用 mounted 回调时 currentInstance 为 null, 写进 onMounted 里会静默注册失败
//    ⇒ 定时器与 visibilitychange 监听永不清理。首拉由下面 onMounted 显式完成,
//    故此处 immediate:false(顺带消掉「默认首跳 + onMounted 显式拉」的首屏双请求)。
usePolling(() => { bjTime.value = bjTimeStr() }, 1000, { immediate: false })
// 🔴 只在盘中轮询: 旧版不看时段, 凌晨挂着也在打「板块强度 + 人气榜」(开盘啦 8 万次/日付费配额)
usePolling(() => { if (isIntradayNow()) tick() }, 60000, { immediate: false })

// 数据新鲜度超时检测(2026-09-27 收尾, 总工单批次五 P0): 仅盘中(isIntradayNow)判定,
// 超过 5 分钟未成功 tick → stale=true, 页头「更新于」标灰 + 警告
function tickStale() {
  stale.value = isIntradayNow() && updatedMs.value > 0 && (Date.now() - updatedMs.value) > 5 * 60 * 1000
}
onMounted(() => {
  bjTime.value = bjTimeStr()
  // 2026-09-22 v4.11.35: 市场雷达打开即算一次(Once 版, 组件内 60s 轮询不重复上报)
  trackUsageOnce('market')
  tick()
  loadHistory()
  loadHot()
  if (src.value === 'em') loadConcept()
  staleTimer = setInterval(tickStale, 30000)
  tickStale()
})
onBeforeUnmount(() => { if (staleTimer) clearInterval(staleTimer) })
</script>

<style scoped>
/* ===================== 盯盘台骨架 ===================== */
.mk-bar {
  display: flex; align-items: center; gap: var(--s2);
  margin: var(--s2) 0 var(--s2); flex-wrap: wrap;
}
.mk-bar-title { color: var(--warn-text); font-size: var(--fs-lg); font-weight: 700; }
.mk-bar-title .fa { color: var(--star); }
.mk-bar-sub { color: var(--text-muted); font-size: var(--fs-xs); }
.mk-refresh {
  margin-left: auto; background: transparent; border: 1px solid var(--border-soft);
  color: var(--text-secondary); border-radius: var(--r-md); padding: var(--s1) var(--s2); cursor: pointer; font-size: var(--fs-xs);
}
.mk-refresh:hover { border-color: var(--accent); color: var(--text-main); }
.mk-refresh:disabled { opacity: 0.5; cursor: default; }
.mk-updated { color: var(--text-dim); font-size: var(--fs-xs); font-variant-numeric: tabular-nums; }
.mk-updated.mk-stale { opacity: 0.55; color: var(--text-muted); }
.mk-stale-tag {
  display: inline-flex; align-items: center; gap: var(--s1);
  color: var(--warn-amber); font-weight: 600; font-size: var(--fs-xs); white-space: nowrap;
}
.spin { animation: spin 0.8s linear infinite; display: inline-block; }

/* 五层竖排，层间距 8px（工单「卡片间距 8px」） */
.mk-stack { display: flex; flex-direction: column; gap: var(--s2); }

/* ===================== 进阶区折叠 ===================== */
.mk-more { margin-top: var(--s4); border-top: 1px solid var(--border-soft); padding-top: var(--s2); }
.mk-more-sum {
  cursor: pointer; color: var(--text-secondary); font-size: var(--fs-sm);
  padding: var(--s1) 0; list-style: none; user-select: none;
}
.mk-more-sum::-webkit-details-marker { display: none; }
.mk-more-sum .fa { color: var(--accent); margin-right: var(--s1); transition: transform 0.15s; }
.mk-more[open] .mk-more-sum .fa-caret-down { transform: rotate(0deg); }
.mk-more:not([open]) .mk-more-sum .fa-caret-down { transform: rotate(-90deg); }

/* ===================== 板块（沿用既有样式） ===================== */
.mrk-head { display: flex; align-items: baseline; gap: var(--s3); flex-wrap: wrap; margin-bottom: var(--s3); }
.mrk-title { font-size: var(--fs-2xl); font-weight: 700; color: var(--warn-text); }
.mrk-title .fa { color: var(--star); }
.mrk-sub { color: var(--text-muted); font-size: var(--fs-sm); }
.mrk-time { margin-left: auto; color: var(--text-dim); font-size: var(--fs-base); font-variant-numeric: tabular-nums; }
.mrk-tabs { display: flex; gap: var(--s2); margin-bottom: var(--s4); }
.mrk-tab {
  padding: var(--s2) var(--s4); border-radius: var(--r-md); border: 1px solid var(--border-soft);
  background: var(--bg-subtle); color: var(--text-secondary); font-size: var(--fs-base); cursor: pointer; transition: border-color 0.2s, color 0.2s;
}
.mrk-tab:hover { border-color: var(--star); color: var(--warn-text); }
.mrk-tab.active { background: rgba(255,180,0,0.15); border-color: var(--star); color: var(--gold); font-weight: 600; }
.mrk-panel { background: var(--bg-subtle); border: 1px solid var(--border-soft); border-radius: var(--r-lg); padding: var(--s4); }
.loading-placeholder { text-align: center; padding: var(--s8); color: var(--text-muted); }
.spinner { width: 28px; height: 28px; border: 3px solid rgba(255,180,0,0.3); border-top-color: var(--star); border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 10px; }
@keyframes spin { to { transform: rotate(360deg); } }
.empty-state { text-align: center; padding: var(--s8); color: var(--text-muted); }
.empty-state.src-fail { color: var(--star); }
.empty-state.src-fail i { margin-right: var(--s2); }
.board-row { cursor: pointer; }
.board-row:hover td { background: rgba(255, 180, 0, 0.06); }
.board-detail-hint {
  display: inline-flex; align-items: center; gap: var(--s1);
  font-size: var(--fs-xs); color: var(--accent); opacity: 0.85; margin-left: var(--s2);
  border: 1px solid rgba(var(--accent-rgb), 0.4); border-radius: var(--r-lg); padding: 0 var(--s2);
}
.board-code { font-size: var(--fs-xs); color: var(--text-muted); }
.strength { color: var(--star); font-weight: 700; }
.lb-badge { display: inline-block; color: var(--up); border: 1px solid rgba(255,80,40,0.5); border-radius: var(--r-sm); padding: 0 var(--s1); font-size: var(--fs-xs); background: rgba(255,80,40,0.12); }
.name-col { white-space: nowrap; }

/* 成分股弹层 */
.mrk-modal-mask {
  position: fixed; inset: 0; z-index: 2000;
  background: rgba(0, 0, 0, 0.55);
  display: flex; align-items: center; justify-content: center; padding: var(--s5);
}
.mrk-modal {
  background: var(--bg-panel);
  border: 1px solid rgba(var(--accent-rgb), 0.4);
  border-radius: var(--r-lg);
  max-width: 980px; width: 100%;
  max-height: 80vh;
  display: flex; flex-direction: column;
  box-shadow: var(--sh-3);
}
.mrk-modal-head {
  display: flex; align-items: center; gap: var(--s2);
  padding: var(--s3) var(--s4); border-bottom: 1px solid var(--border-soft);
}
.mrk-modal-title { font-size: var(--fs-lg); font-weight: 700; color: var(--text-main); }
.mrk-modal-title .fa { color: var(--accent); }
.mrk-modal-code { font-size: var(--fs-sm); color: var(--text-muted); margin-left: var(--s2); }
.mrk-modal-sub { font-size: var(--fs-xs); color: var(--text-muted); margin-left: var(--s2); }
.mrk-modal-close {
  margin-left: auto; background: transparent; border: none;
  color: var(--text-muted); font-size: var(--fs-xl); cursor: pointer; padding: var(--s1) var(--s2);
}
.mrk-modal-close:hover { color: var(--text-main); }
.mrk-modal-body { overflow-y: auto; padding: var(--s2) var(--s4) var(--s4); }
.mrk-modal-table { min-width: 640px; }

/* ===================== 板块轮动历史 ===================== */
.rot-toolbar { display: flex; align-items: center; gap: var(--s2); margin-bottom: var(--s2); flex-wrap: wrap; }
.rot-tip { color: var(--text-muted, var(--text-muted)); font-size: var(--fs-xs); flex: 1; min-width: 0; }
.rot-source { display: flex; gap: 0; border-radius: var(--r-md); overflow: hidden; border: 1px solid var(--border-soft, var(--text-faint)); }
.rot-source-btn { background: var(--bg-input, #1a1a1a); color: var(--text-secondary, var(--text-muted)); border: none; padding: var(--s1) var(--s3); font-size: var(--fs-xs); cursor: pointer; transition: background 0.15s; }
.rot-source-btn:hover { background: var(--bg-card, #222); }
.rot-source-btn.active { background: var(--accent-warm, var(--star)); color: #1a1a1a; font-weight: 600; }
.rot-table-scroll { overflow-x: auto; border: 1px solid var(--border-soft); border-radius: var(--r-md); }
.rot-table { border-collapse: collapse; min-width: 100%; font-size: var(--fs-xs); }
.rot-table th, .rot-table td { padding: var(--s1) var(--s2); text-align: center; border-bottom: 1px solid var(--border-soft); white-space: nowrap; }
.rot-table th { background: var(--bg-subtle); color: var(--text-secondary); font-weight: 500; position: sticky; top: 0; }
.rot-rownum { color: var(--text-muted); font-size: var(--fs-xs); min-width: 40px; }
.rot-date { color: var(--text-secondary); font-size: var(--fs-xs); min-width: 70px; }
.rot-cell { min-width: 80px; padding: var(--s1) var(--s1) !important; vertical-align: middle; }
.rot-board { font-size: var(--fs-xs); color: var(--text-main); border-radius: var(--r-sm); padding: 1px var(--s2); display: inline-block; }
.rot-board.rot-c-0 { color: var(--text-main); background: transparent; }
.rot-c-1 { color: #fff; background: #E24B4A; } .rot-c-2 { color: #fff; background: #F08C3F; }
.rot-c-3 { color: #222; background: #E6BE2A; } .rot-c-4 { color: #fff; background: #5C6BC0; }
.rot-c-5 { color: #fff; background: #38A6DF; } .rot-c-6 { color: #fff; background: #2851A8; }
.rot-c-7 { color: #fff; background: #9A57C9; } .rot-c-8 { color: #fff; background: #D45B92; }
.rot-strength { font-size: var(--fs-xs); color: var(--text-muted); margin-top: 1px; }

/* 移动端适配(<=768px) */
@media (max-width: 768px) {
  .mk-bar-sub { display: none; }
  .mk-more .mrk-panel { overflow-x: auto; -webkit-overflow-scrolling: touch; padding: var(--s2) var(--s2); }
  .mk-more .mrk-panel .stock-table { min-width: 880px; }
  .rot-table-scroll .rot-table { min-width: 680px; }
  .mrk-tabs { flex-wrap: nowrap; overflow-x: auto; -webkit-overflow-scrolling: touch; scrollbar-width: none; padding-bottom: var(--s1); }
  .mrk-tabs::-webkit-scrollbar { display: none; }
  .mrk-tab { flex-shrink: 0; white-space: nowrap; padding: var(--s2) var(--s3); font-size: var(--fs-sm); }
  .mrk-head { gap: var(--s2); }
  .mrk-title { font-size: var(--fs-lg); }
  .mrk-sub { font-size: var(--fs-xs); width: 100%; }
  .mrk-time { margin-left: 0; font-size: var(--fs-xs); }
  .mk-more .mrk-panel .stock-table th { padding: var(--s2) var(--s1); font-size: var(--fs-xs); }
  .mk-more .mrk-panel .stock-table td { padding: var(--s2) var(--s1); font-size: var(--fs-xs); }
  .mrk-modal { max-height: 86vh; }
}

/* 浅色主题覆盖 */
body[data-bg="light"] .mk-bar-title { color: #8a5500; }
body[data-bg="light"] .mrk-title { color: #8a5500; }
body[data-bg="light"] .mrk-title .fa { color: var(--gold-deep); }
body[data-bg="light"] .mrk-sub, body[data-bg="light"] .mrk-time { color: var(--text-faint); }
body[data-bg="light"] .mrk-tab { color: var(--text-faint); background: rgba(255,255,255,0.6); }
body[data-bg="light"] .mrk-tab:hover { color: var(--watermark); border-color: var(--gold-deep); }
body[data-bg="light"] .mrk-tab.active { color: var(--watermark); background: rgba(255,180,0,0.15); border-color: var(--gold-deep); }
body[data-bg="light"] .mrk-panel { background: rgba(255,255,255,0.85); }
body[data-bg="light"] .strength { color: #8a5500; }
body[data-bg="light"] .lb-badge { color: var(--brand-deep); border-color: rgba(184,48,16,0.5); background: rgba(255,80,80,0.1); }
body[data-bg="light"] .board-code { color: #1a1d26; }
body[data-bg="light"] .board-detail-hint { color: #a06a00; border-color: rgba(160, 106, 0, 0.4); }
body[data-bg="light"] .board-row:hover td { background: rgba(199, 145, 0, 0.08); }
body[data-bg="light"] .mrk-modal-title { color: #1a1d26; }
body[data-bg="light"] .rot-source { border-color: #d0d0d0; }
body[data-bg="light"] .rot-source-btn { background: #f5f5f5; color: #555; }
body[data-bg="light"] .rot-source-btn:hover { background: #eaeaea; }
body[data-bg="light"] .rot-source-btn.active { background: #d97b00; color: #fff; }
body[data-bg="light"] .rot-c-1 { background: #C32D2C; }
body[data-bg="light"] .rot-c-2 { background: #D86A1B; }
body[data-bg="light"] .rot-c-3 { color: #4a3a00; background: #F0CB3F; }
body[data-bg="light"] .rot-c-4 { background: #3F51B5; }
body[data-bg="light"] .rot-c-5 { background: #1E7FB5; }
body[data-bg="light"] .rot-c-6 { background: #1D3F8C; }
body[data-bg="light"] .rot-c-7 { background: #6B2B9A; }
body[data-bg="light"] .rot-c-8 { background: #A82C6C; }
</style>
