<template>
  <div class="lhb-panel">
    <!-- ★ 2026-09-29 主人按通达信龙虎榜定标后重构(第二版):
         ① 方向改成 **营业部(席位) → 个股** —— 第一版做的是「个股 → 席位」, 与通达信相反,
            且默认把 20 只票 × 10 席位全摊开 ⇒ 一屏线团, 主人反馈「这怎么看」;
         ② 默认**只展开到席位**那一层(个股收起, 点席位展开), 与通达信的可展开层级一致;
         ③ 左侧补**席位榜**列表(买/卖/只数 + 游资/机构标签), 点一下 = 只看该席位,
            再点一次回到全部 —— 对应通达信左栏那列营业部。 -->
    <div v-if="loading" class="lhb-loading">
      <div class="spinner"></div>
      <div>{{ tipText }}</div>
    </div>
    <div v-else-if="!list.length" class="lhb-empty">
      暂无数据 —— 当日龙虎榜 <b>17:00 后陆续披露</b>（此前为空，不拿昨日榜单顶上）
    </div>

    <div v-else class="lhb-body">
      <aside class="lhb-seats">
        <div class="lhb-seats-head">
          上榜席位 <span class="dim">(按买入额)</span>
          <span v-if="curSeat" class="lhb-seat-clear" @click="curSeat = ''">显示全部</span>
        </div>
        <div v-for="k in seats" :key="k.name" class="lhb-seat"
             :class="{ active: curSeat === k.name }"
             :title="`买 ${wan(k.buy)}万 · 卖 ${wan(k.sell)}万 · 净 ${wan(k.buy - k.sell)}万 · 涉及 ${k.stocks.length} 只`"
             @click="toggleSeat(k.name)">
          <div class="lhb-seat-name">
            {{ k.name }}
            <span v-if="k.hot" class="tag tag-hot">游资</span>
            <span v-if="k.inst" class="tag tag-inst">机构</span>
          </div>
          <div class="lhb-seat-sub">买 {{ wan(k.buy) }}万 · {{ k.stocks.length }} 只</div>
        </div>
        <div v-if="!seats.length" class="lhb-seats-empty">席位列空（明细未取到）</div>
      </aside>

      <div class="lhb-tree-wrap">
        <div ref="chartRef" class="lhb-chart"></div>
      </div>
    </div>

    <div v-if="!loading && list.length" class="lhb-foot">
      共 {{ list.length }} 只上榜 · 展开净买入前 <b>{{ shownCount }}</b> 只的营业部明细 ·
      <b>点席位展开个股</b> / 点左栏只看该席位 · 滚轮缩放 · 拖拽平移
    </div>
  </div>
</template>

<script setup>
// 龙虎榜树图（2026-09-29 第二版：按通达信龙虎榜定标）
// =====================================================================
// 结构: 龙虎榜(根) → **营业部/席位**(一层) → 个股(二层, 默认收起)。
// 数据: `kplLhb()` 榜单 + 对**净买入前 N 只**逐个 `kplLhbDetail(code)` 取买卖席位;
//       再把「个股→席位」**反转聚合**成「席位→个股」。
// 🔴 为什么反转: 看龙虎榜的真实问题是「这个游资/机构今天在扫什么」——
//    通达信左栏是营业部、中栏是「营业部 → 个股」, 就是这个方向。
// 🔴 为什么默认收起个股: 第一版默认全展开(20 只 × 10 席位 ≈ 200 叶) ⇒ 一屏线团不可读。
// 🔴 为什么限 N: 明细是逐票接口(上游无批量口, 66 只串行要数秒) ⇒ **并发 6** 且只拉前 N 只;
//    因此左栏「席位榜」的买/卖/只数 = **这 N 只票范围内**的聚合(不是全市场席位活跃度,
//    那需要另一份数据源)。界面上如实标注。
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
// 2026-09-30 v4.11.83 (P2-2): 由全量 import 'echarts' 改为按需注册收口(见 utils/echarts.js)
import echarts from '../utils/echarts'
import { kplLhb, kplLhbDetail } from '../api/kpl'

const TOP_N = 30          // 拉明细的个股数（净买入前 N 只）
const SEATS_SHOWN = 40    // 左栏最多列几个席位
const STOCKS_PER_SEAT = 20 // 每个席位最多画几只个股
const CONCURRENCY = 6

const chartRef = ref(null)
const list = ref([])
const loading = ref(true)
const progress = ref(0)
const curSeat = ref('')
const seats = ref([])
let chart = null
let disposed = false

