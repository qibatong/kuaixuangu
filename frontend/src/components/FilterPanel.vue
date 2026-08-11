<template>
  <div class="filter-custom" :class="{ 'filter-locked': store.isFilterLocked }">
    <!-- 竞价模式参数 -->
    <template v-if="store.mode === 'auction'">
      <div style="display:flex; flex-wrap:wrap; align-items:center; gap:10px;">
        <label><input type="checkbox" v-model="store.filterSettings.stSuspend" :disabled="store.isFilterLocked"> 剔除ST/停牌</label>
        <span class="filter-divider">|</span>
        <span style="color:#ffbcbc; font-size:12px; font-weight:600;">市场范围：</span>
        <label v-for="m in marketOptions" :key="m.value">
          <input type="checkbox" :value="m.value" v-model="store.filterSettings.markets" :disabled="store.isFilterLocked"> {{ m.label }}
        </label>
        <span class="filter-divider">|</span>
        <label><input type="checkbox" v-model="store.filterSettings.limitUp" :disabled="store.isFilterLocked"> 剔除昨日涨停</label>
      </div>
      <div style="display:flex; flex-wrap:wrap; align-items:center; gap:8px;">
        <label>竞价涨幅 &gt; <input type="number" v-model.number="store.filterSettings.bidGt" min="0" max="20" step="0.5" :disabled="store.isFilterLocked">% 剔除</label>
        <span class="filter-divider">|</span>
        <label>涨停率&lt;<input type="number" v-model.number="store.filterSettings.probLt" min="5" max="95" step="1" :disabled="store.isFilterLocked">% 且可信度&lt;<input type="number" v-model.number="store.filterSettings.confLt" min="50" max="90" step="1" :disabled="store.isFilterLocked">% 剔除</label>
        <span class="filter-divider">|</span>
        <label>流通市值&lt;<input type="number" v-model.number="store.filterSettings.floatMvFloor" min="1" max="5000" step="1" :disabled="store.isFilterLocked">亿 剔除(去小盘)</label>
        <label>流通市值&gt;<input type="number" v-model.number="store.filterSettings.floatMvGt" min="1" max="5000" step="1" :disabled="store.isFilterLocked">亿 剔除</label>
        <label>股价&gt;<input type="number" v-model.number="store.filterSettings.priceGt" min="1" max="5000" step="1" :disabled="store.isFilterLocked">元 剔除</label>
        <span class="filter-divider">|</span>
        <label>竞价金额&lt;<input type="number" v-model.number="store.filterSettings.bidAmtFloor" min="0" max="100000" step="500" :disabled="store.isFilterLocked">万 剔除</label>
      </div>
    </template>

    <!-- 盘中实时模式参数 -->
    <template v-else>
      <div style="display:flex; flex-wrap:wrap; align-items:center; gap:10px;">
        <label><input type="checkbox" v-model="store.spotFilterSettings.stSuspend"> 剔除ST/停牌</label>
        <span class="filter-divider">|</span>
        <span style="color:#ffbcbc; font-size:12px; font-weight:600;">市场范围：</span>
        <label v-for="m in marketOptions" :key="m.value">
          <input type="checkbox" :value="m.value" v-model="store.spotFilterSettings.markets"> {{ m.label }}
        </label>
        <span class="filter-divider">|</span>
        <label><input type="checkbox" v-model="store.spotFilterSettings.limitUp"> 剔除昨日涨停</label>
        <span class="filter-divider">|</span>
        <label><input type="checkbox" v-model="store.spotFilterSettings.spotExcludeZT"> 剔除已涨停封板</label>
      </div>
      <div style="display:flex; flex-wrap:wrap; align-items:center; gap:8px;">
        <label>实时涨幅 <input type="number" v-model.number="store.spotFilterSettings.chgFloor" min="-20" max="30" step="0.5"> ~ <input type="number" v-model.number="store.spotFilterSettings.chgGt" min="-20" max="30" step="0.5">%</label>
        <span class="filter-divider">|</span>
        <label>量比&gt;<input type="number" v-model.number="store.spotFilterSettings.volRatioFloor" min="0" max="20" step="0.1"> 倍</label>
        <span class="filter-divider">|</span>
        <label>换手率 <input type="number" v-model.number="store.spotFilterSettings.turnoverFloor" min="0" max="100" step="0.5"> ~ <input type="number" v-model.number="store.spotFilterSettings.turnoverGt" min="0" max="100" step="0.5">% (0=不限)</label>
        <span class="filter-divider">|</span>
        <label>涨停率&lt;<input type="number" v-model.number="store.spotFilterSettings.probLt" min="5" max="95" step="1">% 且可信度&lt;<input type="number" v-model.number="store.spotFilterSettings.confLt" min="50" max="90" step="1">% 剔除</label>
        <span class="filter-divider">|</span>
        <label>流通市值&lt;<input type="number" v-model.number="store.spotFilterSettings.floatMvFloor" min="1" max="5000" step="1">亿 剔除</label>
        <label>市值&gt;<input type="number" v-model.number="store.spotFilterSettings.floatMvGt" min="1" max="5000" step="1">亿 剔除</label>
        <label>股价&gt;<input type="number" v-model.number="store.spotFilterSettings.priceGt" min="1" max="5000" step="1">元 剔除</label>
      </div>
    </template>

    <div class="filter-actions">
      <button class="tdx-export-btn" style="background:#ff5c5c;" :disabled="store.isFilterLocked && store.mode === 'auction'" @click="apply">应用筛选</button>
      <button class="tdx-export-btn reset-filter-btn" :disabled="store.isFilterLocked && store.mode === 'auction'" @click="reset"><i class="fa fa-undo"></i> 重置</button>
      <button v-if="store.mode === 'auction'" class="tdx-export-btn lock-filter-btn" :class="{ locked: store.isFilterLocked }" @click="store.toggleFilterLock()">{{ store.isFilterLocked ? ' 解锁' : ' 锁定' }}</button>
      <span v-if="store.mode === 'auction'" class="lock-indicator" :style="{ display: store.isFilterLocked ? 'inline-block' : 'none' }"> 筛选已锁定</span>
    </div>
  </div>
</template>

<script setup>
import { defaultSpotFilterSettings, useStocksStore } from '../stores/stocks'

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
  if (store.mode === 'spot') {
    store.spotFilterSettings = { ...defaultSpotFilterSettings }
    return
  }
  store.resetFilterToDefault()
}
</script>
