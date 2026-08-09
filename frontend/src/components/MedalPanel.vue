<template>
  <div class="medal-section">
    <template v-if="!stocks.length">
      <div class="empty-state" style="width:100%">暂无数据，请先执行选股</div>
    </template>
    <div v-for="(item, i) in top3" :key="item.code" class="medal-card">
      <div class="medal-rank"><span class="medal-rank-icon"></span> {{ ['金牌', '银牌', '铜牌'][i] }}</div>
      <div class="medal-name-big">{{ item.name }}</div>
      <div class="medal-code" @click="linkToSoftware(item.code)">{{ item.code }}</div>
      <div class="medal-prob-big">{{ item.probability }}分</div>
      <div class="medal-detail">
        <span class="bid-chg">竞涨幅{{ item.bidChange > 0 ? '+' : '' }}{{ item.bidChange.toFixed(2) }}%</span>
        <span class="conf-val">可信{{ item.confidence }}%</span>
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
</script>
