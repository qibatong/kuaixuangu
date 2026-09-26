<template>
  <!--
    AI预测·火眼 页面壳 —— 2026-09-25 新增
    ===========================================================================
    与 views/AipickView.vue（金睛）共用 components/AipickReport.vue，
    唯一差别是 model="lgb"：
      · 接口带 ?model=lgb → 后端读 /opt/kuaixuan/aipick/output/lgb
      · 标题显示「火眼」徽标 + 训练时间/AUC

    ★ 2026-09-25 主人指令：页面**不出现技术模型名** —— 金睛=XGBoost / 火眼=LightGBM。
      `model="lgb"` 是接口参数，**不改**（后端按该值选产物目录）。

    入口：首页第三个 tab（StockView）、历史回看第三个 tab（HistoryView）、路由 /aipick-lgb。
    配额与金睛共用（quota_guard("aipick")）⇒ 免费用户一天仍是 1 次。
  -->
  <AipickReport :embedded="embedded" :show-date-picker="showDatePicker" model="lgb" />
</template>

<script setup>
import AipickReport from '../components/AipickReport.vue'

defineProps({
  embedded: { type: Boolean, default: false },
  showDatePicker: { type: Boolean, default: true },
})
</script>
