<template>
  <div class="mb">
    <div class="mb-head">
      <span class="mb-title"><i class="fa fa-th-large"></i> 板块题材</span>
      <span class="mb-sub">点左列板块看成分股</span>
      <span class="mb-num">{{ rows.length }} 个</span>
    </div>

    <div class="mb-src" role="tablist">
      <button type="button" class="mb-src-btn" :class="{ active: src === 'kpl' }"
              @click="$emit('update:src', 'kpl')"><i class="fa fa-signal"></i> 开盘啦</button>
      <button type="button" class="mb-src-btn" :class="{ active: src === 'em' }"
              @click="$emit('update:src', 'em')"><i class="fa fa-fire"></i> 东财</button>
    </div>

    <div v-if="loading && !rows.length" class="mb-empty">加载板块榜…</div>
    <div v-else-if="failed" class="mb-empty warn">{{ failMsg || '数据源暂不可用' }}</div>
    <div v-else-if="!rows.length" class="mb-empty">暂无板块数据</div>

    <div v-else class="mb-split">
      <!-- 左：板块列表 -->
      <div class="mb-left">
        <div class="mb-left-head">
          <span>板块</span><span>板</span><span>强度</span><span>主力净</span>
        </div>
        <div
          v-for="b in rows" :key="b.boardCode || b.name"
          class="mb-item" :class="{ on: current && (current.boardCode === b.boardCode || current.name === b.name) }"
          @click="select(b)"
        >
          <span class="mb-item-name">{{ b.name }}</span>
          <span class="mb-item-r">{{ b.limitCount != null ? b.limitCount : '—' }}板</span>
          <span class="mb-item-str" :class="{hot: b.strength>=80}">{{ b.strength != null ? b.strength : '—' }}</span>
          <span class="mb-item-net" :class="dirCls(b.mainNet)">{{ b.mainNet != null ? yi(b.mainNet) : '—' }}</span>
        </div>
      </div>

      <!-- 右：成分股 -->
      <div class="mb-right">
        <div v-if="stocksLoading" class="mb-empty"><div class="spinner"></div> 加载成分股…</div>
        <div v-else-if="!current" class="mb-empty">从左侧选一个板块</div>
        <div v-else-if="!stocks.length" class="mb-empty">{{ current.name }} 暂无成分股</div>
        <table v-else class="mb-stocks">
          <thead><tr>
            <th class="sortable" :class="{on: stockSort==='name'}" @click="toggleStockSort('name')">名称/代码</th>
            <th class="r sortable" :class="{on: stockSort==='price'}" @click="toggleStockSort('price')">价格</th>
            <th class="r sortable" :class="{on: stockSort==='change'}" @click="toggleStockSort('change')">涨幅%</th>
            <th class="r sortable" :class="{on: stockSort==='turnover'}" @click="toggleStockSort('turnover')">换手%</th>
            <th class="r sortable" :class="{on: stockSort==='mainNet'}" @click="toggleStockSort('mainNet')">主力净</th>
            <th class="r sortable" :class="{on: stockSort==='amount'}" @click="toggleStockSort('amount')">成交额亿</th>
            <th class="r sortable" :class="{on: stockSort==='floatMv'}" @click="toggleStockSort('floatMv')">流通亿</th>
            <th class="r sortable" :class="{on: stockSort==='totalMv'}" @click="toggleStockSort('totalMv')">总市值亿</th>
            <th>板块概念</th>
          </tr></thead>
          <tbody>
            <tr v-for="s in sortedStocks" :key="s.code" @click="link(s)">
              <td>
                <div class="mb-sname">{{ s.name }}<span v-if="s.limitTag" class="mb-tag">{{ s.limitTag }}</span></div>
                <div class="mb-scode">{{ s.code }}</div>
              </td>
              <td class="r">{{ s.price != null ? s.price.toFixed(2) : '—' }}</td>
              <td class="r" :class="dirCls(s.change)">{{ signed(s.change) }}%</td>
              <td class="r">{{ s.turnover != null ? s.turnover.toFixed(1) : '—' }}</td>
              <td class="r" :class="dirCls(s.mainNet)">{{ s.mainNet != null ? yi(s.mainNet) : '—' }}</td>
              <td class="r">{{ s.amount != null ? yi(s.amount) : '—' }}</td>
              <td class="r">{{ s.floatMv != null ? yi(s.floatMv) : '—' }}</td>
              <td class="r">{{ s.totalMv != null ? yi(s.totalMv) : '—' }}</td>
              <td class="mb-concept">{{ s.concept || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { sortBoardsByLimit } from '../utils/boards'
import { yi, signed } from '../utils/format'
import { kplBoardStocks, emBoardMembers } from '../api/kpl'
import { usePolling } from '../composables/usePolling'
import { isIntradayNow } from '../utils/time'

const props = defineProps({
  boards: { type: Array, default: () => [] },
  src: { type: String, default: 'kpl' },
  loading: { type: Boolean, default: false },
  failed: { type: Boolean, default: false },
  failMsg: { type: String, default: '' },
  /**
   * 2026-09-28 v4.11.79: 回看的历史日期('' = 看实时)。
   * 用父组件的 datePicker 语义 —— 有值时是**历史回看**, 右栏成分股**不能轮询**
   * (历史数据不会变, 且不该让实时数据冲掉用户手动选的日期)。
   */
  date: { type: String, default: '' },
})
const emit = defineEmits(['update:src', 'stock-click'])

const rows = computed(() => sortBoardsByLimit(props.boards))
const current = ref(null)
const stocks = ref([])
const stocksLoading = ref(false)
const stockSort = ref('change')
const stockDir = ref('desc')

function toggleStockSort(k) {
  if (stockSort.value === k) stockDir.value = stockDir.value === 'desc' ? 'asc' : 'desc'
  else { stockSort.value = k; stockDir.value = k === 'name' ? 'asc' : 'desc' }
}
const sortedStocks = computed(() => {
  const dir = stockDir.value === 'desc' ? -1 : 1
  const k = stockSort.value
  return [...stocks.value].sort((a, b) => {
    if (k === 'name') return String(a.name).localeCompare(String(b.name)) * dir
    const va = a[k], vb = b[k]
    if (va == null) return 1
    if (vb == null) return -1
    return (Number(va) - Number(vb)) * dir
  })
})

function select(b) {
  current.value = b
  loadStocks(b)
}

/**
 * 拉取板块成分股。
 * @param b      板块对象
 * @param silent 静默刷新(true 时不显示 loading 态、失败不清空旧列表)
 *
 * 🔴 2026-09-28 v4.11.79: 加 silent 分支供**轮询**用 ——
 *   轮询若走非 silent 路径, 每 60s 会把右栏整表清空再重绘一次(stocks=[] + loading),
 *   视觉上就是"闪一下", 非常难用。silent 时保留旧数据直到新数据到达。
 */
async function loadStocks(b, silent = false) {
  if (!b) return
  if (!silent) {
    stocks.value = []
    stocksLoading.value = true
  }
  try {
    const code = b.boardCode || b.code
    const fn = props.src === 'em' ? emBoardMembers : kplBoardStocks
    const d = await fn(code, props.date || '')
    stocks.value = (d && (d.list || d)) || []
  } catch (e) {
    if (!silent) stocks.value = []      // 静默刷新失败: 保留旧数据, 不闪空
  } finally {
    if (!silent) stocksLoading.value = false
  }
}

watch(rows, (r) => { if (r && r.length && !current.value) select(r[0]) }, { immediate: true })

// 切数据源(kpl/em)时当前板块的成分股要按新源重拉 —— 原来靠父组件 switchSrc
// 触发 boardRows 变化间接触发; 但 current 已存在时 watch(rows) 不再自动选,
// 故这里显式补一条: src 变了就重拉当前板块。
watch(() => props.src, () => { if (current.value) loadStocks(current.value) })

// 🔴 2026-09-28 v4.11.79: 成分股轮询(此前**完全没有**, 右栏点开即"定格"不刷新)。
//   三条件同时满足才轮询, 避免无谓打上游:
//     ① current 存在(用户已选中某板块)
//     ② 非历史回看(!props.date)
//     ③ 盘中(isIntradayNow, 9:30-15:00 工作日)
//   正常交易时段 60s 一次; 非盘中/历史模式自动停。
//   ⚠️ 后端已给两个成分股接口加 60s TTL 缓存(2026-10-01 由 30 → 60, 与本组件 60s 轮询同频), 故这是"便宜"的轮询:
//      多客户端不会线性放大上游请求(见 kpl.fetch_board_stocks 注释)。
usePolling(() => {
  if (!current.value) return
  if (props.date) return
  if (!isIntradayNow()) return
  return loadStocks(current.value, true)
}, 60000, { immediate: false })

function link(s) { emit('stock-click', s) }

function dirCls(v) {
  if (v == null) return 'dim'
  return v > 0 ? 'up' : v < 0 ? 'down' : 'dim'
}
</script>

<style scoped>
.mb { border-radius: var(--r-lg); padding: var(--s2) var(--s3); background: var(--bg-hover); border: 1px solid var(--border-soft); }
.mb-head { display: flex; align-items: baseline; gap: var(--s2); margin-bottom: var(--s2); flex-wrap: wrap; }
.mb-title { color: var(--text-main); font-size: var(--fs-sm); font-weight: 700; }
.mb-title .fa { color: var(--accent); }
.mb-sub { color: var(--text-muted); font-size: var(--fs-xs); }
.mb-num { margin-left: auto; color: var(--text-muted); font-size: var(--fs-xs); }

.mb-src { display: inline-flex; border-radius: var(--r-md); overflow: hidden; border: 1px solid var(--border-soft); margin-bottom: var(--s2); }
.mb-src-btn { background: var(--bg-input); color: var(--text-secondary); border: none; padding: var(--s2) var(--s4); font-size: var(--fs-sm); cursor: pointer; }
.mb-src-btn.active { background: rgba(255,180,0,.15); color: var(--gold); font-weight: 700; }

.mb-empty { color: var(--text-muted); font-size: var(--fs-xs); padding: var(--s3) 2px; }
.mb-empty.warn { color: var(--star); }

.mb-split { display: grid; grid-template-columns: 260px 1fr; gap: var(--s2); }
.mb-left { max-height: 360px; overflow-y: auto; padding-right: var(--s1); }
.mb-left-head { display: flex; gap: var(--s2); font-size: var(--fs-xs); color: var(--text-muted); padding: 0 var(--s2) var(--s1); border-bottom: 1px solid var(--border-soft); margin-bottom: var(--s1); }
.mb-left-head span:first-child { flex: 1; }
.mb-left-head span:not(:first-child) { text-align: right; min-width: 34px; }
.mb-item { display: flex; align-items: center; gap: var(--s2); padding: var(--s1) var(--s2); border-radius: var(--r-md); cursor: pointer; font-size: var(--fs-xs); }
.mb-item:hover { background: rgba(255,180,0,.06); }
.mb-item.on { background: rgba(255,180,0,.14); }
.mb-item-name { flex: 1; color: var(--text-main); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mb-item-r { color: var(--accent); font-weight: 700; font-size: var(--fs-xs); }
.mb-item-str { color: var(--text-muted); font-size: var(--fs-xs); font-variant-numeric: tabular-nums; min-width: 22px; text-align: right; }
.mb-item-str.hot { color: var(--star); font-weight: 700; }
.mb-item-net { color: var(--text-muted); font-size: var(--fs-xs); font-variant-numeric: tabular-nums; min-width: 38px; text-align: right; }

.mb-right { overflow-x: auto; }
.mb-stocks { width: 100%; border-collapse: collapse; font-size: var(--fs-xs); table-layout: fixed; }
.mb-stocks th, .mb-stocks td { padding: var(--s1) var(--s2); text-align: left; border-bottom: 1px solid var(--border-soft); white-space: nowrap; }
.mb-stocks th { color: var(--text-muted); font-weight: 500; font-size: var(--fs-xs); }
.mb-stocks .r { text-align: right; font-variant-numeric: tabular-nums; }
.mb-stocks tbody tr { cursor: pointer; }
.mb-stocks tbody tr:hover td { background: rgba(255,180,0,.06); }
.mb-sname { color: var(--text-main); }
.mb-scode { color: var(--text-muted); font-size: var(--fs-xs); font-family: var(--font-mono); }
.mb-tag { display: inline-block; margin-left: var(--s1); font-size: var(--fs-xs); color: var(--accent); }
.mb-concept { color: var(--text-muted); font-size: var(--fs-xs); max-width: 140px; overflow: hidden; text-overflow: ellipsis; }
.mb-stocks th.sortable { cursor: pointer; }
.mb-stocks th.sortable:hover { color: var(--text-main); }
.mb-stocks th.on { color: var(--accent, var(--up)); }
.up { color: var(--accent); }
.down { color: var(--down); }
.dim { color: var(--text-muted); }

@media (max-width: 768px) {
  .mb-split { grid-template-columns: 1fr; }
  .mb-left { max-height: 200px; }
  .mb-stocks { min-width: 720px; }
}
</style>
