<template>
  <!--
    板块页（2026-09-27 v4.11.58 前端信息架构改造 · 工单 二/三.3/三.4）
    —— 一级分组「盘中」的唯一落地面:
         · 页顶 = 大盘温度（复用 components/SentimentPanel.vue，工单 三.3「不要新写」）
         · 下方 = 板块，两个数据源可切换：「开盘啦强度榜 | 东财概念榜」
    —— 工单 三.4 方案 A: /concept（原题材异动）并入这里作为第二个数据源，
       路径保留 + 重定向到 /market?src=em，旧书签/外链不 404。
    —— 两个数据源**共用同一个「点板块展开成分股」弹层**，弹层里按 src 选对应接口。
    —— 龙虎榜已拆出为独立页 /lhb（复盘分组），本页不再为它白拉接口。
  -->
  <div class="page-shell">
    <h1 class="visually-hidden">板块</h1>

    <!-- 大盘温度: 指数带 + 涨跌家数/成交额（与首页同一组件，不新写图表） -->
    <SentimentPanel />

    <div class="mrk-head">
      <span class="mrk-title"><i class="fa fa-th-large"></i> 板块</span>
      <span class="mrk-sub">板块强度排行 · 板块轮动 · 人气热榜 · 概念异动</span>
      <span class="mrk-time">{{ bjTime }}</span>
    </div>

    <!-- 数据源切换（工单 三.4 方案 A: 在 /market 页顶部加一个数据源切换 tab） -->
    <div class="mrk-src" role="tablist" aria-label="板块数据源">
      <button
        class="mrk-src-btn" :class="{ active: src === 'kpl' }" role="tab"
        :aria-selected="src === 'kpl'" @click="switchSrc('kpl')"
      ><i class="fa fa-signal"></i> 开盘啦强度榜</button>
      <button
        class="mrk-src-btn" :class="{ active: src === 'em' }" role="tab"
        :aria-selected="src === 'em'" @click="switchSrc('em')"
      ><i class="fa fa-fire"></i> 东财概念榜</button>
    </div>

    <!-- ==================== 数据源 1: 开盘啦强度榜（原市场雷达） ==================== -->
    <template v-if="src === 'kpl'">
      <div class="mrk-tabs">
        <button class="mrk-tab" :class="{ active: tab === 'board' }" @click="switchTab('board')">
          <i class="fa fa-th-large"></i> 板块强度
        </button>
        <button class="mrk-tab" :class="{ active: tab === 'history' }" @click="switchTab('history')">
          <i class="fa fa-history"></i> 板块轮动历史
        </button>
        <button class="mrk-tab" :class="{ active: tab === 'hot' }" @click="switchTab('hot')">
          <i class="fa fa-fire"></i> 人气热榜
        </button>
      </div>

      <!-- 板块强度 -->
      <div v-if="tab === 'board'" class="mrk-panel">
        <div class="rot-toolbar">
          <span class="rot-tip"><i class="fa fa-info-circle"></i> 实时板块强度排行；选日期可回看历史</span>
          <input v-model="datePicker" type="date" class="rot-date" @change="loadBoard">
          <button class="rot-reset-btn" title="回到实时" @click="clearDate('board')"><i class="fa fa-bolt"></i></button>
          <span v-if="boardDataDate && datePicker" class="rot-data-date"><i class="fa fa-calendar"></i> 数据日期 {{ boardDataDate }}<template v-if="boardDataDate !== datePicker">（{{ datePicker }} 非交易日，自动对齐）</template></span>
        </div>
        <div v-if="boardLoading" class="loading-placeholder"><div class="spinner"></div><div>加载板块强度...</div></div>
        <div v-else-if="!boardList.length" class="empty-state">暂无板块强度数据</div>
        <table v-else class="stock-table">
          <thead>
            <tr>
              <th>排名</th>
              <th class="sortable" :class="{ active: boardSort.keyOf('name') }" @click="boardSort.onSort('name', 'string')">板块<span class="sort-ind">{{ boardSort.ind('name') }}</span></th>
              <th class="sortable" :class="{ active: boardSort.keyOf('strength') }" @click="boardSort.onSort('strength')">强度<span class="sort-ind">{{ boardSort.ind('strength') }}</span></th>
              <th class="sortable" :class="{ active: boardSort.keyOf('change') }" @click="boardSort.onSort('change')">涨幅%<span class="sort-ind">{{ boardSort.ind('change') }}</span></th>
              <th class="sortable" :class="{ active: boardSort.keyOf('speed') }" @click="boardSort.onSort('speed')">涨速%<span class="sort-ind">{{ boardSort.ind('speed') }}</span></th>
              <th class="sortable" :class="{ active: boardSort.keyOf('mainNet') }" @click="boardSort.onSort('mainNet')">主力净额(亿)<span class="sort-ind">{{ boardSort.ind('mainNet') }}</span></th>
              <th class="sortable" :class="{ active: boardSort.keyOf('volRatio') }" @click="boardSort.onSort('volRatio')">量比<span class="sort-ind">{{ boardSort.ind('volRatio') }}</span></th>
              <th class="sortable" :class="{ active: boardSort.keyOf('amount') }" @click="boardSort.onSort('amount')">成交额(亿)<span class="sort-ind">{{ boardSort.ind('amount') }}</span></th>
              <th class="sortable" :class="{ active: boardSort.keyOf('totalMv') }" @click="boardSort.onSort('totalMv')">总市值(亿)<span class="sort-ind">{{ boardSort.ind('totalMv') }}</span></th>
              <th class="sortable" :class="{ active: boardSort.keyOf('peNow') }" @click="boardSort.onSort('peNow')">今PE<span class="sort-ind">{{ boardSort.ind('peNow') }}</span></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(b, idx) in boardSort.sorted(boardList)" :key="b.boardCode" class="board-row" @click="openBoard(b)">
              <td class="rank-col">{{ idx + 1 }}</td>
              <td class="name-col"><div class="name-main">{{ b.name }}</div><div class="board-code">{{ b.boardCode }}</div><span class="board-detail-hint"><i class="fa fa-chevron-circle-right"></i> 成分股</span></td>
              <td class="strength">{{ Math.round(b.strength) }}</td>
              <td :class="b.change > 0 ? 'up' : b.change < 0 ? 'down' : 'dim'">{{ signed(b.change) }}%</td>
              <td :class="b.speed > 0 ? 'up' : b.speed < 0 ? 'down' : 'dim'">{{ signed(b.speed) }}%</td>
              <td :class="b.mainNet > 0 ? 'up' : b.mainNet < 0 ? 'down' : 'dim'">{{ yi(b.mainNet) }}</td>
              <td>{{ b.volRatio.toFixed(2) }}</td>
              <td>{{ yi(b.amount) }}</td>
              <td>{{ yi(b.totalMv) }}</td>
              <td class="dim">{{ b.peNow ? b.peNow.toFixed(1) : '-' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 板块轮动历史 -->
      <div v-else-if="tab === 'history'" class="mrk-panel">
        <div class="rot-toolbar">
          <span class="rot-tip"><i class="fa fa-info-circle"></i> 工作日 15:30 后自动保存当日 Top10；近期数据积累后展示趋势</span>
          <div class="rot-source">
            <button
              v-for="s in sourceOptions" :key="s.key"
              :class="{ active: rotSource === s.key }"
              class="rot-source-btn"
              @click="switchSource(s.key)"
            >
              <i :class="s.icon"></i> {{ s.label }}
            </button>
          </div>
          <select v-model.number="rotDays" class="rot-select" @change="loadHistory">
            <option :value="10">近 10 日</option>
            <option :value="20">近 20 日</option>
            <option :value="30">近 30 日</option>
            <option :value="50">近 50 日</option>
          </select>
          <button class="rot-reset-btn" title="刷新" aria-label="刷新" @click="loadHistory"><i class="fa fa-refresh"></i></button>
        </div>
        <div v-if="rotLoading" class="loading-placeholder"><div class="spinner"></div></div>
        <div v-else-if="rotSourceFailed" class="empty-state src-fail">
          <i class="fa fa-exclamation-triangle"></i> 数据源故障（源{{ rotSource === 'kpl' ? 1 : rotSource === 'em' ? 2 : 3 }}暂不可用），请切换其他源查看
        </div>
        <div v-else-if="!rot.dates.length" class="empty-state">
          暂无历史数据(每日 15:30 后调度器抓取积累)
        </div>
        <template v-else>
          <!-- 顶部表格: 行=排名, 列=日期 -->
          <div class="rot-table-scroll">
            <table class="rot-table">
              <thead>
                <tr>
                  <th class="rot-rownum">排名</th>
                  <th v-for="d in historyDates" :key="d" class="rot-date">{{ d.slice(5) }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="rank in 10" :key="rank">
                  <td class="rot-rownum">{{ rank }}</td>
                  <td v-for="d in historyDates" :key="d+rank" class="rot-cell">
                    <template v-for="b in boardAt(d, rank)" :key="b.name">
                      <div :class="['rot-board', 'rot-c-' + (colorMap[b.name] || 0)]">{{ b.name }}</div>
                      <div class="rot-strength">{{ Math.round(b.strength) }}</div>
                    </template>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <!-- 强度趋势线 + 量能柱状 + 多窗口排名(独立组件) -->
          <RotCharts :dates="rot.dates" :rot-map="rotMap" :windows="rot.windows" :common-names="rot.common_names" />
        </template>
      </div>

      <!-- 人气热榜 -->
      <div v-else class="mrk-panel">
        <div class="rot-toolbar">
          <span class="rot-tip"><i class="fa fa-info-circle"></i> 人气热榜；选日期可回看历史</span>
          <div class="rot-source">
            <button
              v-for="s in sourceOptions" :key="s.key"
              :class="{ active: hotSource === s.key }"
              class="rot-source-btn"
              @click="switchHotSource(s.key)"
            >
              <i :class="s.icon"></i> {{ s.label }}
            </button>
          </div>
          <input v-model="datePicker" type="date" class="rot-date" @change="loadHot">
          <button class="rot-reset-btn" title="回到实时" @click="clearDate('hot')"><i class="fa fa-bolt"></i></button>
          <span v-if="hotDataDate && datePicker" class="rot-data-date"><i class="fa fa-calendar"></i> 数据日期 {{ hotDataDate }}<template v-if="hotDataDate !== datePicker">（{{ datePicker }} 非交易日，自动对齐）</template></span>
        </div>
        <div v-if="hotLoading" class="loading-placeholder"><div class="spinner"></div><div>加载人气热榜...</div></div>
        <div v-else-if="hotSourceFailed" class="empty-state src-fail">
          <i class="fa fa-exclamation-triangle"></i> 数据源故障（源{{ hotSource === 'kpl' ? 1 : hotSource === 'em' ? 2 : 3 }}暂不可用），请切换其他源查看
        </div>
        <div v-else-if="!hotList.length" class="empty-state">暂无热榜数据</div>
        <table v-else class="stock-table">
          <thead>
            <tr>
              <th class="sortable" :class="{ active: hotSort.keyOf('rank') }" @click="hotSort.onSort('rank')">人气排名<span class="sort-ind">{{ hotSort.ind('rank') }}</span></th>
              <th class="sortable" :class="{ active: hotSort.keyOf('code') }" @click="hotSort.onSort('code', 'string')">代码<span class="sort-ind">{{ hotSort.ind('code') }}</span></th>
              <th class="sortable" :class="{ active: hotSort.keyOf('name') }" @click="hotSort.onSort('name', 'string')">名称<span class="sort-ind">{{ hotSort.ind('name') }}</span></th>
              <th class="sortable" :class="{ active: hotSort.keyOf('change') }" @click="hotSort.onSort('change')">涨跌幅%<span class="sort-ind">{{ hotSort.ind('change') }}</span></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="h in hotSort.sorted(hotList)" :key="h.code">
              <td class="rank-col">{{ h.rank }}</td>
              <td class="code-click" @click="linkToSoftware(h.code)">{{ h.code }}</td>
              <td><span class="pool-hover-wrap"><span>{{ h.name }}</span><PoolHoverBtn :item="h" /></span></td>
              <td :class="h.change > 0 ? 'up' : h.change < 0 ? 'down' : 'dim'">{{ signed(h.change) }}%</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <!-- ==================== 数据源 2: 东财概念榜（原「题材异动」页） ==================== -->
    <div v-else class="mrk-panel mrk-panel-em">
      <EmConceptPanel @select="openBoard" />
    </div>

    <!-- 板块成分股弹层（2026-08-17 主人需求）：两个数据源共用同一弹层，按 src 选接口 -->
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
import { computed, onMounted, ref, reactive, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { usePolling } from '../composables/usePolling'
import { kplBoardRank, kplBoardStocks, kplHotRank, sectorRotation, emBoardMembers } from '../api/kpl'
import { trackUsageOnce } from '../api/activity'
import { linkToSoftware } from '../utils/tdx'
import { bjTimeStr } from '../utils/time'
import { useSortable } from '../composables/useSortable'
import { yi, signed } from '../utils/format'
import RotCharts from '../components/RotCharts.vue'
import PoolHoverBtn from '../components/PoolHoverBtn.vue'
import SentimentPanel from '../components/SentimentPanel.vue'
import EmConceptPanel from '../components/EmConceptPanel.vue'

const route = useRoute()
const router = useRouter()

// ===================== 数据源切换（工单 三.4 方案 A） =====================
// kpl = 开盘啦强度榜（原市场雷达三 tab）；em = 东财概念榜（原「题材异动」页）
// /concept 路由重定向到 /market?src=em，因此 src 以 query 为唯一真源。
const src = ref(route.query.src === 'em' ? 'em' : 'kpl')

function switchSrc(s) {
  if (s === src.value) return
  src.value = s
  // 写回 URL：既让 /concept→/market?src=em 这条链路自洽，也让切换后的地址可分享/可后退
  router.replace({ name: 'market', query: s === 'em' ? { src: 'em' } : {} })
}

// 支持浏览器前进/后退：query 变了要跟着切（switchSrc 自己改 query 时这里是无操作）
watch(() => route.query.src, (v) => {
  const want = v === 'em' ? 'em' : 'kpl'
  if (want !== src.value) src.value = want
})

const tab = ref('board')
const boardList = ref([])
const hotList = ref([])
const hotSourceFailed = ref(false)
const boardLoading = ref(true)
const hotLoading = ref(true)
const hotSource = ref(localStorage.getItem('kuaixuan_hot_source') || 'kpl')
const datePicker = ref('')
const boardDataDate = ref('')
const hotDataDate = ref('')
const bjTime = ref('--:--:--')

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
  } catch (e) { /* 静默 */ } finally {
    boardLoading.value = false
  }
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

// 板块历史表格列顺序: 从右到左天数递增 → 改为从左往右最新一天(左侧最新, 用于表格);
// 趋势/量能等图表仍用 rot.dates(时间左旧右新, 自然流向)
const historyDates = computed(() => [...rot.dates].reverse())

const rotMap = computed(() => {
  const m = {}
  for (const day of rot.days) {
    m[day.date] = day.boards || []
  }
  return m
})
// 出现 >= 2 次的板块按频次降序分配 8 色(红/橙/黄/靛蓝/天蓝/深蓝/紫/粉, 无绿系),
// 同板块多日同色; 出现 < 2 次不配色(rot-c-0)
const colorMap = computed(() => {
  const cnt = {}
  for (const day of rot.days) {
    for (const b of day.boards || []) {
      cnt[b.name] = (cnt[b.name] || 0) + 1
    }
  }
  const ranks = Object.entries(cnt)
    .filter(([_, c]) => c >= 2)
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
  } catch (e) { rotSourceFailed.value = false } /* 请求失败: 按无故障处理(旧值保留即可) */
  finally { rotLoading.value = false }
}

function switchSource(s) {
  if (s === rotSource.value) return
  rotSource.value = s
  localStorage.setItem('kuaixuan_sector_source', s)
  loadHistory()
}

onMounted(() => {
  bjTime.value = bjTimeStr()
  usePolling(() => { bjTime.value = bjTimeStr() }, 1000, { immediate: false })
  // 2026-09-22 v4.11.35: 市场雷达打开即算一次(Once 版, 组件内 60s 轮询不重复上报)
  trackUsageOnce('market')
  loadBoard()
  loadHistory()
  loadHot()
  usePolling(() => { loadBoard(); loadHot() }, 60000)
})
</script>

<style scoped>
/* ===================== 数据源切换（工单 三.4 方案 A） ===================== */
.mrk-src { display: inline-flex; gap: 0; border-radius: 8px; overflow: hidden; border: 1px solid var(--border-soft); margin-bottom: 12px; }
.mrk-src-btn {
  display: inline-flex; align-items: center; gap: 6px;
  background: var(--bg-input); color: var(--text-secondary); border: none;
  padding: 8px 18px; font-size: 0.875rem; cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.mrk-src-btn:hover { background: var(--bg-card); color: var(--text-main); }
.mrk-src-btn.active { background: rgba(255, 180, 0, 0.15); color: #ffd700; font-weight: 700; box-shadow: inset 0 -2px 0 var(--accent); }
body[data-bg="light"] .mrk-src { border-color: #d0d0d0; }
body[data-bg="light"] .mrk-src-btn { background: #f5f5f5; color: #555; }
body[data-bg="light"] .mrk-src-btn:hover { background: #eaeaea; color: #1a1d26; }
body[data-bg="light"] .mrk-src-btn.active { background: rgba(198, 40, 40, 0.10); color: #c62828; }

/* 板块行点击态(2026-08-17 主人需求: 点板块看成分股) */
.board-row { cursor: pointer; }
.board-row:hover td { background: rgba(255, 180, 0, 0.06); }
.board-click { cursor: pointer; }
.board-click .name-main:hover { color: #ffb400; }
.board-detail-hint {
  display: inline-flex; align-items: center; gap: 3px;
  font-size: 0.75rem; color: var(--accent); opacity: 0.85; margin-left: 6px;
  border: 1px solid rgba(var(--accent-rgb), 0.4); border-radius: 10px; padding: 0 6px;
}
/* 成分股弹层 */
.mrk-modal-mask {
  position: fixed; inset: 0; z-index: 2000;
  background: rgba(0, 0, 0, 0.55);
  display: flex; align-items: center; justify-content: center; padding: 20px;
}
.mrk-modal {
  background: var(--bg-panel);
  border: 1px solid rgba(var(--accent-rgb), 0.4);
  border-radius: 12px;
  max-width: 980px; width: 100%;
  max-height: 80vh;
  display: flex; flex-direction: column;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.45);
}
.mrk-modal-head {
  display: flex; align-items: center; gap: 10px;
  padding: 12px 16px; border-bottom: 1px solid var(--border-soft);
}
.mrk-modal-title { font-size: 1.0625rem; font-weight: 700; color: var(--text-main); }
.mrk-modal-title .fa { color: var(--accent); }
.mrk-modal-code { font-size: 0.8125rem; color: var(--text-muted); font-family: inherit; margin-left: 6px; }
.mrk-modal-sub { font-size: 0.75rem; color: var(--text-muted); margin-left: 8px; }
.mrk-modal-close {
  margin-left: auto; background: transparent; border: none;
  color: var(--text-muted); font-size: 1.125rem; cursor: pointer; padding: 4px 8px;
}
.mrk-modal-close:hover { color: var(--text-main); }
.mrk-modal-body { overflow-y: auto; padding: 10px 14px 14px; }
.mrk-modal-table { min-width: 640px; }
body[data-bg="light"] .mrk-modal-title { color: #1a1d26; }
body[data-bg="light"] .board-detail-hint { color: #a06a00; border-color: rgba(160, 106, 0, 0.4); }
body[data-bg="light"] .board-row:hover td { background: rgba(199, 145, 0, 0.08); }
.page-back { color: var(--text-muted); cursor: pointer; font-size: 0.8125rem; margin-bottom: 12px; display: inline-block; }
.page-back:hover { color: #ffb400; }
.mrk-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.mrk-title { font-size: 1.25rem; font-weight: 700; color: #ffe0a0; }
.mrk-title .fa { color: #ffb400; }
.mrk-sub { color: var(--text-muted); font-size: 0.8125rem; }
.mrk-time { margin-left: auto; color: var(--text-dim); font-size: 0.875rem; font-family: inherit; font-variant-numeric: tabular-nums; }
.mrk-tabs { display: flex; gap: 8px; margin-bottom: 14px; }
.mrk-tab {
  padding: 8px 18px; border-radius: 8px; border: 1px solid var(--border-soft);
  background: var(--bg-hover); color: var(--text-secondary); font-size: 0.875rem; cursor: pointer; transition: border-color 0.2s, color 0.2s;
}
.mrk-tab:hover { border-color: #ffb400; color: #ffe0a0; }
.mrk-tab.active { background: rgba(255,180,0,0.15); border-color: #ffb400; color: #ffd700; font-weight: 600; }
.mrk-panel { background: var(--bg-hover); border: 1px solid var(--border-soft); border-radius: 10px; padding: 14px; }
/* 东财概念榜面板: 自带内边距, 外层不要再叠一层 padding */
.mrk-panel-em { padding: 0; background: transparent; border: none; }
.loading-placeholder { text-align: center; padding: 40px; color: var(--text-muted); }
.spinner { width: 28px; height: 28px; border: 3px solid rgba(255,180,0,0.3); border-top-color: #ffb400; border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 10px; }
@keyframes spin { to { transform: rotate(360deg); } }
.empty-state { text-align: center; padding: 40px; color: var(--text-muted); }
.empty-state.src-fail { color: #ffb400; } /* 数据源故障警示(琥珀色, A股无绿) */
.empty-state.src-fail i { margin-right: 6px; }
.board-code { font-size: 0.75rem; color: var(--text-muted); }
.strength { color: #ffb400; font-weight: 700; }
.lb-badge { display: inline-block; color: #ff8a5c; border: 1px solid rgba(255,80,40,0.5); border-radius: 4px; padding: 0 5px; font-size: 0.75rem; background: rgba(255,80,40,0.12); }
.name-col { white-space: nowrap; }

/* 浅色主题覆盖 */
body[data-bg="light"] .page-back { color: #6b6b6b; }
body[data-bg="light"] .page-back:hover { color: #c79100; }
body[data-bg="light"] .mrk-title { color: #8a5500; }
body[data-bg="light"] .mrk-title .fa { color: #c79100; }
body[data-bg="light"] .mrk-sub { color: #6b6b6b; }
body[data-bg="light"] .mrk-time { color: #6b6b6b; }
body[data-bg="light"] .mrk-tab { color: #6b6b6b; border-color: var(--border-soft); background: rgba(255,255,255,0.6); }
body[data-bg="light"] .mrk-tab:hover { color: #5a4a3a; border-color: #c79100; }
body[data-bg="light"] .mrk-tab.active { color: #5a4a3a; background: rgba(255,180,0,0.15); border-color: #c79100; }
body[data-bg="light"] .mrk-panel { background: rgba(255,255,255,0.85); border-color: var(--border-soft); }
body[data-bg="light"] .mrk-panel-em { background: transparent; border-color: transparent; }
body[data-bg="light"] .strength { color: #8a5500; }
body[data-bg="light"] .lb-badge { color: #b83010; border-color: rgba(184,48,16,0.5); background: rgba(255,80,80,0.1); }
body[data-bg="light"] .board-code { color: #1a1d26; }

/* ===================== 板块轮动历史视图 ===================== */
.rot-toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
.rot-tip { color: var(--text-muted, #aaa); font-size: 0.75rem; flex: 1; min-width: 0; }

/* 注: rot-date / rot-select / rot-reset-btn / rot-data-date 为全局通用样式,
   定义在 src/styles/main.css(板块页 & 连板天梯共用) */
.rot-source { display: flex; gap: 0; border-radius: 6px; overflow: hidden; border: 1px solid var(--border-soft, #444); }
.rot-source-btn { background: var(--bg-input, #1a1a1a); color: var(--text-secondary, #aaa); border: none; padding: 5px 12px; font-size: 0.75rem; cursor: pointer; transition: background 0.15s; }
.rot-source-btn:hover { background: var(--bg-card, #222); }
.rot-source-btn.active { background: var(--accent-warm, #ffb400); color: #1a1a1a; font-weight: 600; }
body[data-bg="light"] .rot-source { border-color: #d0d0d0; }
body[data-bg="light"] .rot-source-btn { background: #f5f5f5; color: #555; }
body[data-bg="light"] .rot-source-btn:hover { background: #eaeaea; }
body[data-bg="light"] .rot-source-btn.active { background: #d97b00; color: #fff; }
.rot-table-scroll { overflow-x: auto; border: 1px solid var(--border-soft); border-radius: 6px; }
.rot-table { border-collapse: collapse; min-width: 100%; font-size: 0.75rem; }
.rot-table th, .rot-table td { padding: 5px 8px; text-align: center; border-bottom: 1px solid var(--border-soft); white-space: nowrap; }
.rot-table th { background: var(--bg-hover); color: var(--text-secondary); font-weight: 500; position: sticky; top: 0; }
.rot-rownum { color: var(--text-muted); font-size: 0.75rem; min-width: 40px; }
.rot-date { color: var(--text-secondary); font-size: 0.75rem; min-width: 70px; }
.rot-cell { min-width: 80px; padding: 3px 4px !important; vertical-align: middle; }
/* 同名板块(出现 >= 2 次)按独立颜色高亮区分: 8 色循环, 暗/亮主题各一套 */
.rot-board { font-size: 0.75rem; color: var(--text-main); border-radius: 4px; padding: 1px 6px; display: inline-block; }
.rot-board.rot-c-0 { /* 仅出现 1 次: 不高亮 */ color: var(--text-main); background: transparent; }
.rot-c-1 { color: #fff; background: #E24B4A; } .rot-c-2 { color: #fff; background: #F08C3F; }
.rot-c-3 { color: #222; background: #E6BE2A; } .rot-c-4 { color: #fff; background: #5C6BC0; }
.rot-c-5 { color: #fff; background: #38A6DF; } .rot-c-6 { color: #fff; background: #2851A8; }
.rot-c-7 { color: #fff; background: #9A57C9; } .rot-c-8 { color: #fff; background: #D45B92; }
body[data-bg="light"] .rot-c-1 { background: #C32D2C; }
body[data-bg="light"] .rot-c-2 { background: #D86A1B; }
body[data-bg="light"] .rot-c-3 { color: #4a3a00; background: #F0CB3F; }
body[data-bg="light"] .rot-c-4 { background: #3F51B5; }
body[data-bg="light"] .rot-c-5 { background: #1E7FB5; }
body[data-bg="light"] .rot-c-6 { background: #1D3F8C; }
body[data-bg="light"] .rot-c-7 { background: #6B2B9A; }
body[data-bg="light"] .rot-c-8 { background: #A82C6C; }
.rot-strength { font-size: 0.75rem; color: var(--text-muted); margin-top: 1px; }
.rot-charts { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 16px; }
.rot-chart-block { background: var(--bg-card); border: 1px solid var(--border-soft); border-radius: 8px; padding: 12px; }
.rot-chart-title { font-size: 0.75rem; color: var(--text-muted); margin-bottom: 6px; }
.rot-svg { width: 100%; height: auto; }
.rot-windows { margin-top: 16px; background: var(--bg-card); border: 1px solid var(--border-soft); border-radius: 8px; padding: 12px; }
.rot-window-legend { display: flex; gap: 16px; justify-content: center; margin-top: 6px; font-size: 0.75rem; }
.rot-window-legend i { margin-right: 4px; }
/* 移动端适配(<=768px): 表格横滑 + Tab 横滑 + 布局紧凑 */
@media (max-width: 768px) {
  .rot-charts { grid-template-columns: 1fr; }
  /* 数据源切换两个按钮等分铺满, 便于拇指点按 */
  .mrk-src { display: flex; width: 100%; }
  .mrk-src-btn { flex: 1 1 0; justify-content: center; padding: 9px 6px; font-size: 0.8125rem; }
  /* 宽表格横向滚动(板块强度/人气热榜) */
  .mrk-panel { overflow-x: auto; -webkit-overflow-scrolling: touch; padding: 10px 8px; }
  .mrk-panel .stock-table { min-width: 880px; }
  /* 历史轮动表横滑内容完整 */
  .rot-table-scroll .rot-table { min-width: 680px; }
  /* Tab 横向滑动(3 个 tab 一排滑, 不换行占纵向空间) */
  .mrk-tabs { flex-wrap: nowrap; overflow-x: auto; -webkit-overflow-scrolling: touch; scrollbar-width: none; padding-bottom: 4px; }
  .mrk-tabs::-webkit-scrollbar { display: none; }
  .mrk-tab { flex-shrink: 0; white-space: nowrap; padding: 7px 12px; font-size: 0.8125rem; }
  /* 头部紧凑 */
  .mrk-head { gap: 6px; }
  .mrk-title { font-size: 1.0625rem; }
  .mrk-sub { font-size: 0.75rem; width: 100%; }
  .mrk-time { margin-left: 0; font-size: 0.75rem; }
  /* 表格字号压缩 */
  .mrk-panel .stock-table th { padding: 7px 4px; font-size: 0.75rem; }
  .mrk-panel .stock-table td { padding: 6px 4px; font-size: 0.75rem; }
  /* 成分股弹窗近全屏 */
  .mrk-modal { max-height: 86vh; }
}
</style>
