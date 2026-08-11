<template>
  <div class="stock-table-container">
    <div v-if="!stocks.length" class="empty-state">暂无符合条件股票</div>

    <!-- 盘中实时模式表 -->
    <table v-else-if="mode === 'spot'" class="stock-table">
      <thead>
        <tr>
          <th>排名</th>
          <th class="sortable" :class="{ active: sortKey === 'code' }" @click="onSort('code', 'string')">股票代码<span class="sort-ind">{{ sortInd('code') }}</span></th>
          <th class="sortable" :class="{ active: sortKey === 'name' }" @click="onSort('name', 'string')">股票名称<span class="sort-ind">{{ sortInd('name') }}</span></th>
          <th class="sortable num" :class="{ active: sortKey === 'realChange' }" @click="onSort('realChange', 'number')" title="实时涨幅：当前价相对昨收的涨幅">实时涨幅<span class="sort-ind">{{ sortInd('realChange') }}</span></th>
          <th class="sortable num" :class="{ active: sortKey === 'bidChange' }" @click="onSort('bidChange', 'number')" title="竞价涨幅：集合竞价撮合价相对昨收的涨幅，9:25 定格">竞价涨幅<span class="sort-ind">{{ sortInd('bidChange') }}</span></th>
          <th class="sortable num" :class="{ active: sortKey === 'volRatio' }" @click="onSort('volRatio', 'number')" title="量比：当前每分钟平均成交量 / 过去5日每分钟平均成交量。≥2 显著放量">量比<span class="sort-ind">{{ sortInd('volRatio') }}</span></th>
          <th class="sortable num" :class="{ active: sortKey === 'turnover' }" @click="onSort('turnover', 'number')" title="实时换手率：成交量/流通股本">换手率<span class="sort-ind">{{ sortInd('turnover') }}</span></th>
          <th class="sortable num" :class="{ active: sortKey === 'sealRatio' }" @click="onSort('sealRatio', 'number')" title="封单强度 = 封单金额/流通市值(封成比)。≥2% 强封单；非涨停股为 0">封单强度<span class="sort-ind">{{ sortInd('sealRatio') }}</span></th>
          <th class="sortable num" :class="{ active: sortKey === 'sealFund' }" @click="onSort('sealFund', 'number')" title="封单金额(亿)：涨停板排队买入资金">封单(亿)<span class="sort-ind">{{ sortInd('sealFund') }}</span></th>
          <th class="sortable num" :class="{ active: sortKey === 'limitBoards' }" @click="onSort('limitBoards', 'number')" title="连板数：连续涨停天数">连板<span class="sort-ind">{{ sortInd('limitBoards') }}</span></th>
          <th class="sortable num" :class="{ active: sortKey === 'amount' }" @click="onSort('amount', 'number')" title="今日累计成交额(亿)">成交额(亿)<span class="sort-ind">{{ sortInd('amount') }}</span></th>
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
          <td class="code-click" @click="linkToSoftware(item.code)">{{ item.code }}</td>
          <td>{{ item.name }}</td>
          <td :class="item.realChange > 0 ? 'up' : 'down'" :title="'实时涨幅: 当前价相对昨收'">{{ signed(item.realChange) }}%</td>
          <td :class="item.bidChange > 0 ? 'up' : 'down'" :title="'竞价涨幅: 集合竞价撮合价相对昨收的涨幅'">{{ signed(item.bidChange) }}%</td>
          <td :class="item.volRatio >= 2 ? 'ratio-hot' : item.volRatio >= 1 ? 'ratio-warm' : ''">{{ item.volRatio.toFixed(2) }}</td>
          <td :class="item.turnover >= 3 ? 'ratio-hot' : ''">{{ item.turnover.toFixed(2) }}%</td>
          <td :class="item.sealRatio >= 2 ? 'accel-hot' : ''" :title="item.sealRatio > 0 ? '封成比 ' + item.sealRatio.toFixed(2) + '%' : '非涨停/无封单'">
            {{ item.sealRatio > 0 ? item.sealRatio.toFixed(2) + '%' : '-' }}
          </td>
          <td>{{ item.sealFund > 0 ? item.sealFund.toFixed(2) : '-' }}</td>
          <td>{{ item.limitBoards > 0 ? item.limitBoards + '板' : '-' }}</td>
          <td>{{ item.amount > 0 ? item.amount.toFixed(2) : '-' }}</td>
          <td>{{ item.circulationMV.toFixed(1) }}</td>
          <td>{{ item.industry }}</td>
          <td style="max-width:180px;white-space:pre-wrap">{{ item.concept }}</td>
          <td class="up">{{ item.probability }}分</td>
          <td>{{ item.confidence }}%</td>
          <td><button class="pool-add-btn" :class="{ added: inPool(item.code) }" @click.stop="addToPool(item)">{{ inPool(item.code) ? '已入池' : '＋池' }}</button></td>
        </tr>
      </tbody>
    </table>

    <!-- 竞价模式表 -->
    <table v-else class="stock-table">
      <thead>
        <tr>
          <th>排名</th>
          <th class="sortable" :class="{ active: sortKey === 'code' }" @click="onSort('code', 'string')">股票代码<span class="sort-ind">{{ sortInd('code') }}</span></th>
          <th class="sortable" :class="{ active: sortKey === 'name' }" @click="onSort('name', 'string')">股票名称<span class="sort-ind">{{ sortInd('name') }}</span></th>
          <th class="sortable num" :class="{ active: sortKey === 'realChange' }" @click="onSort('realChange', 'number')" title="实时涨幅：当前价相对昨收的涨幅">实时涨幅<span class="sort-ind">{{ sortInd('realChange') }}</span></th>
          <th class="sortable" :class="{ active: sortKey === 'qiangchou' }" @click="onSort('qiangchou', 'number')" title="竞价涨幅≥2% 且 竞价/昨比≥20% 时标记 🔥抢筹：代表资金在集合竞价阶段大幅抢筹，是当日强势启动的先行信号">抢筹<span class="sort-ind">{{ sortInd('qiangchou') }}</span></th>
          <th class="sortable num" :class="{ active: sortKey === 'bidChange' }" @click="onSort('bidChange', 'number')" title="竞价涨幅">竞价涨幅<span class="sort-ind">{{ sortInd('bidChange') }}</span></th>
          <th class="sortable num" :class="{ active: sortKey === 'accel' }" @click="onSort('accel', 'number')" title="最后5分钟抢筹加速度：9:25竞价涨幅 − 9:20竞价涨幅(百分点)。正值=9:20后资金加速抢筹，≥+1.5% 显著(红色加粗)；负值=竞价冲高回落，警惕">加速度<span class="sort-ind">{{ sortInd('accel') }}</span></th>
          <th class="sortable num" :class="{ active: sortKey === 'entityChange' }" @click="onSort('entityChange', 'number')">实体涨幅<span class="sort-ind">{{ sortInd('entityChange') }}</span></th>
          <th class="sortable" :class="{ active: sortKey === 'warnType' }" @click="onSort('warnType', 'number')">异动<span class="sort-ind">{{ sortInd('warnType') }}</span></th>
          <th class="sortable num" :class="{ active: sortKey === 'bidAmt' }" @click="onSort('bidAmt', 'number')">竞价金额(万)<span class="sort-ind">{{ sortInd('bidAmt') }}</span></th>
          <th class="sortable num" :class="{ active: sortKey === 'bidRatio' }" @click="onSort('bidRatio', 'number')" title="竞价成交额 ÷ 前一交易日全天成交额(%)。衡量竞价资金强度：值越高说明竞价阶段成交越活跃；≥20% 视为强抢筹（配合抢筹列使用）。非竞价时段/无数据时显示 -">竞价/昨比<span class="sort-ind">{{ sortInd('bidRatio') }}</span></th>
          <th class="sortable num" :class="{ active: sortKey === 'circulationMV' }" @click="onSort('circulationMV', 'number')">流通市值(亿)<span class="sort-ind">{{ sortInd('circulationMV') }}</span></th>
          <th class="sortable" :class="{ active: sortKey === 'industry' }" @click="onSort('industry', 'string')">行业<span class="sort-ind">{{ sortInd('industry') }}</span></th>
          <th class="sortable" :class="{ active: sortKey === 'concept' }" @click="onSort('concept', 'string')">概念<span class="sort-ind">{{ sortInd('concept') }}</span></th>
          <th class="sortable num" :class="{ active: sortKey === 'probability' }" @click="onSort('probability', 'number')">综合评分<span class="sort-ind">{{ sortInd('probability') }}</span></th>
          <th class="sortable num" :class="{ active: sortKey === 'confidence' }" @click="onSort('confidence', 'number')">可信度<span class="sort-ind">{{ sortInd('confidence') }}</span></th>
          <th class="op-col">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(item, idx) in sortedStocks" :key="item.code" :class="{ 'row-offline': item._offline }">
          <td class="rank-col">{{ idx + 1 }}</td>
          <td class="code-click" @click="linkToSoftware(item.code)">{{ item.code }}</td>
          <td>{{ item.name }}<span v-if="item._offline" class="offline-tag" title="9:30 竞价锁定名单中的股票，当前实时榜已无此票（竞价结论恒定保留）">已跌出</span></td>
          <td :class="realCls(item)">{{ signed(item.realChange) }}%</td>
          <td>
            <span v-if="item.qiangchou" class="qc-badge" title="竞价涨幅≥2% 且 竞价/昨比≥20%">🔥抢筹</span>
            <span v-else-if="item._snapshot || isAuction" title="竞价涨幅≥2% 且 竞价/昨比≥20%">-</span>
            <span v-else class="qc-pending" title="9:25-9:30 竞价时段才判定抢筹信号">竞价时</span>
          </td>
          <td :class="item.bidChange > 0 ? 'up' : 'down'" :title="'竞价涨幅: 集合竞价撮合价相对昨收的涨幅'">{{ signed(item.bidChange) }}%</td>
          <td :class="accelCls(item.accel)" :title="accelTitle(item.accel)">{{ accelText(item.accel) }}</td>
          <td :class="item.entityChange > 0 ? 'up' : 'down'">{{ signed(item.entityChange) }}%</td>
          <td>{{ warnLabel(item.warnType) }}</td>
          <td :title="'集合竞价阶段撮合成交金额(万元)'">{{ bidAmtText(item.bidAmt) }}</td>
          <td :class="ratioCls(item.bidRatio)" :title="ratioTitle(item.bidRatio)">{{ ratioText(item.bidRatio) }}</td>
          <td>{{ item.circulationMV.toFixed(1) }}</td>
          <td>{{ item.industry }}</td>
          <td style="max-width:180px;white-space:pre-wrap">{{ item.concept }}</td>
          <td class="up">{{ item.probability }}分</td>
          <td>{{ item.confidence }}%</td>
          <td><button class="pool-add-btn" :class="{ added: inPool(item.code) }" @click.stop="addToPool(item)">{{ inPool(item.code) ? '已入池' : '＋池' }}</button></td>
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
  mode: { type: String, default: 'auction' }
})

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

