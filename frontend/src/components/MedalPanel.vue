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
      <div class="medal-conf">可信{{ item.confidence }}%</div>
      <!-- 评分构成(五因子分项): 让小白看得懂分怎么来的 -->
      <div class="medal-factors">
        <div v-for="f in factorList(item)" :key="f.label" class="factor-row">
          <span class="f-label">{{ f.label }}</span>
          <span class="f-val">{{ fmtVal(f) }}</span>
          <span class="f-score">{{ f.score }}分</span>
          <span class="f-w">×{{ Math.round(f.weight * 100) }}%</span>
        </div>
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

// 五因子明细: {label, value, score, weight} -> 数组
function factorList(item) {
  const f = item.factors || {}
  return Object.values(f)
}
function fmtVal(f) {
  if (f.value === null || f.value === undefined || isNaN(f.value)) return '-'
  if (f.label.includes('市值')) return f.value.toFixed(1) + '亿'
  if (f.label.includes('等级')) return f.value + '级'
  return (f.value > 0 ? '+' : '') + f.value.toFixed(2) + '%'
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
/* 评分构成(五因子) */
.medal-conf { font-size: 11px; color: var(--text-muted); margin-top: -2px; }
.medal-factors {
  display: flex; flex-direction: column; gap: 2px;
  margin-top: 7px; padding-top: 6px;
  border-top: 1px dashed var(--border-soft);
  font-size: 11px;
}
.factor-row { display: flex; align-items: center; gap: 5px; }
.f-label { flex: 1; color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.f-val { color: var(--text-secondary); font-family: monospace; white-space: nowrap; }
.f-score { color: #e0a800; font-weight: 600; width: 32px; text-align: right; }
.f-w { color: var(--text-muted); width: 34px; text-align: right; }
body[data-bg="light"] .f-score { color: #a06a00; }
body[data-bg="light"] .f-label { color: #5a6b85; }
body[data-bg="light"] .medal-conf { color: #5a6b85; }
</style>
