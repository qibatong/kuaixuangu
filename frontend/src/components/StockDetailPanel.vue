<template>
  <div v-if="loaded" class="sd-wrap">
    <!-- ===== ① 头部 ===== -->
    <div class="sd-head">
      <div class="sd-head-top">
        <div class="sd-nm">{{ d.name || name || '—' }}<span class="sd-code">{{ code }}</span></div>
        <div class="sd-metrics">
          <div class="sd-mrow">现涨 <b :class="chgCls">{{ chgText }}</b></div>
          <div class="sd-mrow">竞额 <b>{{ d.bidAmt != null ? fmtNum(d.bidAmt) + '万' : '—' }}</b></div>
          <div class="sd-mrow">自由流通 <b>{{ d.freeCirculationMV != null ? fmtNum(d.freeCirculationMV) + '亿' : '—' }}</b></div>
          <div class="sd-mrow">可信 <b>{{ score ? score.confidence + '%' : '—' }}</b></div>
        </div>
      </div>
      <div class="sd-price">
        <span class="sd-price-num" :class="chgCls">{{ chgText }}</span>
        <span class="sd-price-sub" :class="d.bidChange >= 0 ? 'up' : 'down'">竞涨 {{ fmtPct(d.bidChange) }}</span>
        <span class="sd-stamp">竞价定格 09:25</span>
      </div>
      <div class="sd-tags">
        <span v-if="d.lb > 0" class="sd-tag sd-tag-gray">昨{{ d.lb }}板</span>
        <span
v-if="risk && risk.warn_level" class="sd-tag"
              :class="risk.warn_level === 'red' ? 'sd-tag-red' : 'sd-tag-amber'"
>
          {{ risk.warn_level === 'red' ? '严重异动' : '异动风险' }}</span>
        <span v-for="t in (topics || []).slice(0,1)" :key="t.name" class="sd-tag sd-tag-app">{{ t.name }}</span>
      </div>
    </div>

    <!-- ===== ② 为什么选它 ===== -->
    <div v-if="score" class="sd-card">
      <div class="sd-ctitle">为什么选它<span class="sd-hint">评分 {{ score.probability }} / 可信 {{ score.confidence }}%</span></div>
      <div class="sd-score">
        <div class="sd-ring" :style="ringStyle">
          <div class="sd-ring-in"><b>{{ score.probability }}</b><span>综合评分</span></div>
        </div>
        <div class="sd-factors">
          <div v-for="f in score.parts" :key="f.key" class="sd-f">
            <div class="sd-f-row">
              <span class="sd-f-name">{{ f.label }}</span>
              <span class="sd-f-score">{{ f.score }}</span>
            </div>
            <div class="sd-f-bar"><div class="sd-f-fill" :style="{width: f.score + '%', background: factorColor(f.key)}"></div></div>
          </div>
        </div>
      </div>
      <div v-if="reason" class="sd-reason">
        <b>入选理由：</b>{{ reason }}
      </div>
    </div>

    <!-- ===== ③ 近5日走势 ===== -->
    <div v-if="recent5.dates.length" class="sd-card">
      <div class="sd-ctitle">近5日走势<span class="sd-hint">收盘 / 涨跌幅</span></div>
      <div ref="trendRef" class="sd-trend"></div>
    </div>

    <!-- ===== ④ 历史战绩 ===== -->
    <div v-if="history.length" class="sd-card">
      <div class="sd-ctitle">历史战绩<span class="sd-hint">{{ history.length }} 次入选</span></div>
      <div class="sd-hist">
        <div v-for="h in history" :key="h.date" class="sd-hist-row">
          <span class="sd-hist-date">{{ h.date }}</span>
          <span class="sd-hist-pct" :class="(h.realChange||0) >= 0 ? 'up' : 'down'">{{ fmtPct(h.realChange) }}</span>
        </div>
      </div>
    </div>

    <!-- ===== ⑤ 所属题材 ===== -->
    <div v-if="topics && topics.length" class="sd-card">
      <div class="sd-ctitle">所属题材<span class="sd-hint">点击可跳转题材榜</span></div>
      <div class="sd-boards">
        <span v-for="t in topics" :key="t.name" class="sd-board" @click="onBoardClick(t.name)">
          {{ t.name }} <span class="sd-board-hot">热度 {{ t.heat }}</span>
        </span>
      </div>
    </div>

    <!-- ===== ⑤ 风险提示 ===== -->
    <div v-if="risk && (risk.warn_msg || risk.max_range)" class="sd-card">
      <div class="sd-ctitle">风险提示</div>
      <div class="sd-riskbox" :class="risk.warn_level || 'amber'">
        <span>{{ risk.warn_msg || risk.max_range }}</span>
      </div>
    </div>

    <div class="sd-foot">数据来源：最近交易日 9:25 定格快照 · 仅展示分项评分</div>
  </div>

  <div v-else class="sd-state">
    <span v-if="loading" class="sd-loading">加载中…</span>
    <span v-else class="sd-empty">{{ errMsg || '暂无详情数据' }}</span>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { stockDetail, stockChart, fetchQuotes } from '../api/stocks'
