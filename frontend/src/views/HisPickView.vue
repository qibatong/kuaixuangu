<template>
  <!-- 独立路由入口（便于直接打开该页核对；竞价选股 tab 也用同一组件） -->
  <div class="his-pick-page">
    <HisPickPanel />
  </div>
</template>

<script setup>
import HisPickPanel from '../components/HisPickPanel.vue'
</script>

<style scoped>
.his-pick-page { padding: var(--s1) var(--s2) var(--s4); }
</style>
