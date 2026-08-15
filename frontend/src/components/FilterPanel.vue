<template>
  <!-- 盘中/竞价共用同一套筛选条件(诗人需求: 盘中=不锁定的竞价,逻辑一致) -->
  <div class="filter-custom" :class="{ 'filter-locked': store.isFilterLocked }">
    <div style="display:flex; flex-wrap:wrap; align-items:center; gap:10px;">
      <label><input v-model="store.filterSettings.stSuspend" type="checkbox" :disabled="store.isFilterLocked"> 剔除ST/停牌</label>
      <span class="filter-divider">|</span>
      <span style="color:var(--accent-text); font-size:12px; font-weight:600;">市场范围：</span>
      <label v-for="m in marketOptions" :key="m.value">
        <input v-model="store.filterSettings.markets" type="checkbox" :value="m.value" :disabled="store.isFilterLocked"> {{ m.label }}
      </label>
      <span class="filter-divider">|</span>
      <label><input v-model="store.filterSettings.limitUp" type="checkbox" :disabled="store.isFilterLocked"> 剔除昨日涨停</label>
    </div>
    <div style="display:flex; flex-wrap:wrap; align-items:center; gap:8px;">
      <label>竞价涨幅 &gt; <input v-model.number="store.filterSettings.bidGt" type="number" min="0" max="20" step="0.5" :disabled="store.isFilterLocked">% 剔除</label>
      <span class="filter-divider">|</span>
      <label>涨停率&lt;<input v-model.number="store.filterSettings.probLt" type="number" min="5" max="95" step="1" :disabled="store.isFilterLocked">% 且可信度&lt;<input v-model.number="store.filterSettings.confLt" type="number" min="50" max="90" step="1" :disabled="store.isFilterLocked">% 剔除</label>
      <span class="filter-divider">|</span>
      <label>流通市值&lt;<input v-model.number="store.filterSettings.floatMvFloor" type="number" min="1" max="5000" step="1" :disabled="store.isFilterLocked">亿 剔除(去小盘)</label>
      <label>流通市值&gt;<input v-model.number="store.filterSettings.floatMvGt" type="number" min="1" max="5000" step="1" :disabled="store.isFilterLocked">亿 剔除</label>
      <label>股价&gt;<input v-model.number="store.filterSettings.priceGt" type="number" min="1" max="5000" step="1" :disabled="store.isFilterLocked">元 剔除</label>
      <span class="filter-divider">|</span>
      <label>竞价金额&lt;<input v-model.number="store.filterSettings.bidAmtFloor" type="number" min="0" max="100000" step="500" :disabled="store.isFilterLocked">万 剔除</label>
    </div>

    <div class="filter-actions">
      <button class="tdx-export-btn" style="background:var(--accent-deep);" :disabled="store.isFilterLocked && store.mode === 'auction'" @click="apply">应用筛选</button>
      <button class="tdx-export-btn reset-filter-btn" :disabled="store.isFilterLocked && store.mode === 'auction'" @click="reset"><i class="fa fa-undo"></i> 重置</button>
      <button v-if="store.mode === 'auction'" class="tdx-export-btn lock-filter-btn" :class="{ locked: store.isFilterLocked }" @click="store.toggleFilterLock()">{{ store.isFilterLocked ? ' 解锁' : ' 锁定' }}</button>
      <span v-if="store.mode === 'auction'" class="lock-indicator" :style="{ display: store.isFilterLocked ? 'inline-block' : 'none' }"> 筛选已锁定</span>
    </div>
  </div>
</template>

<script setup>
import { useStocksStore } from '../stores/stocks'

const store = useStocksStore()
const marketOptions = [
  { value: 'hs', label: '沪深A股主板' },
  { value: 'cyb', label: '创业板' },
  { value: 'kcb', label: '科创板' }
]

async function apply() {
  try {
    await store.applyCustomFilter()
  } catch (e) {
    /* 错误已由 request 层抛给调用方 */
  }
}

function reset() {
  store.resetFilterToDefault()
}
</script>
