<template>
  <Teleport to="body">
    <!-- 2026-10-04 P1⑥ 个股详情抽屉：mode="drawer" 时从底部升起（默认仍是居中弹窗） -->
    <div v-if="visible" class="chart-mask" :class="{ 'is-drawer': isDrawer }" @click.self="close">
      <div class="chart-modal">
        <!-- 抽屉把手：下拉 >70px 关闭（手机端习惯手势） -->
        <div
          v-if="isDrawer" class="drawer-grab"
          @touchstart.passive="onGrabStart" @touchmove.passive="onGrabMove" @touchend.passive="onGrabEnd"
        >
          <span class="drawer-grab-bar"></span>
        </div>
        <div class="chart-header">
          <div class="chart-title">
            <span class="stock-name">{{ stockName || '股票' }}</span>
            <span class="stock-code">{{ stockCode }}</span>
            <span v-if="preClose" class="stock-pre-close">昨收: {{ preClose.toFixed(2) }}</span>
          </div>
          <div class="chart-actions">
            <button class="chart-btn-icon" title="刷新当前周期" @click.stop="refresh">
              <i class="fa fa-refresh" :class="{ 'fa-spin': loading }"></i>
            </button>
            <button class="chart-btn-icon chart-btn-close" title="关闭(ESC)" @click.stop="close">
              <i class="fa fa-times"></i>
            </button>
          </div>
        </div>

        <div class="chart-tabs">
          <button
            v-for="t in tabsShown" :key="t.key"
            class="chart-tab" :class="{ active: activeTab === t.key }"
            @click.stop="switchTab(t.key)"
          >
{{ t.label }}
</button>
        </div>

        <div class="chart-body">
          <!-- 个股详情(为什么选它): 默认 tab, 独立组件拉详情 -->
          <div v-if="activeTab === 'detail'" class="chart-body-detail">
            <StockDetailPanel :code="stockCode" :name="stockName" />
          </div>

          <!-- 竞价三时点（9:15/9:20/9:25）：抽屉专属 tab —— 规划 P1⑥ 的四块内容之一。
               接口 GET /api/stats/bid-snapshot-stock?date=&code=；非交易日后端自动回退最近交易日。 -->
          <div v-else-if="activeTab === 'bid'" class="chart-body-bid">
            <div class="bid-head">
              竞价三时点
              <span class="bid-date">{{ bidDate }}</span>
            </div>
            <div v-if="bidLoading" class="bid-tip"><i class="fa fa-spinner fa-spin"></i> 加载中…</div>
            <div v-else-if="bidErr" class="bid-tip err">{{ bidErr }}</div>
            <div v-else-if="!bidPoints.length" class="bid-tip">当日无竞价快照数据</div>
            <template v-else>
              <div class="bid-grid">
                <div v-for="p in bidPoints" :key="p.key" class="bid-item">
                  <span class="bid-time">{{ p.label }}</span>
                  <span class="bid-chg" :class="{ up: p.change > 0, down: p.change < 0 }">{{ fmtPct(p.change) }}</span>
                  <span class="bid-sub">竞价额 {{ fmtAmt(p.bidAmt) }}</span>
                  <span v-if="p.buyAmt" class="bid-sub">封单 {{ fmtAmt(p.buyAmt) }}</span>
                </div>
              </div>
              <div class="bid-note">
                口径：竞价涨幅按快照价相对昨收计算；「封单」为该时点买一档挂单金额。9:15 之前撤单可撤，
                9:20 后不可撤 ⇒ 三个时点的变化最能反映资金意图。
              </div>
            </template>
          </div>

          <template v-else>
            <!-- 画布容器必须常驻, 不能随 loading 被 v-if 卸载:
                 否则切 分时/日K/周K/月K 时容器摘掉再重建, 而 ECharts 实例仍绑在旧(已脱离)节点上,
                 导致各周期切出来都是空白(无 canvas 子节点)。loading/empty 改为绝对定位浮在画布上。 -->
            <div ref="chartRef" class="chart-canvas"></div>
            <div v-if="loading" class="chart-loading"><i class="fa fa-spinner fa-spin"></i> 加载中…</div>
            <div v-else-if="!hasData" class="chart-empty">
              <i class="fa fa-bar-chart"></i> {{ errorMsg || '暂无数据' }}
            </div>
          </template>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick, shallowRef } from 'vue'
