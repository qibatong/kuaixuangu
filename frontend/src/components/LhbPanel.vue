<template>
  <div class="lhb-panel">
    <!-- ★ 2026-09-29 主人拍板：龙虎榜**只留一张层级树图**（个股 → 买卖营业部，带金额）。
         原「净买入排行」12 列表格、「机构席位 / 知名游资」两个 tab、「资金流向图」桑基 tab、
         营业部明细弹窗、顶部提示条与日期回看，全部下线 —— 整页只有这张图。
         数据口径未变(仍是 /api/kpl/lhb + 逐票 /api/kpl/lhb-detail)，只是不再有表格视图。 -->
    <div v-if="loading" class="lhb-loading">
      <div class="spinner"></div>
      <div>{{ tipText }}</div>
    </div>
    <div v-else-if="!list.length" class="lhb-empty">
      暂无数据 —— 当日龙虎榜 <b>17:00 后陆续披露</b>（此前为空，不拿昨日榜单顶上）
    </div>
    <div v-else ref="chartRef" class="lhb-chart"></div>

    <div v-if="!loading && list.length" class="lhb-foot">
      共 {{ list.length }} 只上榜 · 图中展开净买入前 {{ shownCount }} 只 · 滚轮缩放 / 拖拽平移 · <b>点个股可收起或展开</b>
    </div>
  </div>
</template>

<script setup>
// 龙虎榜树图（2026-09-29）
// =====================================================================
// 结构: 龙虎榜(根) → 个股(父) → 买入/卖出营业部(子), 金额写在标签里。
// 数据: `kplLhb()` 拿当日榜单, 再对**净买入前 N 只**逐个 `kplLhbDetail(code)` 取营业部。
// 🔴 为什么要限 N: 明细是**逐票**接口(上游 doc101 没有批量口), 55 只逐个串行要数秒 ——
//    旧桑基图的写法就是串行 for-await(20 只用时可见), 这里改成**并发 5**并只在首屏拉一次。
// 🔴 为什么成对保留「买入 / 卖出」两侧: 同一个席位可能在买也可能在卖, 只画一侧会把
//    "谁在砸盘"丢掉(旧实现只画买入侧)。
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import * as echarts from 'echarts'
import { kplLhb, kplLhbDetail } from '../api/kpl'

const TOP_N = 20          // 展开的个股数（净买入前 N 只）
const SEATS_PER_SIDE = 5  // 每只票每侧最多画几个席位（与旧桑基图一致）
const CONCURRENCY = 5     // 明细并发数（上游有缓存，5 路足够且不打爆）

const chartRef = ref(null)
const list = ref([])
const loading = ref(true)
const fetching = ref(0)   // 已拉完明细的票数（只用于 loading 文案）
let chart = null
let disposed = false

const shownCount = computed(() => Math.min(TOP_N, list.value.length))
const tipText = computed(() =>
  fetching.value ? `正在拉取营业部明细… ${fetching.value}/${shownCount.value}` : '正在加载龙虎榜…')

/** 上榜原因：上游有时下发数组(如 ['日涨幅偏离值达7%']) ⇒ 统一拼成文本 */
function reasonText(v) {
  if (Array.isArray(v)) return v.filter(Boolean).join('、')
  return v ? String(v) : ''
}

/** 元 → 亿（保留 2 位，正数带 +） */
function yiText(v, signed = false) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '-'
  const s = (n / 1e8).toFixed(2)
  return (signed && n > 0 ? '+' : '') + s
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

function buildOption(nodes) {
  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      triggerOn: 'mousemove',
      formatter: (p) => (p.data && p.data.tip) || p.name,
    },
    series: [{
      type: 'tree',
      data: nodes,
      orient: 'LR',                 // 左(根/个股) → 右(营业部)，横向铺开更好读
      left: 24, right: 160, top: 16, bottom: 16,
      symbol: 'circle',
      symbolSize: 7,
      initialTreeDepth: 2,          // 默认展开到营业部
      expandAndCollapse: true,      // 点个股可收起/展开
      roam: true,                   // 滚轮缩放 + 拖拽平移（节点多时必需）
      animationDuration: 300,
      label: {
        position: 'left', align: 'right', verticalAlign: 'middle',
        color: '#e8ecf2', fontSize: 12, fontWeight: 600,
      },
      leaves: {
        label: {
          position: 'right', align: 'left', verticalAlign: 'middle',
          color: '#aab2c0', fontSize: 11, fontWeight: 400,
        },
      },
      lineStyle: { color: '#4a5160', width: 1, curveness: 0.5 },
      emphasis: { focus: 'descendant' },
    }],
  }
}

