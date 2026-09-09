<template>
  <div class="stock-table-container">
    <div v-if="!stocks.length" class="empty-state">暂无符合条件股票</div>

    <!-- 选股表(竞价/盘中共用同一套列; 对齐生产机紧凑布局: 固定列宽, 代码+名称合并) -->
    <table v-else class="stock-table stock-table-compact">
      <colgroup>
        <col style="width:88px" />
        <col style="width:46px" />
        <col style="width:54px" />
        <col style="width:46px" />
        <col style="width:50px" />
        <col style="width:40px" />
        <col style="width:64px" />
        <col style="width:52px" />
        <col style="width:42px" />
        <col style="width:52px" />
        <col style="width:72px" />
      </colgroup>
      <thead>
        <tr>
          <th class="sortable merged-col col-name" :class="{ active: sortKey === 'code' || sortKey === 'name' }" @click="onSort('code', 'string')">名称<span class="sort-ind">{{ sortInd('code') }}</span></th>
          <th class="sortable num col-realchg" :class="{ active: sortKey === 'realChange' }" title="实时涨幅：当前价相对昨收的涨幅" @click="onSort('realChange', 'number')">现涨<span class="sort-ind">{{ sortInd('realChange') }}</span></th>
          <th class="sortable col-qc" :class="{ active: sortKey === 'qiangchou' }" title="命中竞价异动-竞价抢筹时标记 🔥：与右视图竞价抢筹同源（9:20→9:25 竞额/涨幅抢筹 + 9:24→9:25 最后一秒）。徽章文字=抢筹类型（竞额/涨幅/末秒），悬停看各自幅度" @click="onSort('qiangchou', 'number')">抢筹<span class="sort-ind">{{ sortInd('qiangchou') }}</span></th>
          <th class="sortable num col-bidchg" :class="{ active: sortKey === 'bidChange' }" title="竞价涨幅" @click="onSort('bidChange', 'number')">竞涨<span class="sort-ind">{{ sortInd('bidChange') }}</span></th>
          <th class="sortable num col-entchg" :class="{ active: sortKey === 'entityChange' }" @click="onSort('entityChange', 'number')">实体<span class="sort-ind">{{ sortInd('entityChange') }}</span></th>
          <th class="sortable col-warn" :class="{ active: sortKey === 'warnType' }" @click="onSort('warnType', 'number')">异动<span class="sort-ind">{{ sortInd('warnType') }}</span></th>
          <th class="sortable num col-bidamt" :class="{ active: sortKey === 'bidAmt' }" title="集合竞价阶段撮合成交金额" @click="onSort('bidAmt', 'number')">竞额<span class="sort-ind">{{ sortInd('bidAmt') }}</span></th>
          <th class="sortable num col-mv" :class="{ active: sortKey === 'circulationMV' }" @click="onSort('circulationMV', 'number')">流通<span class="sort-ind">{{ sortInd('circulationMV') }}</span></th>
          <th class="sortable num col-score" :class="{ active: sortKey === 'probability' }" @click="onSort('probability', 'number')">评分<span class="sort-ind">{{ sortInd('probability') }}</span></th>
          <th class="sortable num col-conf" :class="{ active: sortKey === 'confidence' }" @click="onSort('confidence', 'number')">可信<span class="sort-ind">{{ sortInd('confidence') }}</span></th>
          <th class="sortable col-concept" :class="{ active: sortKey === 'concept' }" @click="onSort('concept', 'string')">概念<span class="sort-ind">{{ sortInd('concept') }}</span></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(item, idx) in sortedStocks" :key="item.code">
          <td class="stock-info-cell" :data-stock-code="item.code" :data-stock-name="item.name" @click="emit('open-chart', item.code, item.name)">
            <div class="stock-name-row">
              <span class="pool-hover-wrap">
                <span class="stock-name">{{ item.name }}</span>
                <PoolHoverBtn :item="item" />
              </span>
            </div>
            <div class="stock-code-row"><span class="stock-code">{{ item.code }}</span></div>
            <div v-if="yidongTag(item.code)" class="yd-badge-row"><span class="yd-badge">{{ yidongTag(item.code) }}</span></div>
          </td>
          <td :class="item.realChange === null || item.realChange === undefined ? 'dim' : realCls(item)" :title="item.realChange === null || item.realChange === undefined ? ('无实时行情数据（竞价锁定时刻 ' + fmtPct(item._staleReal) + '）') : ''">{{ item.realChange === null || item.realChange === undefined ? '-' : signed(item.realChange) + '%' }}</td>
          <td>
            <span v-if="item.qiangchou" class="qc-badge" :title="qcTitle(item)">🔥{{ qcLabel(item) }}</span>
            <span v-else-if="item._snapshot || isAuction" title="竞价异动-竞价抢筹未命中(9:20→9:25 竞价涨幅 / 最后一秒竞价涨幅)">-</span>
            <span v-else class="qc-pending" title="9:25-9:30 竞价时段才判定抢筹信号">竞价时</span>
          </td>
          <td :class="item.bidChange > 0 ? 'up' : 'down'" :title="'竞价涨幅: 集合竞价撮合价相对昨收的涨幅'">{{ signed(item.bidChange) }}%</td>
          <td :class="item.entityChange === null || item.entityChange === undefined ? 'dim' : (item.entityChange > 0 ? 'up' : 'down')" :title="item.entityChange === null || item.entityChange === undefined ? ('无实时行情数据（竞价锁定时刻 ' + fmtPct(item._staleEntity) + '）') : ''">{{ item.entityChange === null || item.entityChange === undefined ? '-' : signed(item.entityChange) + '%' }}</td>
          <td>{{ warnLabel(item.warnType) }}</td>
          <td :title="'集合竞价阶段撮合成交金额: ' + (item.bidAmt ? bidAmtText(item.bidAmt) : '-')">{{ item.bidAmt || item.bidAmt === 0 ? bidAmtText(item.bidAmt) : '-' }}</td>
          <td>{{ item.circulationMV ? item.circulationMV.toFixed(1) : '-' }}</td>
          <td class="score-cell">{{ item.probability }}分</td>
          <td>{{ item.confidence }}%</td>
          <td class="concept-cell" :title="'概念: ' + (item.concept || '')">
            <template v-if="item.concept">
              <span v-for="(c, i) in conceptList(item.concept)" :key="i" class="concept-item">{{ c }}</span>
            </template>
            <span v-else>-</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { linkToSoftware } from '../utils/tdx'