// 2026-09-30 v4.11.83 (P2-2): echarts 注册收口到 utils/echarts.js(全仓共用一份, 见该文件注释)
import echarts from '../utils/echarts'
import { stockChart } from '../api/stocks'
import { bidSnapshotStock } from '../api/stats'
import StockDetailPanel from './StockDetailPanel.vue'
import { fmtNum, fmtVol, fmtVolShort } from '../utils/chart'
import { isIntradayNow, todayBj } from '../utils/time'

const props = defineProps({
  visible: { type: Boolean, default: false },
  code: { type: String, default: '' },
  name: { type: String, default: '' },
  // 2026-10-04 P1⑥：'modal' = 原居中弹窗(桌面)；'drawer' = 手机端底部抽屉（多一个竞价三时点 tab）
  mode: { type: String, default: 'modal' },
})
const emit = defineEmits(['update:visible', 'close'])

const isDrawer = computed(() => props.mode === 'drawer')

const tabs = [
  { key: 'detail', label: '个股' },
  { key: 'minute', label: '分时' },
  { key: 'day',    label: '日K' },
  { key: 'week',   label: '周K' },
  { key: 'month',  label: '月K' },
]
// 抽屉模式才给「竞价」页：桌面弹窗维持原样，避免动到已验收的界面
const tabsShown = computed(() => isDrawer.value
  ? [...tabs.slice(0, 1), { key: 'bid', label: '竞价' }, ...tabs.slice(1)]
  : tabs)
const activeTab = ref('detail')
const stockCode = computed(() => props.code || '')
const stockName = ref(props.name || '')
const preClose = ref(0)
const loading = ref(false)
const errorMsg = ref('')
const chartData = ref(null)  // {period, time:[], price/avg/volume 或 open/close/high/low/...}
const chartRef = ref(null)
const chartInst = shallowRef(null)
const resizeObs = shallowRef(null)
// 手机端: 边距/字号/滑块高度压缩 (dvw 即视口宽, 阈值 ≤ 768px)
const isMobile = () => {
  try { return window.innerWidth <= 768 } catch (e) { return false } }
const isLandscape = () => {
  try { return window.innerWidth > window.innerHeight } catch (e) { return false } }
// 根据移动端返回 grid/label/滑块 紧凑参数
function chartMetrics() {
  if (isMobile()) {
    if (isLandscape()) {
      // 横屏: 边距极小, 让主图尽可能高
      return {
        mainGrid: { left: 42, right: 8, top: 6, height: '58%' },
        volGrid:  { left: 42, right: 8, top: '74%', height: '16%' },
        axisFont: 9, tooltipFont: 11, markLineFont: 9,
        dataZoomSlider: true, sliderHeight: 12, sliderBottom: 1,
      }
    }
    return {
      mainGrid: { left: 44, right: 10, top: 14, height: '55%' },
      volGrid:  { left: 44, right: 10, top: '74%', height: '16%' },
      axisFont: 10, tooltipFont: 12, markLineFont: 10,
      dataZoomSlider: true, sliderHeight: 16, sliderBottom: 2,
    }
  }
  return {
    mainGrid: null,  // 用 option 内默认 (60,60,20)
    volGrid:  null,
    axisFont: -1, tooltipFont: -1, markLineFont: -1,
    dataZoomSlider: false,
  }
}

const hasData = computed(() => {
  const d = chartData.value
  if (!d) return false
  if (d.period === 'minute') return d.time && d.time.length > 0
  return d.time && d.time.length > 0
})

function close() {
  emit('update:visible', false)
  emit('close')
}

async function fetchData({ silent = false } = {}) {
  if (activeTab.value === 'detail' || activeTab.value === 'bid') return
  if (!stockCode.value) return
  if (!silent) loading.value = true
  errorMsg.value = ''
  try {
    const r = await stockChart(stockCode.value, activeTab.value)
    if (!r || !r.ok) {
      errorMsg.value = (r && r.msg) || '数据加载失败'
      chartData.value = null
      return
    }
    chartData.value = r
    if (r.preClose) preClose.value = Number(r.preClose) || 0
    if (r.name) stockName.value = r.name
  } catch (e) {
    errorMsg.value = (e && e.message) || '请求异常'
    chartData.value = null
  } finally {
    // 先把 loading 置 false → 模板切回 canvas(v-else ref=chartRef) → 再渲染
    // (不能在上面的 try 里、loading 仍为 true 时调 renderChart: 那时 chartRef 为 null,
    //   ensureChart 直接 return, 导致 分时/日K/周K/月K 全都画不出来)
    loading.value = false
    await nextTick()
    if (chartData.value && !errorMsg.value) renderChart()
  }
}

function switchTab(k) {
  if (activeTab.value === k) return
  activeTab.value = k
  if (k === 'detail') return
  if (k === 'bid') { loadBid(); return }
  fetchData()
  startPoll()
}

