<template>
  <!--
    AI预测（金睛）页面壳 —— 2026-09-25 双模型重构
    ===========================================================================
    原 845 行的实现已抽到 components/AipickReport.vue，本文件只负责"声明用哪个模型"。
    这样两个模型的页面共用同一份表格/排序/规则过滤/日期回看/VipGate 代码，
    避免复制 845 行导致的长期双份维护。

    ★ 2026-09-25 主人指令：页面**不出现技术模型名** —— 金睛=XGBoost / 火眼=LightGBM。

    路由 /aipick、首页左视图 tab、历史回看 tab 三处入口全部指向本文件 ⇒ 行为与改造前完全一致。
  -->
  <AipickReport :embedded="embedded" :show-date-picker="showDatePicker" model="xgb" />
</template>

<script setup>
import AipickReport from '../components/AipickReport.vue'

defineProps({
  embedded: { type: Boolean, default: false },
  showDatePicker: { type: Boolean, default: true },
})
</script>