import { isBefore930 } from '../utils/time'
import { useYidongMonitor } from '../composables/useYidongMonitor'
import PoolHoverBtn from './PoolHoverBtn.vue'

const { yidongTag } = useYidongMonitor()

const props = defineProps({
  stocks: { type: Array, default: () => [] },
  mode: { type: String, default: 'auction' },
  bidSealMap: { type: Object, default: () => ({}) }  // code -> {limitBoards, bidSealAmt, bidNetAmt}
})

const emit = defineEmits(['open-chart'])

// ---- 抢筹细分(2026-09-09): 左视图区分竞额/涨幅/末秒抢筹并展示幅度 ----
const QC_LABEL = { amt: '竞额', chg: '涨幅', last: '末秒' }

function qcTypes(item) {
  return (item.qcType || '').split('+').filter((t) => QC_LABEL[t])
}

function qcLabel(item) {
  const ts = qcTypes(item)
  if (!ts.length) return '抢筹'
  return QC_LABEL[ts[0]] + (ts.length > 1 ? '·' + QC_LABEL[ts[1]] : '')
}

function qcTitle(item) {
  const base = '竞价异动-竞价抢筹(与右视图同源)'
  if (item.qcText) {
    return base + '：' + item.qcText +
      (item.qcFallback ? '（数据源异常，已用公式兜底，口径与右视图不同）' : '')
  }
  return item.qiangchou ? base + '：命中抢筹（本批次未记录细分幅度）'
    : '竞价异动-竞价抢筹未命中'
}

// 是否处于竞价时段(9:30 前): 非竞价时段不判定抢筹, 显示"竞价时"
const isAuction = isBefore930()

// 排序状态: { key: 'bidChange', dir: 'asc' | 'desc' } 或 null
const sortState = ref(null)