// ---------- 竞价三时点（抽屉专属 tab）----------
// 后端 /api/stats/bid-snapshot-stock 需要 date；非交易日会自动回退到最近交易日 ⇒
// 前端只管传**北京时间的今天**，不必自己判交易日。
const BID_POINT_LABELS = { '9_15': '9:15', '9_20': '9:20', '9_25': '9:25' }
const bidDate = ref('')
const bidLoading = ref(false)
const bidErr = ref('')
const bidPoints = ref([])

function fmtPct(v) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (!isFinite(n)) return '—'
  return (n > 0 ? '+' : '') + n.toFixed(2) + '%'
}
// 后端返回单位：万元
function fmtAmt(v) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (!isFinite(n)) return '—'
  return n >= 10000 ? (n / 10000).toFixed(2) + '亿' : n.toFixed(0) + '万'
}

async function loadBid() {
  if (!stockCode.value) return
  bidLoading.value = true
  bidErr.value = ''
  try {
    const d = todayBj()
    const r = await bidSnapshotStock(d, stockCode.value)
    bidDate.value = (r && r.date) || d
    const raw = (r && r.points) || {}
    bidPoints.value = Object.keys(BID_POINT_LABELS).map(k => ({
      key: k,
      label: BID_POINT_LABELS[k],
      change: raw[k] ? Number(raw[k].bid_change) : null,
      bidAmt: raw[k] ? Number(raw[k].bid_amt) : null,
      buyAmt: raw[k] ? Number(raw[k].bid_buy_amt) : null,
    }))
  } catch (e) {
    bidErr.value = (e && e.message) || '竞价数据加载失败'
    bidPoints.value = []
  } finally {
    bidLoading.value = false
  }
}

// ---------- 抽屉下拉关闭手势 ----------
let grabStartY = 0
let grabbing = false
function onGrabStart(e) { grabbing = true; grabStartY = e.touches[0].clientY }
function onGrabMove(e) {
  if (!grabbing) return
  const dy = e.touches[0].clientY - grabStartY
  // 只有向下拖才响应，向上滑不动（避免误关）
  if (dy > 70) { grabbing = false; close() }
}
function onGrabEnd() { grabbing = false }

function refresh() {
  fetchData()
}

// ---------- 盘中自动刷新 ----------
// 弹窗打开期间, 工作日盘中(9:30-15:00)每 60s 静默刷新一次当前周期:
// 分时对齐后端 60s 缓存; 日K后端 120s / 周K月K 1800s 缓存, 轮询多为缓存命中, 开销小。
let pollTimer = null
function startPoll() {
  stopPoll()
  pollTimer = setInterval(() => {
    if (isIntradayNow()) fetchData({ silent: true })
  }, 60000)
}
function stopPoll() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

// ---------- ECharts ----------
function ensureChart() {
  if (!chartRef.value) return
  if (chartInst.value) return
  chartInst.value = echarts.init(chartRef.value, null, { renderer: 'canvas' })
  // 响应式: 监听窗口 resize
  resizeObs.value = new ResizeObserver(() => chartInst.value && chartInst.value.resize())
  resizeObs.value.observe(chartRef.value)
}

function pctArr(arr, base) {
  if (!base) return arr.map(() => null)
  return arr.map(v => (v == null || v === '' ? null : +((v - base) / base * 100).toFixed(2)))
}

// 简单移动平均线: 返回与原序列等长数组, 不足 n 的前 n-1 个位置置 null(不连线)
function ma(arr, n) {
  const out = []
  for (let i = 0; i < arr.length; i++) {
    if (i < n - 1) { out.push(null); continue }
    let s = 0
    for (let j = i - n + 1; j <= i; j++) s += arr[j] || 0
    out.push(+(s / n).toFixed(2))
  }
  return out
}