import * as echarts from 'echarts'

const props = defineProps({
  code: { type: String, default: '' },
  name: { type: String, default: '' },
  quote: { type: Object, default: null },
})
const emit = defineEmits(['board'])

const d = ref(null)
const score = ref(null)
const risk = ref(null)
const history = ref([])
const topics = ref([])
const reason = ref('')
const loading = ref(false)
const errMsg = ref('')
const loaded = ref(false)
const trendRef = ref(null)
let chart = null

const ACCENT = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#ff7a59'

function fmtPct(v) { return (v == null ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%') }
function fmtNum(v) { return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 1 }) }

const chgText = computed(() => {
  // 优先用右列传过来的实时涨幅(与列表口径一致), 其次接口数据
  if (props.quote && props.quote.change != null) return fmtPct(props.quote.change)
  const v = d.value && (d.value.realChange ?? d.value.bidChange)
  return v == null ? '—' : fmtPct(v)
})
const chgCls = computed(() => {
  let v = d.value && (d.value.realChange ?? d.value.bidChange)
  if (props.quote && props.quote.change != null) v = props.quote.change
  return v == null ? '' : (v >= 0 ? 'up' : 'down')
})
const ringStyle = computed(() => {
  const p = score.value ? score.value.probability : 0
  return { background: 'conic-gradient(' + ACCENT + ' ' + (p * 3.6) + 'deg, rgba(255,255,255,.07) 0deg)' }
})

const PALETTE = ['#ff5c5c', '#ff8a5c', '#ffb020', '#6ea8ff', '#b98aff', '#ff7a59']
function factorColor(key) {
  const order = ['warn', 'bid', 'activity', 'yesterday', 'market', 'topic']
  const i = order.indexOf(key)
  return PALETTE[i >= 0 ? i : 0]
}

function onBoardClick(name) { emit('board', name) }

// 近5日走势: 收盘价线(左轴) + 涨跌幅柱(右轴)
const recent5 = ref({ dates: [], closes: [], pcts: [] })
function renderTrend() {
  if (!trendRef.value || !recent5.value.dates.length) return
  if (!chart) chart = echarts.init(trendRef.value)
  const { dates, closes, pcts } = recent5.value
  chart.setOption({
    grid: { left: 40, right: 40, top: 18, bottom: 22 },
    tooltip: { trigger: 'axis', backgroundColor: '#1c2740', borderWidth: 0, textStyle: { color: '#e8edf5', fontSize: 11 } },
    xAxis: { type: 'category', data: dates, axisLine: { lineStyle: { color: '#2a3650' } }, axisLabel: { color: '#5b6b85', fontSize: 10 } },
    yAxis: [
      { type: 'value', scale: true, position: 'left', axisLabel: { color: '#5b6b85', fontSize: 10 }, splitLine: { lineStyle: { color: '#1c2740' } } },
      { type: 'value', position: 'right', axisLabel: { color: '#5b6b85', fontSize: 10, formatter: '{value}%' }, splitLine: { show: false } },
    ],
    series: [
      { name: '收盘价', type: 'line', data: closes, smooth: true, symbol: 'circle', symbolSize: 5,
        lineStyle: { color: '#ff7a5c', width: 2 }, itemStyle: { color: '#ff7a5c' }, yAxisIndex: 0 },
      { name: '涨跌幅', type: 'bar', data: pcts, barWidth: 10, yAxisIndex: 1,
        itemStyle: { color: p => p.value >= 0 ? '#ffb020' : '#35c284', borderRadius: [3,3,0,0] } },
    ],
  }, true)
}