// 切换排序状态: 无 → 降序(默认, 数值越大越靠前) → 升序 → 无
// 字符串列默认升序(字典序), 数值列默认降序
function onSort(key, type) {
  if (!sortState.value || sortState.value.key !== key) {
    sortState.value = { key, dir: type === 'string' ? 'asc' : 'desc' }
  } else if (sortState.value.dir === 'desc') {
    sortState.value = { key, dir: 'asc' }
  } else {
    sortState.value = null
  }
}

function sortInd(key) {
  if (!sortState.value || sortState.value.key !== key) return ''
  return sortState.value.dir === 'desc' ? ' ↓' : ' ↑'
}

const sortKey = computed(() => sortState.value ? sortState.value.key : null)

// 排序后的列表; null/undefined 始终排到末尾(无论升降)
const sortedStocks = computed(() => {
  if (!sortState.value) return props.stocks
  const { key, dir } = sortState.value
  const colDef = columnType(key)
  const mult = dir === 'asc' ? 1 : -1
  return [...props.stocks].sort((a, b) => {
    const av = a[key], bv = b[key]
    // null/undefined 排到末尾
    const aNull = av === null || av === undefined
    const bNull = bv === null || bv === undefined
    if (aNull && bNull) return 0
    if (aNull) return 1
    if (bNull) return -1
    if (colDef === 'string') return mult * String(av).localeCompare(String(bv), 'zh-Hans-CN')
    return mult * (av - bv)
  })
})

// 列类型映射(影响默认排序方向和比较方式)
function columnType(key) {
  const strKeys = new Set(['code', 'name', 'industry', 'concept'])
  return strKeys.has(key) ? 'string' : 'number'
}

function signed(v) { return (v > 0 ? '+' : '') + v.toFixed(2) }

// 概念最多显示前 2 个, 每个概念独立一行(换行显示, 而非顿号/空格拼接; 完整概念放 title hover)
function conceptList(c) {
  if (!c) return []
  return String(c).split(/[、,，]/).map(s => s.trim()).filter(Boolean).slice(0, 2)
}

// 涨跌百分比显示(兼容 null/undefined, 用于 tooltip 的锁定时刻值)
function fmtPct(v) {
  if (v === null || v === undefined || isNaN(v)) return '-'
  return (v > 0 ? '+' : '') + Number(v).toFixed(2) + '%'
}

// 2026-09-03 主人要求恢复: 竞价选股表重新展示「竞额」列(bidAmt, 万元); amt>=10000万(1亿)折算显示亿
function bidAmtText(amt) {
  if (amt === null || amt === undefined || isNaN(amt)) return '-'
  return amt >= 10000 ? (amt / 10000).toFixed(2) + '亿' : Math.round(amt).toFixed(0)
}
function realCls(item) {
  if (item.realChange < item.bidChange) return 'real-green'
  return item.realChange > 0 ? 'up' : 'down'
}
function warnLabel(w) {
  return w === 5 ? '强' : w === 4 ? '⚡中' : w === 3 ? '↑弱' : '-'
}
</script>

<style scoped>
/* 紧凑表(对齐生产机): 固定列宽 + 紧凑字号 */
.stock-table-compact {
  table-layout: fixed;
  min-width: 0;
  width: 100%;
}
.stock-table-compact th,
.stock-table-compact td {
  text-align: center;
  vertical-align: middle;
  padding: 5px 2px !important;
  font-size: 11.5px !important;
}
.stock-table-compact th {
  padding: 7px 2px !important;
  font-size: 11.5px !important;
}
.stock-table-compact th.sortable {
  white-space: nowrap;
}
.concept-cell {
  width: 72px;
  min-width: 0;
  white-space: normal;
  line-height: 1.3;
  font-size: 11.5px;
  color: var(--text-secondary);
  padding: 4px 2px !important;
}
.concept-cell .concept-item {
  display: block;
  line-height: 1.4;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}
.stock-table-compact .qc-badge,
.stock-table-compact .qc-pending {
  padding: 1px 4px;
  font-size: 11px;
  border-radius: 3px;
}
.stock-table-compact .pool-add-btn {
  padding: 1px 5px;
  font-size: 11px;
  border-radius: 3px;
}
/* 首列"名称": 代码+名称 上下排布, 可点击打开图表 */
.stock-info-cell {
  cursor: pointer;
  min-width: 0;
  min-height: 0;
  height: 52px;
  text-align: center;
  padding: 4px 2px !important;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}
