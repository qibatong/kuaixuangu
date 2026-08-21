<template>
  <div class="stock-table-container">
    <div v-if="!stocks.length" class="empty-state">暂无符合条件股票</div>

    <!-- 选股表(竞价/盘中共用同一套列; 盘中=不锁定的竞价, 逻辑一致) -->
    <table v-else class="stock-table">
      <thead>
        <tr>
          <th>排名</th>
          <th class="sortable" :class="{ active: sortKey === 'code' }" @click="onSort('code', 'string')">股票代码<span class="sort-ind">{{ sortInd('code') }}</span></th>
          <th class="sortable" :class="{ active: sortKey === 'name' }" @click="onSort('name', 'string')">股票名称<span class="sort-ind">{{ sortInd('name') }}</span></th>
          <th class="sortable num" :class="{ active: sortKey === 'realChange' }" title="实时涨幅：当前价相对昨收的涨幅" @click="onSort('realChange', 'number')">实时涨幅<span class="sort-ind">{{ sortInd('realChange') }}</span></th>
          <th class="sortable" :class="{ active: sortKey === 'qiangchou' }" title="竞价涨幅≥2% 且 竞价/昨比≥20% 时标记 🔥抢筹：代表资金在集合竞价阶段大幅抢筹，是当日强势启动的先行信号" @click="onSort('qiangchou', 'number')">抢筹<span class="sort-ind">{{ sortInd('qiangchou') }}</span></th>
          <th class="sortable num" :class="{ active: sortKey === 'bidChange' }" title="竞价涨幅" @click="onSort('bidChange', 'number')">竞价涨幅<span class="sort-ind">{{ sortInd('bidChange') }}</span></th>
          <th class="sortable num" :class="{ active: sortKey === 'entityChange' }" @click="onSort('entityChange', 'number')">实体涨幅<span class="sort-ind">{{ sortInd('entityChange') }}</span></th>
          <th class="sortable" :class="{ active: sortKey === 'warnType' }" @click="onSort('warnType', 'number')">异动<span class="sort-ind">{{ sortInd('warnType') }}</span></th>
          <th class="sortable num" :class="{ active: sortKey === 'bidAmt' }" @click="onSort('bidAmt', 'number')">竞价金额(万)<span class="sort-ind">{{ sortInd('bidAmt') }}</span></th>
          <th class="sortable num" :class="{ active: sortKey === 'bidRatio' }" title="竞价成交额 ÷ 前一交易日全天成交额(%)。衡量竞价资金强度：值越高说明竞价阶段成交越活跃；≥20% 视为强抢筹（配合抢筹列使用）。非竞价时段/无数据时显示 -" @click="onSort('bidRatio', 'number')">竞价/昨比<span class="sort-ind">{{ sortInd('bidRatio') }}</span></th>
          <th class="sortable num" :class="{ active: sortKey === 'circulationMV' }" @click="onSort('circulationMV', 'number')">流通市值(亿)<span class="sort-ind">{{ sortInd('circulationMV') }}</span></th>
          <th class="sortable" :class="{ active: sortKey === 'industry' }" @click="onSort('industry', 'string')">行业<span class="sort-ind">{{ sortInd('industry') }}</span></th>
          <th class="sortable" :class="{ active: sortKey === 'concept' }" @click="onSort('concept', 'string')">概念<span class="sort-ind">{{ sortInd('concept') }}</span></th>
          <th class="sortable num" :class="{ active: sortKey === 'probability' }" @click="onSort('probability', 'number')">综合评分<span class="sort-ind">{{ sortInd('probability') }}</span></th>
          <th class="sortable num" :class="{ active: sortKey === 'confidence' }" @click="onSort('confidence', 'number')">可信度<span class="sort-ind">{{ sortInd('confidence') }}</span></th>
          <th class="op-col">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(item, idx) in sortedStocks" :key="item.code">
          <td class="rank-col">{{ idx + 1 }}</td>
          <td class="code-click" @click="emit('open-chart', item.code, item.name)">{{ item.code }}</td>
          <td class="name-col" @click="emit('open-chart', item.code, item.name)">
            <div class="name-main">{{ item.name }}</div>
            <div v-if="ladderLabel(item.code)" class="ladder-tag" :title="sealTitle(item.code)">{{ ladderLabel(item.code) }}</div>
          </td>
          <td :class="item.realChange === null || item.realChange === undefined ? 'dim' : realCls(item)" :title="item.realChange === null || item.realChange === undefined ? ('无实时行情数据（竞价锁定时刻 ' + fmtPct(item._staleReal) + '）') : ''">{{ item.realChange === null || item.realChange === undefined ? '-' : signed(item.realChange) + '%' }}</td>
          <td>
            <span v-if="item.qiangchou" class="qc-badge" title="竞价涨幅≥2% 且 竞价/昨比≥20%">🔥抢筹</span>
            <span v-else-if="item._snapshot || isAuction" title="竞价涨幅≥2% 且 竞价/昨比≥20%">-</span>
            <span v-else class="qc-pending" title="9:25-9:30 竞价时段才判定抢筹信号">竞价时</span>
          </td>
          <td :class="item.bidChange > 0 ? 'up' : 'down'" :title="'竞价涨幅: 集合竞价撮合价相对昨收的涨幅'">{{ signed(item.bidChange) }}%</td>
          <td :class="item.entityChange === null || item.entityChange === undefined ? 'dim' : (item.entityChange > 0 ? 'up' : 'down')" :title="item.entityChange === null || item.entityChange === undefined ? ('无实时行情数据（竞价锁定时刻 ' + fmtPct(item._staleEntity) + '）') : ''">{{ item.entityChange === null || item.entityChange === undefined ? '-' : signed(item.entityChange) + '%' }}</td>
          <td>{{ warnLabel(item.warnType) }}</td>
          <td :title="'集合竞价阶段撮合成交金额(万元)'">{{ bidAmtText(item.bidAmt) }}</td>
          <td :class="ratioCls(item.bidRatio)" :title="ratioTitle(item.bidRatio)">{{ ratioText(item.bidRatio) }}</td>
          <td>{{ item.circulationMV.toFixed(1) }}</td>
          <td>{{ item.industry }}</td>
          <td style="max-width:180px;white-space:pre-wrap" :title="'概念: ' + (item.concept || '')">{{ shortConcept(item.concept) }}</td>
          <td class="score-cell" :title="factorTitle(item)">{{ item.probability }}分</td>
          <td>{{ item.confidence }}%</td>
          <td><button class="pool-add-btn" :class="{ added: inPool(item.code) }" @click.stop="addToPool(item)">{{ inPool(item.code) ? '已加自选' : '＋自选' }}</button></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { linkToSoftware } from '../utils/tdx'