// 2026-10-07 视觉自查: 图表配色改随主题取 CSS 变量 —— 此前整份 ECharts 写死深底色,
// 浅色主题下轴标签/tooltip/K线绿原样渲染(#22c55e 白底约 2.2:1 不可读)。
// 弹窗每次打开/切周期都会重建 option ⇒ 在构建时取当次主题值即可, 无需监听主题切换。
function chartC() {
  const light = document.body.dataset.bg === 'light'
  const cs = getComputedStyle(document.body)
  const v = (name, fb) => { const s = (cs.getPropertyValue(name) || '').trim(); return s || fb }
  return {
    up: v('--up', light ? '#c62828' : '#ff8a6f'),
    down: v('--down', light ? '#0f7a3d' : '#00c864'),
    flat: v('--text-muted', light ? '#5b6472' : '#9ca3af'),
    axisLabel: v('--text-muted', light ? '#5b6472' : '#9ca3af'),
    axisLine: light ? 'rgba(0,0,0,0.30)' : 'rgba(255,255,255,0.28)',
    splitLine: light ? 'rgba(0,0,0,0.07)' : 'rgba(255,255,255,0.05)',
    tooltipBg: light ? 'rgba(255,255,255,0.97)' : 'rgba(20,22,28,0.94)',
    tooltipBorder: light ? 'rgba(0,0,0,0.12)' : '#374151',
    tooltipText: v('--text-main', light ? '#1a1d26' : '#e5e7eb'),
    pointerBg: light ? 'rgba(0,0,0,0.72)' : '#1f2937',
    sliderBg: light ? 'rgba(0,0,0,0.05)' : 'rgba(255,255,255,0.04)',
  }
}

function renderChart() {
  if (!chartData.value) return
  ensureChart()
  if (!chartInst.value) return
  const d = chartData.value
  const option = d.period === 'minute'
    ? optionMinute(d)
    : optionCandle(d)
  chartInst.value.setOption(option, true)
}

function optionMinute(d) {
  const times = d.time || []
  const prices = d.price || []
  const avgs = d.avg || []
  const vols = d.volume || []
  const base = preClose.value || (prices[0] && Number(prices[0]))
  // 涨跌幅(%)
  const pricePct = pctArr(prices, base)
  const avgPct = pctArr(avgs, base)
  // 涨跌色成交量
  const C = chartC()
  const volData = times.map((_, i) => {
    const color = i === 0 ? C.flat
      : (prices[i] > prices[i - 1] ? C.up : prices[i] < prices[i - 1] ? C.down : C.flat)
    return { value: vols[i] || 0, itemStyle: { color } }
  })
  // Y轴右边: 价格刻度 → 左边: 涨跌幅
  const minPrice = Math.min(...prices.filter(v => v), base || Infinity)
  const maxPrice = Math.max(...prices.filter(v => v), base || -Infinity)
  const margin = Math.max((maxPrice - minPrice) * 0.08, base ? base * 0.005 : 0.01)
  const m = chartMetrics()
  const af = m.axisFont > 0 ? m.axisFont : 11
  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      backgroundColor: C.tooltipBg, borderColor: C.tooltipBorder,
      textStyle: { color: C.tooltipText, fontSize: m.tooltipFont > 0 ? m.tooltipFont : 12 },
      formatter: (params) => {
        if (!params || !params.length) return ''
        const idx = params[0].dataIndex
        const tm = times[idx] || ''
        const p = prices[idx]; const ap = avgs[idx]
        const v = vols[idx]
        const pct = pricePct[idx]
        const lines = [`<b>${tm}</b>`]
        if (p != null) lines.push(`价格: <b>${+Number(p).toFixed(2)}</b> <span style="color:${pct >= 0 ? C.up : C.down}">${pct >= 0 ? '+' : ''}${pct}%</span>`)
        if (ap != null) lines.push(`均价: ${+Number(ap).toFixed(2)}`)
        if (v != null) lines.push(`成交量: ${fmtVol(v)}`)
        return lines.join('<br/>')
      }
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [
      m.mainGrid || { left: 60, right: 60, top: 20, height: '55%' },
      m.volGrid  || { left: 60, right: 60, top: '75%', height: '18%' },
    ],
    xAxis: [
      { type: 'category', data: times, boundaryGap: false,
        gridIndex: 0, axisLine: { lineStyle: { color: C.axisLine } },
        axisLabel: { show: false }, axisTick: { show: false } },
      { type: 'category', data: times, boundaryGap: false,
        gridIndex: 1, axisLine: { lineStyle: { color: C.axisLine } },
        axisLabel: { color: C.axisLabel, fontSize: af } },
    ],
    yAxis: [
      // 主图 左: 涨跌幅, 右: 价格
      { type: 'value', gridIndex: 0, position: 'left',
        axisLabel: { color: C.axisLabel, fontSize: af, formatter: v => v.toFixed(2) + '%' },
        splitLine: { lineStyle: { color: C.splitLine } },
        min: base ? +(((base - (maxPrice + margin)) / base * 100)).toFixed(2) : null,
        max: base ? +(((maxPrice + margin - base) / base * 100)).toFixed(2) : null,
      },
      { type: 'value', gridIndex: 0, position: 'right',
        axisLabel: { color: C.axisLabel, fontSize: af, formatter: v => Number(v).toFixed(2) },
        splitLine: { show: false }, min: minPrice - margin, max: maxPrice + margin,
      },
      // 副图 成交量
      { type: 'value', gridIndex: 1, position: 'left',
        axisLabel: { color: C.axisLabel, fontSize: m.axisFont > 0 ? m.axisFont : 10, formatter: fmtVolShort },
        splitLine: { lineStyle: { color: C.splitLine } } },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
    ],
    series: [
      { name: '价格', type: 'line', xAxisIndex: 0, yAxisIndex: 0,
        data: pricePct, showSymbol: false, smooth: false,
        lineStyle: { color: '#3b82f6', width: 1.5 },
        areaStyle: {
          color: {
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [{ offset: 0, color: 'rgba(59,130,246,0.22)' }, { offset: 1, color: 'rgba(59,130,246,0)' }]
          }
        },
        markLine: {
          symbol: 'none', silent: true,
          lineStyle: { color: C.flat, type: 'dashed', width: 1 },
          data: [{ yAxis: 0, label: { formatter: base ? Number(base).toFixed(2) : '', position: 'end', color: C.axisLabel,
              fontSize: m.markLineFont > 0 ? m.markLineFont : 10 } }]
        }
      },
      { name: '均价', type: 'line', xAxisIndex: 0, yAxisIndex: 0,
        data: avgPct, showSymbol: false, smooth: false,
        lineStyle: { color: '#f59e0b', width: 1 }
      },
      { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 2, data: volData,
        barWidth: '60%' },
    ]
  }
}

