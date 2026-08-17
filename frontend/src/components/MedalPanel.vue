<template>
  <div class="medal-section">
    <template v-if="!stocks.length">
      <div class="empty-state" style="width:100%">暂无数据，请先执行选股</div>
    </template>
    <div v-for="(item, i) in top3" :key="item.code" class="medal-card">
      <div class="medal-rank"><span class="medal-rank-icon"></span> {{ ['金牌', '银牌', '铜牌'][i] }}</div>
      <div class="medal-name-big">{{ item.name }}<span v-if="item.qiangchou" class="qc-badge" title="竞价涨幅≥2% 且 竞价/昨比≥20%">🔥抢筹</span></div>
      <div class="medal-code" @click="linkToSoftware(item.code)">{{ item.code }}</div>
      <div class="medal-prob-big">{{ item.probability }}分</div>
      <!-- 竞涨幅 + 可信度(2026-08-18 主人反馈恢复: 上次去五因子时把可信也删了, 竞涨幅本来就没有) -->
      <div class="medal-sub">
        <span class="medal-bid">竞涨幅{{ fmtPct(item.bidChange) }}</span>
        <span class="medal-conf">可信{{ item.confidence }}%</span>
      </div>
      <div class="medal-real-chg" :class="{ 'green-real': item.realChange < item.bidChange }">实时涨幅{{ item.realChange > 0 ? '+' : '' }}{{ item.realChange.toFixed(2) }}%</div>
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
  margin-left: 6px;
  font-size: 12px;
  color: #ffa07a;
  animation: qc-pulse 1.6s ease-in-out infinite;
}
@keyframes qc-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.55; }
}
/* 竞涨幅 + 可信度 一行(恢复于 2026-08-18 主人反馈) */
.medal-sub {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 1px;
  font-size: 11px;
  white-space: nowrap;
}
.medal-bid { color: #ff8a6f; font-weight: 600; font-family: monospace; }
.medal-conf { color: var(--text-muted); }
body[data-bg="light"] .medal-bid { color: #c0562f; }
body[data-bg="light"] .medal-conf { color: #5a6b85; }
</style>