const shownCount = computed(() => Math.min(TOP_N, list.value.length))
const tipText = computed(() =>
  progress.value ? `正在拉取营业部明细… ${progress.value}/${shownCount.value}` : '正在加载龙虎榜…')

/** 元 → 万（1 位小数；缺失给 '-'） */
function wan(v) {
  const n = Number(v)
  return Number.isFinite(n) ? (n / 1e4).toFixed(1) : '-'
}

/** 上榜原因：上游有时下发数组(如 ['日涨幅偏离值达7%']) ⇒ 统一拼文本 */
function reasonText(v) {
  if (Array.isArray(v)) return v.filter(Boolean).join('、')
  return v ? String(v) : ''
}

/** 限并发映射（保持原顺序） */
async function mapLimit(items, limit, fn) {
  const out = new Array(items.length)
  let i = 0
  const workers = Array.from({ length: Math.max(1, Math.min(limit, items.length)) }, async () => {
    while (i < items.length) {
      const idx = i++
      out[idx] = await fn(items[idx], idx)
    }
  })
  await Promise.all(workers)
  return out
}

function seatNode(k) {
  const stocks = k.stocks.slice(0, STOCKS_PER_SEAT).map((s) => {
    const net = Number(s.buy) - Number(s.sell)
    return {
      name: `${s.name} ${s.code}  买${wan(s.buy)}万/卖${wan(s.sell)}万`,
      itemStyle: { color: Number(s.change) > 0 ? '#e04a4a' : Number(s.change) < 0 ? '#2ea82e' : '#8a93a3' },
      tip: `${s.name} ${s.code}<br/>涨跌幅 ${Number(s.change) || 0}%`
        + `<br/>该席位 买 ${wan(s.buy)}万 · 卖 ${wan(s.sell)}万 · 净 ${wan(net)}万`
        + (s.reason ? `<br/>上榜原因：${s.reason}` : ''),
    }
  })
  return {
    name: `${k.name}  买${wan(k.buy)}万 · ${k.stocks.length}只`,
    itemStyle: { color: k.hot ? '#ff6a3c' : k.inst ? '#ffb400' : '#8fa0b8' },
    tip: `${k.name}${k.hot ? '（游资）' : k.inst ? '（机构）' : ''}<br/>`
      + `在此榜内 买 ${wan(k.buy)}万 · 卖 ${wan(k.sell)}万 · 净 ${wan(k.buy - k.sell)}万<br/>涉及 ${k.stocks.length} 只`,
    children: stocks,
  }
}

function buildOption(nodes) {
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item', triggerOn: 'mousemove', formatter: (p) => (p.data && p.data.tip) || p.name },
    series: [{
      type: 'tree',
      data: nodes,
      orient: 'LR',
      left: 20, right: 220, top: 20, bottom: 20,
      symbol: 'circle',
      symbolSize: 7,
      initialTreeDepth: 1,          // 🔴 只展开到「席位」层, 个股收起(点席位展开)
      expandAndCollapse: true,
      roam: true,
      animationDuration: 300,
      label: { position: 'left', align: 'right', verticalAlign: 'middle', color: '#e8ecf2', fontSize: 12, fontWeight: 600 },
      leaves: { label: { position: 'right', align: 'left', verticalAlign: 'middle', color: '#aab2c0', fontSize: 11, fontWeight: 400 } },
      lineStyle: { color: '#4a5160', width: 1, curveness: 0.5 },
      emphasis: { focus: 'descendant' },
    }],
  }
}

function render() {
  if (!chart) return
  const picked = curSeat.value ? seats.value.filter((k) => k.name === curSeat.value) : seats.value
  if (!picked.length) { chart.clear(); return }
  const root = {
    name: curSeat.value ? `席位：${curSeat.value}` : `龙虎榜 ${shownCount.value} 只 · ${seats.value.length} 个席位`,
    itemStyle: { color: '#ffb400' },
    tip: '席位按买入额排序（游资=橙 / 机构=金）· 点席位可展开它的个股',
    children: picked.map(seatNode),
  }
  chart.clear()
  chart.setOption(buildOption([root]))
}

function toggleSeat(name) {
  curSeat.value = curSeat.value === name ? '' : name
  render()
}