function optionCandle(d) {
  const times = d.time || []
  const opens = d.open || [], closes = d.close || []
  const highs = d.high || [], lows = d.low || []
  const vols = d.volume || [], amounts = d.amount || []
  const base = preClose.value
  // OHLC Candlestick
  const ohlc = times.map((_, i) => [opens[i], closes[i], lows[i], highs[i]])
  // 涨跌色成交量
  const C = chartC()
  const volData = times.map((_, i) => {
    const c = closes[i], o = opens[i]
    const color = c > o ? C.up : c < o ? C.down : C.flat
    return { value: vols[i] || 0, itemStyle: { color } }
  })
  const m = chartMetrics()
  const af = m.axisFont > 0 ? m.axisFont : 11
  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      backgroundColor: C.tooltipBg, borderColor: C.tooltipBorder,
      textStyle: { color: C.tooltipText, fontSize: m.tooltipFont > 0 ? m.tooltipFont : 12 },
      formatter: (params) => {
        if (!params || !params.length) return ''
        const idx = params[0].dataIndex
        const tm = times[idx] || ''
        const o = opens[idx], c = closes[idx], h = highs[idx], l = lows[idx]
        const v = vols[idx], am = amounts[idx]
        const chg = base && o ? +((c - base) / base * 100).toFixed(2) : null
        const color = chg >= 0 ? C.up : C.down
        // 追加均线值(MA5/10/20/30/60, 同为 line 系列)
        const maLine = (params || []).filter(p => p.seriesType === 'line' && /^MA\d+$/.test(p.seriesName))
          .map(p => `${p.seriesName}: <span style="color:${p.color || '#ccc'}">${p.value == null ? '-' : +Number(p.value).toFixed(2)}</span>`)
          .join('&nbsp;&nbsp;')
        return `
          <b>${tm}</b><br/>
          开: <b>${fmtNum(o)}</b> 收: <b style="color:${color}">${fmtNum(c)}</b> ${chg != null ? `<span style="color:${color}">${chg >= 0 ? '+' : ''}${chg}%</span>` : ''}<br/>
          高: ${fmtNum(h)} 低: ${fmtNum(l)}<br/>
          量: ${fmtVol(v)}${am != null ? `<br/>额: ${fmtVol(am)}` : ''}${maLine ? `<br/>${maLine}` : ''}
        `
      }
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }], label: { backgroundColor: C.pointerBg } },
    grid: [
      m.mainGrid || { left: 60, right: 24, top: 20, height: '55%' },
      m.volGrid  || { left: 60, right: 24, top: '75%', height: '18%' },
    ],
    xAxis: [
      { type: 'category', data: times, scale: true, boundaryGap: true,
        gridIndex: 0, axisLine: { lineStyle: { color: C.axisLine } },
        axisLabel: { show: false }, axisTick: { show: false } },
      { type: 'category', data: times, scale: true, boundaryGap: true,
        gridIndex: 1, axisLine: { lineStyle: { color: C.axisLine } },
        axisLabel: { color: C.axisLabel, fontSize: af },
      },
    ],
    yAxis: [
      { type: 'value', gridIndex: 0, scale: true,
        axisLabel: { color: C.axisLabel, fontSize: af, formatter: v => Number(v).toFixed(2) },
        splitLine: { lineStyle: { color: C.splitLine } },
      },
      { type: 'value', gridIndex: 1,
        axisLabel: { color: C.axisLabel, fontSize: m.axisFont > 0 ? m.axisFont : 10, formatter: fmtVolShort },
        splitLine: { lineStyle: { color: C.splitLine } } },
    ],
    dataZoom: (() => {
      const dz = [
        { type: 'inside', xAxisIndex: [0, 1], start: 50, end: 100 },
      ]
      if (m.dataZoomSlider) {
        dz.push({ type: 'slider', xAxisIndex: [0, 1], start: 50, end: 100,
          bottom: m.sliderBottom ?? 2,
          height: m.sliderHeight ?? 16,
          borderColor: 'transparent',
          backgroundColor: C.sliderBg,
          fillerColor: 'rgba(59,130,246,0.2)',
          handleStyle: { color: '#3b82f6' },
          textStyle: { color: C.axisLabel, fontSize: 9 },
        })
      } else {
        dz.push({ type: 'slider', xAxisIndex: [0, 1], start: 50, end: 100,
          bottom: 2, height: 18, borderColor: 'transparent',
          backgroundColor: C.sliderBg,
          fillerColor: 'rgba(59,130,246,0.2)',
          handleStyle: { color: '#3b82f6' },
          textStyle: { color: C.axisLabel, fontSize: 10 },
        })
      }
      return dz
    })(),
    series: [
      { name: 'K线', type: 'candlestick', xAxisIndex: 0, yAxisIndex: 0,
        data: ohlc,
        itemStyle: {
          color: C.up, color0: C.down,
          borderColor: C.up, borderColor0: C.down,
        }
      },
      // 均线 MA5/10/20/30/60 (基于收盘价)
      { name: 'MA5', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: ma(closes, 5),
        showSymbol: false, symbol: 'none', smooth: true, z: 5,
        lineStyle: { width: 1.2, color: '#f6c85f' } },
      { name: 'MA10', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: ma(closes, 10),
        showSymbol: false, symbol: 'none', smooth: true, z: 5,
        lineStyle: { width: 1.2, color: '#3ba0ff' } },
      { name: 'MA20', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: ma(closes, 20),
        showSymbol: false, symbol: 'none', smooth: true, z: 5,
        lineStyle: { width: 1.2, color: '#d974ff' } },
      { name: 'MA30', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: ma(closes, 30),
        showSymbol: false, symbol: 'none', smooth: true, z: 5,
        lineStyle: { width: 1.2, color: '#4de07d' } },
      { name: 'MA60', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: ma(closes, 60),
        showSymbol: false, symbol: 'none', smooth: true, z: 5,
        lineStyle: { width: 1.2, color: '#ff8d5a' } },
      { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: volData,
        barWidth: '60%' },
    ]
  }
}