async function buildTree() {
  if (!chartRef.value) return
  chart = chart || echarts.init(chartRef.value)

  // 净买入降序取前 N（buyIn 缺失排最后）
  const ranked = [...list.value].sort((a, b) => (Number(b.buyIn) || -Infinity) - (Number(a.buyIn) || -Infinity))
  const tops = ranked.slice(0, TOP_N)

  const details = await mapLimit(tops, CONCURRENCY, async (s) => {
    try {
      const d = await kplLhbDetail(s.code)
      fetching.value++
      return (d && d.detail) || null
    } catch (e) {
      fetching.value++
      return null
    }
  })
  if (disposed) return

  const children = []
  tops.forEach((s, i) => {
    const det = details[i]
    if (!det) return
    const up = Number(s.change) > 0
    const color = up ? '#e04a4a' : Number(s.change) < 0 ? '#2ea82e' : '#8a93a3'
    const net = yiText(s.buyIn, true)
    const buys = (det.buyList || []).slice(0, SEATS_PER_SIDE).map((b) => ({
      name: `${b.name} 买${yiText(b.buy)}亿${(b.hot || b.inst) ? (b.hot ? ' [游资]' : ' [机构]') : ''}`,
      itemStyle: { color: '#e0a04a' },
      tip: `${b.name}<br/>买 ${yiText(b.buy)}亿 · 卖 ${yiText(b.sell)}亿 · 净 ${yiText(Number(b.buy) - Number(b.sell), true)}亿`,
    }))
    const sells = (det.sellList || []).slice(0, SEATS_PER_SIDE).map((t) => ({
      name: `${t.name} 卖${yiText(t.sell)}亿${(t.hot || t.inst) ? (t.hot ? ' [游资]' : ' [机构]') : ''}`,
      itemStyle: { color: '#4aa3e0' },
      tip: `${t.name}<br/>买 ${yiText(t.buy)}亿 · 卖 ${yiText(t.sell)}亿 · 净 ${yiText(Number(t.buy) - Number(t.sell), true)}亿`,
    }))
    children.push({
      name: `${s.name} ${s.code} 净${net}亿`,
      itemStyle: { color },
      tip: `${s.name} ${s.code}<br/>涨跌幅 ${Number(s.change) || 0}% · 净买入 ${net}亿 · 成交 ${yiText(s.amount)}亿`
        + (reasonText(det.upReason) ? `<br/>上榜原因：${reasonText(det.upReason)}` : ''),
      children: [...buys, ...sells],
    })
  })

  const root = {
    name: `龙虎榜 ${list.value.length} 只`,
    itemStyle: { color: '#ffb400' },
    tip: '买入席位=橙 · 卖出席位=蓝 · 个股红涨绿跌（点个股可收起展开）',
    children,
  }
  chart.clear()
  chart.setOption(buildOption([root]))
}

async function load() {
  loading.value = true
  fetching.value = 0
  try {
    const d = await kplLhb()
    list.value = (d && d.list) || []
  } catch (e) {
    list.value = []
  } finally {
    loading.value = false
  }
  if (!list.value.length) return
  // 🔴 必须先等 DOM 渲染出图表容器再 init —— loading 分支刚切走时 `chartRef` 还不存在,
  //    直接 init 会拿到 null(Vue 的 ref 在下一个 tick 才绑定)。nextTick 后再等一帧拿布局。
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
  if (chart) { chart.dispose(); chart = null }   // 旧桑基图漏了释放，这里补上
})
</script>

<style scoped>
.lhb-chart { width: 100%; height: 76vh; min-height: 520px; }
.lhb-loading, .lhb-empty {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 8px; min-height: 220px; color: var(--text-muted); font-size: 0.875rem;
}
.lhb-empty b { color: #e6b400; }
.lhb-foot { margin-top: 6px; color: var(--text-muted); font-size: 0.75rem; }
.lhb-foot b { color: var(--text-secondary); }

@media (max-width: 768px) {
  /* 手机端: 树图横向展开会被压扁 ⇒ 给足高度并允许缩放平移(roam 已开) */
  .lhb-chart { height: 68vh; min-height: 440px; }
  .lhb-foot { font-size: 0.7rem; line-height: 1.5; }
}
</style>