async function load() {
  if (!props.code) return
  loading.value = true
  errMsg.value = ''
  try {
    const r = await stockDetail(props.code)
    if (!r || !r.ok) { errMsg.value = (r && r.msg) || '详情加载失败'; loaded.value = true; return }
    d.value = r
    score.value = r.score || null
    risk.value = r.risk || null
    history.value = (r.history || []).slice(0, 6)
    topics.value = r.topics || []
    reason.value = r.reason || ''
    loaded.value = true
    // 拉实时行情覆盖顶部现涨(与列表口径一致, 非交易日也能拿到最近快照)
    try {
      const qr = await fetchQuotes([props.code])
      const q = qr && qr.quotes && qr.quotes[props.code]
      if (q) {
        if (q.realChange != null) d.value.realChange = q.realChange
        if (q.price != null) d.value.lastPrice = q.price
        if (q.turnover != null) d.value.turnover = q.turnover
      }
    } catch (e) { /* 实时行情失败不阻塞 */ }
    // 近5日走势(日K: 收盘线+涨跌幅柱), 失败不阻塞面板
    try {
      const kr = await stockChart(props.code, 'day')
      if (kr && kr.ok && kr.time && kr.time.length) {
        const k = Math.min(6, kr.time.length)
        const dates = kr.time.slice(-k)
        const closes = (kr.close || []).slice(-k)
        const pcts = closes.map((c, i) => {
          const prev = i === 0 ? closes[0] : closes[i - 1]
          return prev ? +((c - prev) / prev * 100).toFixed(2) : null
        })
        recent5.value = { dates, closes, pcts }
      }
    } catch (e) { /* 日K取不到就不画 */ }
    await nextTick()
    renderTrend()
  } catch (e) {
    errMsg.value = (e && e.message) || '请求异常'
    loaded.value = true
  } finally { loading.value = false }
}

function resize() { chart && chart.resize() }

onMounted(() => { load(); window.addEventListener('resize', resize) })
onBeforeUnmount(() => { window.removeEventListener('resize', resize); chart && chart.dispose() })
watch(() => props.code, () => { if (props.code) { loaded.value = false; load() } })
</script>

<style scoped>
.sd-wrap { height: 100%; overflow-y: auto; padding: 14px; }
.sd-wrap::-webkit-scrollbar { width: 6px; }
.sd-wrap::-webkit-scrollbar-thumb { background: rgba(255,255,255,.12); border-radius: 3px; }

