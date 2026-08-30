<template>
  <div class="medal-section">
    <template v-if="!stocks.length">
      <div class="empty-state" style="width:100%">暂无数据，请先执行选股</div>
    </template>
    <div v-for="(item, i) in top3" :key="item.code" class="medal-card">
      <div class="medal-rank"><span class="medal-rank-icon"></span> {{ ['金牌', '银牌', '铜牌'][i] }}</div>
      <div class="medal-name-big">{{ item.name }}<span v-if="item.qiangchou" class="qc-badge" title="竞价涨幅≥2% 且 竞价/昨比≥20%">🔥抢筹</span></div>
      <div class="medal-code" @click="linkToSoftware(item.code)">{{ item.code }}</div>
      <!-- 实时涨幅顶替原"95分大字"位置(2026-08-18 主人反馈: 盘中关注点, 应是最显眼数字) -->
      <div class="medal-real-big" :class="{ 'green-real': item.realChange < item.bidChange }">
        {{ item.realChange > 0 ? '+' : '' }}{{ item.realChange.toFixed(2) }}%
      </div>
      <!-- 竞涨幅: 缩字号, 实时涨幅下面 -->
      <div class="medal-bid-sm">竞涨幅{{ fmtPct(item.bidChange) }}</div>
      <!-- 评分 + 可信度 合并到最下面一行 -->
      <div class="medal-score-row">
        <span class="medal-prob-sm">{{ item.probability }}分</span>
        <span class="medal-conf">可信{{ item.confidence }}%</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { linkToSoftware } from '../utils/tdx'

const props = defineProps({
  stocks: { type: Array, default: () => [] }
})

const top3 = computed(() => props.stocks.slice(0, 3))

// 涨幅格式化: null/undefined → '-'; >0 加 +
function fmtPct(v) {
  if (v === null || v === undefined || isNaN(v)) return '-'
  return (v > 0 ? '+' : '') + v.toFixed(2) + '%'
}
</script>

<style scoped>
.qc-badge {
  position: absolute;
  top: 8px;
  right: 8px;
  font-size: 14px;
  color: #ffa07a;
  animation: qc-pulse 1.6s ease-in-out infinite;
}
@keyframes qc-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.55; }
}
/* 实时涨幅大字(顶替原评分位置, 2026-08-18 主人反馈) */
.medal-real-big {
  font-size: 56px;
  font-weight: 900;
  color: #ff5252;
  line-height: 1.1;
  margin: 2px 0 0;
  font-family: "LXGW WenKai Mono", monospace;
  letter-spacing: -0.5px;
}
.medal-real-big.green-real { color: #00c864 !important; }
body[data-bg="light"] .medal-real-big { color: #c62828; }
body[data-bg="light"] .medal-real-big.green-real { color: #1a7a2a !important; }
/* 竞涨幅: 缩字号, 实时涨幅下面 */
.medal-bid-sm {
  font-size: 20px;
  color: #ff8a6f;
  font-weight: 600;
  font-family: "LXGW WenKai Mono", monospace;
  margin-top: 1px;
}
body[data-bg="light"] .medal-bid-sm { color: #c0562f; }
/* 评分 + 可信度 一行, 放在最下面 */
.medal-score-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 22px;
  white-space: nowrap;
}
.medal-prob-sm { color: #e0a800; font-weight: 700; }
.medal-conf { color: var(--text-muted); }
body[data-bg="light"] .medal-prob-sm { color: #a06a00; }
body[data-bg="light"] .medal-conf { color: #5a6b85; }
/* 手机端覆盖(2026-08-18 补: desktop 放大字号后, 这3个 scoped 类在 mobile 也要缩小, 否则手机端挤压) */
@media (max-width: 899px) {
  .medal-rank { font-size: 16px; }
  .medal-name-big { font-size: 17px; }
  .medal-code { font-size: 15px; }
  .medal-real-big { font-size: 32px; }
  .medal-bid-sm { font-size: 14px; }
  .medal-score-row { font-size: 14px; gap: 5px; }
}
</style>