/** 「个股 → 席位」明细 反转为「席位 → 个股」 */
function pivot(rows) {
  const m = new Map()
  for (const r of rows) {
    if (!r.det) continue
    for (const side of ['buyList', 'sellList']) {
      for (const x of (r.det[side] || [])) {
        const nm = x.name
        if (!nm) continue
        if (!m.has(nm)) m.set(nm, { name: nm, buy: 0, sell: 0, hot: false, inst: false, stocks: [] })
        const k = m.get(nm)
        k.buy += Number(x.buy) || 0
        k.sell += Number(x.sell) || 0
        k.hot = k.hot || !!x.hot
        k.inst = k.inst || !!x.inst
        if (!k.stocks.some((s) => s.code === r.code)) {
          k.stocks.push({
            code: r.code, name: r.name, change: r.change,
            buy: Number(x.buy) || 0, sell: Number(x.sell) || 0,
            reason: reasonText(r.det.upReason),
          })
        }
      }
    }
  }
  return [...m.values()].sort((a, b) => b.buy - a.buy).slice(0, SEATS_SHOWN)
}

async function buildTree() {
  if (!chartRef.value) return
  chart = chart || echarts.init(chartRef.value)

  const ranked = [...list.value].sort((a, b) => (Number(b.buyIn) || -Infinity) - (Number(a.buyIn) || -Infinity))
  const tops = ranked.slice(0, TOP_N)
  const details = await mapLimit(tops, CONCURRENCY, async (s) => {
    let det = null
    try {
      const d = await kplLhbDetail(s.code)
      det = (d && d.detail) || null
    } catch (e) { det = null }
    progress.value++
    return { ...s, det }
  })
  if (disposed) return

  seats.value = pivot(details)
  render()
}

async function load() {
  loading.value = true
  progress.value = 0
  try {
    const d = await kplLhb()
    list.value = (d && d.list) || []
  } catch (e) {
    list.value = []
  } finally {
    loading.value = false
  }
  if (!list.value.length) return
  // 🔴 必须等 DOM 渲染出容器再 init（loading 分支刚切走时 ref 还是 null）
  await nextTick()
  await new Promise((r) => requestAnimationFrame(() => r()))
  await buildTree()
}

function onResize() { chart && chart.resize() }

onMounted(async () => {
  window.addEventListener('resize', onResize)
  await load()
})
onUnmounted(() => {
  disposed = true
  window.removeEventListener('resize', onResize)
  if (chart) { chart.dispose(); chart = null }
})
</script>

<style scoped>
.lhb-body { display: flex; gap: 10px; align-items: stretch; }
.lhb-seats {
  flex: 0 0 236px; max-height: 78vh; overflow-y: auto;
  border: 1px solid var(--border-soft, #3a3f4b); border-radius: 8px; padding: 6px;
  background: var(--bg-panel, rgba(18, 22, 35, 0.6));
}
.lhb-seats-head {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  font-size: 0.75rem; color: var(--text-muted); padding: 4px 4px 6px;
  border-bottom: 1px solid var(--border-soft, #3a3f4b); margin-bottom: 4px;
}
.lhb-seat-clear { margin-left: auto; color: var(--accent); cursor: pointer; }
.lhb-seat { padding: 5px 6px; border-radius: 6px; cursor: pointer; }
.lhb-seat:hover { background: rgba(255, 255, 255, 0.05); }
.lhb-seat.active { background: rgba(255, 180, 0, 0.12); }
.lhb-seat-name { font-size: 0.75rem; color: var(--text-primary); line-height: 1.35; word-break: break-all; }
.lhb-seat-sub { font-size: 0.6875rem; color: var(--text-muted); margin-top: 2px; }
.lhb-seats-empty { font-size: 0.75rem; color: var(--text-muted); padding: 8px 6px; }
.lhb-tree-wrap { flex: 1 1 auto; min-width: 0; }
.lhb-chart { width: 100%; height: 78vh; min-height: 460px; }
.lhb-loading, .lhb-empty {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 8px; min-height: 220px; color: var(--text-muted); font-size: 0.875rem;
}
.lhb-empty b { color: #e6b400; }
.lhb-foot { margin-top: 6px; color: var(--text-muted); font-size: 0.75rem; }
.lhb-foot b { color: var(--text-secondary); }
.tag { display: inline-block; font-size: 0.625rem; padding: 0 3px; border-radius: 3px; margin-left: 3px; vertical-align: middle; }
.tag-inst { background: rgba(255, 180, 0, 0.15); color: #ffb400; border: 1px solid rgba(255, 180, 0, 0.3); }
.tag-hot { background: rgba(255, 80, 40, 0.15); color: #ff6a3c; border: 1px solid rgba(255, 80, 40, 0.3); }

@media (max-width: 768px) {
  /* 手机: 左栏收到顶部(横向滚动), 图给足高度 */
  .lhb-body { flex-direction: column; }
  .lhb-seats { flex: 0 0 auto; max-height: 168px; }
  .lhb-chart { height: 62vh; min-height: 400px; }
  .lhb-foot { font-size: 0.7rem; line-height: 1.5; }
}
</style>