.sd-head { padding-bottom: 12px; border-bottom: 1px solid var(--border-soft, #1f2937); }
.sd-head-top { display: flex; justify-content: space-between; align-items: flex-start; }
.sd-nm { font-size: 1.125rem; font-weight: 800; color: var(--text-main, #f3f4f6); }
.sd-code { font-size: 0.8125rem; color: var(--text-secondary, #9ca3af); font-weight: 500; margin-left: 6px; }
.sd-metrics { text-align: right; font-size: 0.75rem; color: var(--text-secondary, #9ca3af); line-height: 1.7; }
.sd-mrow b { color: var(--text-main, #f3f4f6); font-weight: 600; margin-left: 2px; }

.sd-price { margin-top: 10px; display: flex; align-items: baseline; gap: 8px; }
.sd-price-num { font-size: 2rem; font-weight: 800; line-height: 1; }
.sd-price-sub { font-size: 0.8125rem; font-weight: 600; }
.sd-stamp { font-size: 0.6875rem; color: var(--text-muted, #6b7280); }
.up { color: var(--up, #ff5c5c) !important; }
.down { color: var(--down, #3db97f) !important; }

.sd-tags { margin-top: 10px; display: flex; gap: 8px; flex-wrap: wrap; }
.sd-tag { padding: 3px 10px; border-radius: 6px; font-size: 0.75rem; border: 1px solid; }
.sd-tag-gray { color: var(--text-secondary, #9ca3af); background: rgba(255,255,255,.04); border-color: var(--border-soft, #1f2937); }
.sd-tag-amber { color: var(--amber, #ffb020); background: rgba(255,176,32,.08); border-color: rgba(255,176,32,.4); }
.sd-tag-red { color: #ff6b6b; background: rgba(255,77,79,.1); border-color: rgba(255,77,79,.4); }
.sd-tag-app { color: var(--up, #ff5c5c); background: rgba(255,92,92,.08); border-color: rgba(255,92,92,.4); }

.sd-card { margin-top: 12px; background: var(--card, #111826); border: 1px solid var(--border-soft, #1f2937); border-radius: 12px; padding: 14px; }
.sd-ctitle { display: flex; align-items: center; font-size: 0.9375rem; font-weight: 700; color: var(--text-main, #f3f4f6); margin-bottom: 12px; }
.sd-ctitle::before { content: ""; width: 3px; height: 14px; background: var(--accent, #ff7a5c); border-radius: 2px; margin-right: 8px; }
.sd-hint { margin-left: auto; font-size: 0.6875rem; color: var(--text-muted, #6b7280); font-weight: 400; }

.sd-score { display: flex; gap: 14px; align-items: center; }
.sd-ring { width: 84px; height: 84px; border-radius: 50%; flex-shrink: 0; display: flex; align-items: center; justify-content: center; }
.sd-ring-in { width: 64px; height: 64px; border-radius: 50%; background: var(--card, #111826); display: flex; flex-direction: column; align-items: center; justify-content: center; }
.sd-ring-in b { font-size: 1.375rem; color: var(--text-main, #f3f4f6); line-height: 1; }
.sd-ring-in span { font-size: 0.625rem; color: var(--text-muted, #6b7280); margin-top: 2px; }
.sd-factors { flex: 1; min-width: 0; }
.sd-f { margin-bottom: 9px; }
.sd-f-row { display: flex; justify-content: space-between; font-size: 0.8125rem; margin-bottom: 4px; }
.sd-f-name { color: var(--text-secondary, #9ca3af); }
.sd-f-score { color: var(--text-main, #f3f4f6); font-weight: 700; }
.sd-f-bar { height: 6px; background: rgba(255,255,255,.06); border-radius: 3px; overflow: hidden; }
.sd-f-fill { height: 100%; border-radius: 3px; }

.sd-reason { margin-top: 12px; border: 1px solid rgba(255,122,89,.35); border-left: 3px solid var(--accent, #ff7a5c);
  background: rgba(255,122,89,.06); border-radius: 8px; padding: 10px 12px; font-size: 0.8125rem; line-height: 1.7; color: #d8c8c0; }
.sd-reason b { color: var(--accent, #ff7a59); }

.sd-trend { width: 100%; height: 160px; }

.sd-hist { display: flex; flex-direction: column; gap: 8px; }
.sd-hist-row { display: flex; justify-content: space-between; align-items: center;
  padding: 6px 10px; background: rgba(255,255,255,.03); border-radius: 6px; }
.sd-hist-date { font-size: 0.8125rem; color: var(--text-secondary, #9ca3af); font-family: ui-monospace, monospace; }
.sd-hist-pct { font-size: 0.875rem; font-weight: 700; }

.sd-boards { display: flex; flex-wrap: wrap; gap: 8px; }
.sd-board { padding: 6px 12px; border-radius: 8px; font-size: 0.8125rem; color: var(--text-main, #f3f4f6);
  background: rgba(255,255,255,.04); border: 1px solid var(--border-soft, #1f2937); cursor: pointer; }
.sd-board:hover { border-color: var(--accent, #ff7a5c); color: var(--accent, #ff7a5c); }
.sd-board-hot { color: var(--amber, #ffb020); font-size: 0.75rem; margin-left: 2px; }

.sd-riskbox { padding: 10px 12px; border-radius: 8px; font-size: 0.8125rem; line-height: 1.6; color: var(--text-secondary, #9ca3af); }
.sd-riskbox.red { background: rgba(255,77,79,.08); border: 1px solid rgba(255,77,79,.35); }
.sd-riskbox.amber { background: rgba(255,176,32,.08); border: 1px solid rgba(255,176,32,.35); }

.sd-foot { margin-top: 12px; font-size: 0.625rem; color: var(--text-muted, #6b7280); text-align: center; }
.sd-state { height: 100%; display: flex; align-items: center; justify-content: center; color: var(--text-muted, #6b7280); font-size: 0.875rem; }
</style>
