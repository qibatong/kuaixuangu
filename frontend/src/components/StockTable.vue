<template>
  <div class="stock-table-container">
    <div v-if="!stocks.length" class="empty-state">暂无符合条件股票</div>
    <table v-else class="stock-table">
      <thead>
        <tr><th>排名</th><th>股票代码</th><th>股票名称</th><th>抢筹</th><th>竞价涨幅</th><th>实时涨幅</th><th>实体涨幅</th><th>异动</th><th>竞价金额(万)</th><th>竞价/昨比</th><th>流通市值(亿)</th><th>行业</th><th>概念</th><th>综合评分</th><th>可信度</th></tr>
      </thead>
      <tbody>
        <tr v-for="(item, idx) in stocks" :key="item.code">
          <td class="rank-col">{{ idx + 1 }}</td>
          <td class="code-click" @click="linkToSoftware(item.code)">{{ item.code }}</td>
          <td>{{ item.name }}</td>
          <td>
            <span v-if="item.qiangchou" class="qc-badge" title="竞价涨幅≥2% 且 竞价/昨比≥20%">🔥抢筹</span>
            <span v-else-if="item._snapshot || isAuction" title="竞价涨幅≥2% 且 竞价/昨比≥20%">-</span>
            <span v-else class="qc-pending" title="9:25-9:30 竞价时段才判定抢筹信号">竞价时</span>
          </td>
          <td :class="item.bidChange > 0 ? 'up' : 'down'">{{ signed(item.bidChange) }}%</td>
          <td :class="realCls(item)">{{ signed(item.realChange) }}%</td>
          <td :class="item.entityChange > 0 ? 'up' : 'down'">{{ signed(item.entityChange) }}%</td>
          <td>{{ warnLabel(item.warnType) }}</td>
          <td>{{ bidAmtText(item.bidAmt) }}</td>
          <td :class="ratioCls(item.bidRatio)">{{ ratioText(item.bidRatio) }}</td>
          <td>{{ item.circulationMV.toFixed(1) }}</td>
          <td>{{ item.industry }}</td>
          <td style="max-width:180px;white-space:pre-wrap">{{ item.concept }}</td>
          <td class="up">{{ item.probability }}分</td>
          <td>{{ item.confidence }}%</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { linkToSoftware } from '../utils/tdx'
import { isBefore930 } from '../utils/time'

defineProps({
  stocks: { type: Array, default: () => [] }
})

// 是否处于竞价时段(9:30 前): 非竞价时段不判定抢筹, 显示"竞价时"
const isAuction = isBefore930()

function signed(v) { return (v > 0 ? '+' : '') + v.toFixed(2) }
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
@keyframes qc-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.55; }
}
</style>