// ---------- helpers (已移至 ../utils/chart 以便单测) ----------

// ---------- lifecycle ----------
watch(() => props.visible, (v) => {
  if (v) {
    stockName.value = props.name || ''
    activeTab.value = 'detail'
    document.addEventListener('keydown', onKey)
    // 抽屉模式预取竞价三时点：一次请求换「竞价」tab 秒开（切 tab 再拉会有明显白等）
    if (isDrawer.value) loadBid()
  } else {
    document.removeEventListener('keydown', onKey)
    stopPoll()
    if (chartInst.value) { chartInst.value.dispose(); chartInst.value = null }
    if (resizeObs.value) { resizeObs.value.disconnect(); resizeObs.value = null }
    chartData.value = null
  }
})

watch(() => props.code, () => {
  if (props.visible && activeTab.value !== 'detail') fetchData()
})

function onKey(e) {
  if (e.key === 'Escape') close()
  if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
    const idx = tabs.findIndex(t => t.key === activeTab.value)
    if (idx < 0) return
    const next = e.key === 'ArrowLeft'
      ? tabs[(idx - 1 + tabs.length) % tabs.length].key
      : tabs[(idx + 1) % tabs.length].key
    switchTab(next)
  }
}

onMounted(() => {
  if (props.visible) fetchData()
})
onUnmounted(() => {
  document.removeEventListener('keydown', onKey)
  stopPoll()
  if (chartInst.value) { try { chartInst.value.dispose() } catch {} chartInst.value = null }
  if (resizeObs.value) { resizeObs.value.disconnect(); resizeObs.value = null }
})
</script>