import { isBefore930 } from '../utils/time'
import { usePoolStore } from '../stores/pool'
import { showToast } from '../utils/toast'

const pool = usePoolStore()

const props = defineProps({
  stocks: { type: Array, default: () => [] },
  mode: { type: String, default: 'auction' },
  bidSealMap: { type: Object, default: () => ({}) }  // code -> {limitBoards, bidSealAmt, bidNetAmt}
})

const emit = defineEmits(['open-chart'])

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

// 概念只显示前 2 个(开盘啦概念可能 10+ 个板块, 全显太长; 完整放 title hover)
function shortConcept(c) {
  if (!c) return '-'
  const parts = String(c).split(/[、,，]/).map(s => s.trim()).filter(Boolean)
  return parts.slice(0, 2).join('、')
}

// 涨跌百分比显示(兼容 null/undefined, 用于 tooltip 的锁定时刻值)
function fmtPct(v) {
  if (v === null || v === undefined || isNaN(v)) return '-'
  return (v > 0 ? '+' : '') + Number(v).toFixed(2) + '%'
}

// 手动收录单只股票到策略池(任意数量)
function addToPool(item) {
  const n = pool.addStocks([item])
  showToast(n ? `✅ ${item.code} ${item.name} 已加入股票池` : `${item.code} 已在池中`, n ? 'success' : 'info')
}

// 评分构成 tooltip(五因子分项): "竞价涨幅 +3.20% → 88分 (权重34%)" 每行一个
function factorTitle(item) {
  const f = item.factors || {}
  const rows = Object.values(f).map(x => {
    let v = '-'
    if (x.value !== null && x.value !== undefined && !isNaN(x.value)) {
      v = x.label.includes('市值') ? x.value.toFixed(1) + '亿'
        : x.label.includes('等级') ? x.value + '级'
        : (x.value > 0 ? '+' : '') + x.value.toFixed(2) + '%'
    }
    return `${x.label} ${v} → ${x.score}分 (权重${Math.round(x.weight * 100)}%)`
  })
  return '评分构成：\n' + rows.join('\n')
}
// 是否已在池中
function inPool(code) {
  return pool.stockPool.some(x => x.code === code)
}
// 连板标签(来自开盘啦竞价委买额榜): 首板/2连板/3连板...
function ladderLabel(code) {
  const s = props.bidSealMap[code]
  if (!s || !s.limitBoards) return ''
  return s.limitBoards <= 1 ? '首板' : s.limitBoards + '连板'
}
// 涨停委买额 tooltip
function sealTitle(code) {
  const s = props.bidSealMap[code]
  if (!s) return ''
  const seal = s.bidSealAmt || 0
  const net = s.bidNetAmt || 0
  return `涨停委买额 ${(seal / 1e8).toFixed(2)}亿 · 竞价净额 ${(net / 1e8).toFixed(2)}亿`
}
function ratioTitle(br) {
  if (br === null || br === undefined || isNaN(br)) return '竞价成交额 ÷ 前一交易日全天成交额(%)，非竞价时段/无数据时显示 -'
  return `竞价成交额 ÷ 前一交易日全天成交额 = ${br.toFixed(2)}%${br >= 20 ? '（≥20%，强抢筹！）' : br >= 10 ? '（竞价较活跃）' : '（竞价强度一般）'}`
}
function realCls(item) {
  if (item.realChange < item.bidChange) return 'real-green'
  return item.realChange > 0 ? 'up' : 'down'
}
function warnLabel(w) {
  return w === 5 ? '强' : w === 4 ? '⚡中' : w === 3 ? '↑弱' : '-'
}
function bidAmtText(amt) {
  return amt >= 10000 ? (amt / 10000).toFixed(2) + '亿' : amt.toFixed(0)
}
function ratioCls(br) {
  if (br === null || br === undefined || isNaN(br)) return 'dim'
  return br >= 2 ? 'ratio-hot' : br >= 1 ? 'ratio-warm' : ''
}
function ratioText(br) {
  if (br === null || br === undefined || isNaN(br)) return '-'
  return br.toFixed(2) + '%'
}
</script>

<style scoped>
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
.ladder-tag {
  display: inline-block;
  margin-top: 3px;
  font-size: 10px;
  line-height: 1.3;
  color: var(--accent);
  border: 1px solid rgba(255, 80, 40, 0.5);
  border-radius: 4px;
  padding: 1px 5px;
  background: rgba(255, 80, 40, 0.12);
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
body[data-bg="light"] .qc-pending {  color: #6a7a90; border-color: #999;  }
</style>