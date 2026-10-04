<template>
  <div class="page-shell">
    <h1 class="visually-hidden">龙虎榜</h1>
    <div class="lhb-head">
      <span class="lhb-title"><i class="fa fa-list-alt"></i> 龙虎榜</span>
      <!-- ★ 2026-10-04 主人定标(通达信手机端截图): 本页弃树图, 改为
           「榜单列表(股票/机构/营业部 3 tab + 日期回看) ↔ 个股详情(买卖营业部表)」。
           原层级树图(echarts tree)与第一版桑基图均已下线。 -->
      <span class="lhb-sub">上榜个股 · 机构 / 营业部席位 · 点股票看买卖前五营业部 · 可回看历史交易日</span>
      <span class="lhb-time">{{ bjTime }}</span>
    </div>
    <LhbPanel />
  </div>
</template>

<script setup>
// 2026-09-27 v4.11.58: 龙虎榜从「市场雷达」拆出为独立页，归入「复盘」分组。
// 面板本体在 components/LhbPanel.vue（可复用），本文件只做页面外壳 + 时钟。
import { onMounted, ref } from 'vue'
import LhbPanel from '../components/LhbPanel.vue'
import { usePolling } from '../composables/usePolling'
import { bjTimeStr } from '../utils/time'

const bjTime = ref('--:--:--')

// ⚠️ 2026-09-27 v4.11.59 修: 时钟轮询从 onMounted 回调搬到 setup 顶层 ——
//    Vue 调用 mounted 回调时 currentInstance 为 null, usePolling 内的 onBeforeUnmount
//    会静默注册失败 ⇒ 1s 定时器永不清理(详见 EmConceptPanel.vue 的长注释)。
usePolling(() => { bjTime.value = bjTimeStr() }, 1000, { immediate: false })

onMounted(() => {
  bjTime.value = bjTimeStr()
  // ⚠️ 不做行为埋点: 后端 services/activity.FEATURES 是白名单(8 个键, 无 lhb),
  //    上报非白名单键只会打一条 warning 并 counted=false。工单要求零后端改动,
  //    所以这里**故意不上报**, 而不是硬塞一个别的键(那会把「涨停梯队」的计数带脏)。
})
</script>

<style scoped>
.lhb-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.lhb-title { font-size: 1.25rem; font-weight: 700; color: #ffe0a0; }
.lhb-title .fa { color: #ffb400; }
.lhb-sub { color: var(--text-muted); font-size: 0.8125rem; }
.lhb-time { margin-left: auto; color: var(--text-dim); font-size: 0.875rem; font-variant-numeric: tabular-nums; }

body[data-bg="light"] .lhb-title { color: #8a5500; }
body[data-bg="light"] .lhb-title .fa { color: #c79100; }

@media (max-width: 768px) {
  .lhb-title { font-size: 1.0625rem; }
  .lhb-sub { font-size: 0.75rem; width: 100%; }
  .lhb-time { margin-left: 0; font-size: 0.75rem; }
}
</style>
