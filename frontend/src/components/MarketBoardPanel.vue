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
          v-for="(b, i) in rows" :key="b.boardCode || b.name"
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

const props = defineProps({
  boards: { type: Array, default: () => [] },
  src: { type: String, default: 'kpl' },
  loading: { type: Boolean, default: false },
  failed: { type: Boolean, default: false },
  failMsg: { type: String, default: '' },
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

async function loadStocks(b) {
  stocks.value = []
  stocksLoading.value = true
  try {
    const code = b.boardCode || b.code
    const fn = props.src === 'em' ? emBoardMembers : kplBoardStocks
    const d = await fn(code)
    stocks.value = (d && (d.list || d)) || []
  } catch (e) { stocks.value = [] } finally { stocksLoading.value = false }
}

watch(rows, (r) => { if (r && r.length && !current.value) select(r[0]) }, { immediate: true })

function link(s) { emit('stock-click', s) }

function dirCls(v) {
  if (v == null) return 'dim'
  return v > 0 ? 'up' : v < 0 ? 'down' : 'dim'
}
</script>

<style scoped>
.mb { border-radius: 10px; padding: 10px 12px; background: var(--bg-hover); border: 1px solid var(--border-soft); }
.mb-head { display: flex; align-items: baseline; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.mb-title { color: var(--text-main); font-size: 0.8125rem; font-weight: 700; }
.mb-title .fa { color: var(--accent); }
.mb-sub { color: var(--text-muted); font-size: 0.6875rem; }
.mb-num { margin-left: auto; color: var(--text-muted); font-size: 0.6875rem; }

.mb-src { display: inline-flex; border-radius: 8px; overflow: hidden; border: 1px solid var(--border-soft); margin-bottom: 10px; }
.mb-src-btn { background: var(--bg-input); color: var(--text-secondary); border: none; padding: 6px 14px; font-size: 0.8125rem; cursor: pointer; }
.mb-src-btn.active { background: rgba(255,180,0,.15); color: #ffd700; font-weight: 700; }

.mb-empty { color: var(--text-muted); font-size: 0.75rem; padding: 12px 2px; }
.mb-empty.warn { color: #ffb400; }

.mb-split { display: grid; grid-template-columns: 260px 1fr; gap: 10px; }
.mb-left { max-height: 360px; overflow-y: auto; padding-right: 4px; }
.mb-left-head { display: flex; gap: 6px; font-size: 0.65rem; color: var(--text-muted); padding: 0 6px 4px; border-bottom: 1px solid var(--border-soft); margin-bottom: 4px; }
.mb-left-head span:first-child { flex: 1; }
.mb-left-head span:not(:first-child) { text-align: right; min-width: 34px; }
.mb-item { display: flex; align-items: center; gap: 6px; padding: 5px 6px; border-radius: 6px; cursor: pointer; font-size: 0.75rem; }
.mb-item:hover { background: rgba(255,180,0,.06); }
.mb-item.on { background: rgba(255,180,0,.14); }
.mb-item-name { flex: 1; color: var(--text-main); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mb-item-r { color: #ff5252; font-weight: 700; font-size: 0.6875rem; }
.mb-item-str { color: var(--text-muted); font-size: 0.6875rem; font-variant-numeric: tabular-nums; min-width: 22px; text-align: right; }
.mb-item-str.hot { color: #ffb400; font-weight: 700; }
.mb-item-net { color: var(--text-muted); font-size: 0.6875rem; font-variant-numeric: tabular-nums; min-width: 38px; text-align: right; }

.mb-right { overflow-x: auto; }
.mb-stocks { width: 100%; border-collapse: collapse; font-size: 0.75rem; table-layout: fixed; }
.mb-stocks th, .mb-stocks td { padding: 5px 6px; text-align: left; border-bottom: 1px solid var(--border-soft); white-space: nowrap; }
.mb-stocks th { color: var(--text-muted); font-weight: 500; font-size: 0.6875rem; }
.mb-stocks .r { text-align: right; font-variant-numeric: tabular-nums; }
.mb-stocks tbody tr { cursor: pointer; }
.mb-stocks tbody tr:hover td { background: rgba(255,180,0,.06); }
.mb-sname { color: var(--text-main); }
.mb-scode { color: var(--text-muted); font-size: 0.625rem; font-family: ui-monospace, monospace; }
.mb-tag { display: inline-block; margin-left: 4px; font-size: 0.625rem; color: #ff5252; }
.mb-concept { color: var(--text-muted); font-size: 0.6875rem; max-width: 140px; overflow: hidden; text-overflow: ellipsis; }
.mb-stocks th.sortable { cursor: pointer; }
.mb-stocks th.sortable:hover { color: var(--text-main); }
.mb-stocks th.on { color: var(--accent, #ff7a5c); }
.up { color: #ff5252; }
.down { color: #00c864; }
.dim { color: var(--text-muted); }

@media (max-width: 768px) {
  .mb-split { grid-template-columns: 1fr; }
  .mb-left { max-height: 200px; }
  .mb-stocks { min-width: 720px; }
}
</style>