<style scoped>
.chart-mask {
  position: fixed; inset: 0; background: rgba(0,0,0,0.55);
  z-index: 9999; display: flex; align-items: center; justify-content: center;
  backdrop-filter: blur(2px);
}
.chart-modal {
  width: min(960px, 96vw); height: min(680px, 92vh);
  background: var(--bg-panel-solid, #0f172a);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-lg);
  display: flex; flex-direction: column; overflow: hidden;
  box-shadow: var(--sh-3);
}
.chart-header {
  padding: var(--s3) var(--s4); display: flex; align-items: center; justify-content: space-between;
  border-bottom: 1px solid var(--border-soft);
  background: var(--bg-panel-solid, #111827);
}
.chart-title { display: flex; align-items: baseline; gap: var(--s2); flex-wrap: wrap; }
.stock-name { font-size: var(--fs-lg); font-weight: 700; color: var(--text-main, #f3f4f6); }
.stock-code { font-size: var(--fs-sm); color: var(--text-secondary, var(--text-dim)); font-family: inherit; }
.stock-pre-close { font-size: var(--fs-xs); color: var(--text-muted, var(--text-faint)); }
.chart-actions { display: flex; gap: var(--s2); }
.chart-btn-icon {
  width: 32px; height: 32px; border-radius: var(--r-md); border: none; cursor: pointer;
  background: transparent; color: var(--text-secondary, #d1d5db); font-size: var(--fs-base);
  transition: background 0.15s;
}
.chart-btn-icon:hover { background: rgba(255,255,255,0.08); }
.chart-btn-close:hover { background: rgba(239, 68, 68, 0.2); color: var(--accent); }

.chart-tabs {
  display: flex; gap: var(--s1); padding: var(--s2) var(--s4) 0;
  border-bottom: 1px solid var(--border-soft);
}
.chart-tab {
  padding: var(--s2) var(--s4); border-radius: 6px 6px 0 0; border: none; cursor: pointer;
  background: transparent; color: var(--text-secondary, var(--text-dim)); font-size: var(--fs-sm);
  font-weight: 500; transition: background-color 0.15s, color 0.15s;
}
.chart-tab:hover { background: rgba(255,255,255,0.04); color: var(--text-primary); }
.chart-tab.active {
  background: rgba(var(--accent-rgb),0.12); color: var(--accent);
  border-bottom: 2px solid var(--accent);
}

.chart-body {
  flex: 1; min-height: 0; position: relative; padding: var(--s1) var(--s2) var(--s1) var(--s1);
  background: var(--bg-panel-solid, #0b1220);
}
.chart-canvas { width: 100%; height: 100%; }
.chart-body-detail { width: 100%; height: 100%; overflow: hidden; }
.chart-loading, .chart-empty {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  flex-direction: column; gap: var(--s2); color: var(--text-muted, var(--text-faint)); font-size: var(--fs-base);
}
.chart-loading i { color: #3b82f6; }
.chart-empty i { font-size: 2rem; opacity: 0.5; }

/* ===== 手机端适配 (<=768px): 全屏弹框, 边距压缩, 触控目标加大, ECharts 紧凑 ===== */
@media (max-width: 768px) {
  .chart-mask {
    /* 手机全屏背景, 去掉 backdrop-filter 避免卡 */
    backdrop-filter: none;
    background: rgba(0,0,0,0.72);
    padding: 0;
    align-items: stretch;
    justify-content: stretch;
  }
  .chart-modal {
    width: 100vw !important;
    height: 100vh !important;
    max-width: 100%;
    max-height: 100%;
    border-radius: 0;
    border: none;
    box-shadow: none;
    /* 避免 iOS Safari 底部 tab 条遮挡 */
    padding-bottom: env(safe-area-inset-bottom, 0);
  }
  /* 头部: 更紧凑 + 触控按钮加大到 min 44px */
  .chart-header {
    padding: var(--s2) var(--s3);
    gap: var(--s2);
  }
  .chart-title { gap: var(--s2); align-items: center; }
  .stock-name { font-size: var(--fs-lg) !important; }
  .stock-code { font-size: var(--fs-xs) !important; }
  .stock-pre-close { font-size: var(--fs-xs); }
  .chart-btn-icon {
    min-width: 44px;
    min-height: 44px;
    width: 44px;
    height: 44px;
    font-size: var(--fs-lg);
    border-radius: var(--r-lg);
  }
  /* 4 个周期 Tab: 手机上等宽一排, 可点击区加大 */
  .chart-tabs {
    padding: var(--s2) var(--s2) 0;
    gap: 2px;
  }
  .chart-tab {
    flex: 1 1 0;
    min-width: 0;
    padding: var(--s2) var(--s1);
    font-size: var(--fs-sm);
    border-radius: 6px 6px 0 0;
  }
  /* 图表主体: 去掉 body padding, 让画布占满 */
  .chart-body {
    padding: 2px var(--s1) 2px 2px;
  }
}

/* 横屏手机(< 768px height, 但 width > height 即横屏): 画布顶边距再压, 留给内容 */
@media (max-width: 768px) and (orientation: landscape) {
  .chart-header {
    padding: var(--s2) var(--s2);
  }
  .chart-title { gap: var(--s1); }
  .stock-name { font-size: var(--fs-base) !important; }
  .chart-tabs { padding: 2px var(--s2) 0; }
  .chart-tab { padding: var(--s1) var(--s1); font-size: var(--fs-xs); }
  .chart-btn-icon {
    min-width: 36px; min-height: 36px; width: 36px; height: 36px; font-size: var(--fs-md);
  }
}

/* ===== 2026-10-04 P1⑥ 个股详情抽屉（mode="drawer"，≤768px 从底部升起）=====
   ★ 选择器都用「双类」提高特异性：上面 @media(max-width:768px) 里有 .chart-modal 全屏规则，
     单类选择器会被它压住，写成 .chart-modal.is-drawer 才能可靠覆盖（与媒体查询的先后无关）。 */
.chart-mask.is-drawer {
  align-items: flex-end;
  justify-content: stretch;
  padding: 0;
  background: rgba(0, 0, 0, .62);
  backdrop-filter: none;
}
.chart-modal.is-drawer {
  width: 100vw !important;
  max-width: none !important;
  height: 84vh !important;
  border-radius: 14px 14px 0 0;
  border-bottom: none;
  /* iPhone 底部 Home 指示条别压住内容（全站统一用 env(safe-area-inset-*) 适配） */
  padding-bottom: env(safe-area-inset-bottom);
  box-sizing: content-box;
  animation: drawerUp .22s ease-out;
}
@keyframes drawerUp { from { transform: translateY(16%); opacity: .5 } to { transform: none; opacity: 1 } }
.drawer-grab {
  display: flex; justify-content: center; flex-shrink: 0;
  padding: var(--s2) 0 2px; background: var(--bg-panel-solid, #0f172a);
  touch-action: none;   /* 禁止浏览器把它当页面滚动，才能收到连续的 touchmove */
}
.drawer-grab-bar {
  width: 40px; height: 4px; border-radius: var(--r-sm);
  background: var(--border-soft, #374151);
}

/* ===== 竞价三时点卡 ===== */
.chart-body-bid {
  height: 100%; overflow-y: auto;
  padding: 12px 14px calc(16px + env(safe-area-inset-bottom));
}
.bid-head {
  display: flex; align-items: baseline; gap: var(--s2); margin-bottom: var(--s2);
  font-size: var(--fs-sm); font-weight: 700; color: var(--text-main, #f3f4f6);
}
.bid-date { font-size: var(--fs-xs); font-weight: 400; color: var(--text-muted, var(--text-faint)); }
.bid-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--s2); }
.bid-item {
  border: 1px solid var(--border-soft); border-radius: var(--r-lg);
  padding: var(--s2) var(--s2); text-align: center;
}
.bid-time { display: block; font-size: var(--fs-xs); color: var(--text-secondary, var(--text-dim)); }
.bid-chg {
  display: block; margin: 2px 0 var(--s1); font-size: var(--fs-lg); font-weight: 700;
  color: var(--text-main, #f3f4f6);
  font-family: var(--font-mono);
}
.bid-chg.up { color: var(--up); }     /* A 股口径：红涨绿跌(2026-10-07 自查: 原裸 #ef4444/#22c55e 不随主题) */
.bid-chg.down { color: var(--down); }
.bid-sub { display: block; font-size: var(--fs-xs); color: var(--text-muted, var(--text-faint)); }
.bid-tip { padding: var(--s8) 0; text-align: center; font-size: var(--fs-sm); color: var(--text-muted, var(--text-faint)); }
.bid-tip.err { color: var(--brand-soft); }
.bid-note {
  margin-top: var(--s2); padding-top: var(--s2); border-top: 1px dashed var(--border-soft);
  font-size: var(--fs-xs); color: var(--text-muted, var(--text-faint)); line-height: 1.6;
}
</style>