.stock-info-cell .stock-name-row {
  order: 1;
  display: block;
  align-items: center;
  justify-content: center;
  gap: 0;
  line-height: 1.3;
}
.stock-info-cell .stock-name {
  font-weight: 600;
  color: var(--text-main);
  font-size: 12.5px;
}
/* 异动监管标签行: 始终占用固定高度(无标签也占位), 保证各列网格线对齐 */
.yd-badge-row {
  order: 3;
  height: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: 2px;
}
.yd-badge {
  display: inline-block;
  font-size: 10px;
  line-height: 1;
  padding: 1px 5px;
  border-radius: 3px;
  border: 1px solid #ffd700;
  color: #ffd700;
  white-space: nowrap;
}
.stock-info-cell .stock-code-row {
  order: 2;
  line-height: 1.2;
  text-align: center;
  margin-top: 2px;
}
.stock-info-cell .stock-code {
  font-family: "LXGW WenKai Mono", monospace;
  font-size: 10.5px;
  color: var(--text-muted);
  letter-spacing: 0.5px;
}
.stock-info-cell:hover .stock-name,
.stock-info-cell:hover .stock-code {
  color: var(--accent);
}
th.sortable {
  cursor: pointer;
  user-select: none;
}
th.sortable:hover {
  color: var(--accent);
}
th.sortable.active {
  color: var(--accent-deep);
}
.sort-ind {
  display: inline-block;
  width: 10px;
  color: var(--accent-deep);
  font-weight: 700;
}
th.sortable:hover .sort-ind:not(:empty),
th.sortable.active .sort-ind {
  opacity: 1;
}
th.sortable .sort-ind:empty::before {
  content: '↕';
  opacity: 0.6;   /* 默认排序提示: 0.25 太淡几乎不可见, 提到 0.6 */
  font-weight: 400;
}
/* 浅色主题: 排序箭头用深橙保证可见 */
body[data-bg="light"] .sort-ind { color: #b83010; }
body[data-bg="light"] th.sortable:hover { color: #b83010; }
body[data-bg="light"] th.sortable.active { color: #c00; }
.qc-badge {
  display: inline-block;
  background: rgba(var(--accent-rgb), 0.18);
  border: 1px solid var(--accent);
  color: var(--accent-text);
  border-radius: 4px;
  padding: 0 6px;
  font-size: 12px;
  animation: qc-pulse 1.6s ease-in-out infinite;
}
.qc-pending {
  color: var(--text-muted);
  font-size: 12px;
  border: 1px dashed #555;
  border-radius: 4px;
  padding: 0 6px;
}
.accel-hot {
  color: var(--accent-deep);
  font-weight: 700;
}
.op-col {
  min-width: 56px;
}
.pool-add-btn {
  background: rgba(120, 200, 80, 0.15);
  border: 1px solid #78c850;
  color: #c0e8a0;
  border-radius: 4px;
  padding: 2px 8px;
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
}
.pool-add-btn:hover {
  background: rgba(120, 200, 80, 0.3);
}
.pool-add-btn.added {
  background: rgba(120, 200, 80, 0.35);
  border-color: #78c850;
  color: #e8ffd0;
  cursor: default;
}
.row-offline td { opacity: 0.55; }
.name-col { min-width: 90px; }
.name-main { line-height: 1.4; }
.offline-tag {
  display: inline-block;
  margin-top: 3px;
  font-size: 10px;
  line-height: 1.3;
  color: var(--text-muted);
  border: 1px dashed #777;
  border-radius: 4px;
  padding: 1px 5px;
}
@keyframes qc-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.55; }
}

/* 浅色主题覆盖 */
body[data-bg="light"] th.sortable {  color: #5a4a3a;  }
body[data-bg="light"] th.sortable:hover {  color: #b83010;  }
body[data-bg="light"] th.sortable.active {  color: #b83010;  }
body[data-bg="light"] .sort-ind {  color: #b83010;  }
body[data-bg="light"] .qc-badge {  color: #8a5500; background: rgba(184,48,16,0.15); border-color: #b83010;  }
body[data-bg="light"] .qc-pending {  color: #8a8a8a; border-color: #999;  }
</style>