// 手动收录单只股票到策略池(任意数量)
function addToPool(item) {
  const n = pool.addStocks([item])
  showToast(n ? `✅ ${item.code} ${item.name} 已加入股票池` : `${item.code} 已在池中`, n ? 'success' : 'info')
}
// 是否已在池中
function inPool(code) {
  return pool.stockPool.some(x => x.code === code)
}
function accelText(v) {
  if (v === null || v === undefined || isNaN(v)) return '-'
  return (v > 0 ? '+' : '') + v.toFixed(2) + '%'
}
function accelCls(v) {
  if (v === null || v === undefined || isNaN(v)) return 'dim'
  return v >= 1.5 ? 'accel-hot' : v > 0 ? 'up' : v < 0 ? 'down' : 'dim'
}
function accelTitle(v) {
  if (v === null || v === undefined || isNaN(v)) return '最后5分钟抢筹加速度：9:25竞价涨幅 − 9:20竞价涨幅（需当天9:20自动采集，非竞价时段显示 -）'
  return `9:25竞价涨幅 − 9:20竞价涨幅 = ${v > 0 ? '+' : ''}${v.toFixed(2)}%${v >= 1.5 ? '（显著抢筹，资金最后5分钟加速买入）' : v > 0 ? '（小幅走强）' : v < 0 ? '（竞价冲高回落，谨慎）' : ''}`
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
  color: #ff8a65;
}
th.sortable.active {
  color: #ff5028;
}
.sort-ind {
  display: inline-block;
  width: 10px;
  color: #ff5028;
  font-weight: 700;
}
th.sortable:hover .sort-ind:not(:empty),
th.sortable.active .sort-ind {
  opacity: 1;
}
th.sortable .sort-ind:empty::before {
  content: '↕';
  opacity: 0.25;
  font-weight: 400;
}
.qc-badge {
  display: inline-block;
  background: rgba(255, 80, 40, 0.18);
  border: 1px solid #ff5028;
  color: #ffa07a;
  border-radius: 4px;
  padding: 0 6px;
  font-size: 12px;
  animation: qc-pulse 1.6s ease-in-out infinite;
}
.qc-pending {
  color: #777;
  font-size: 12px;
  border: 1px dashed #555;
  border-radius: 4px;
  padding: 0 6px;
}
.accel-hot {
  color: #ff5028;
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
.offline-tag {
  margin-left: 6px;
  font-size: 11px;
  color: #888;
  border: 1px dashed #666;
  border-radius: 4px;
  padding: 0 5px;
}
@keyframes qc-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.55; }
}
</style>