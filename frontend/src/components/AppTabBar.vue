<template>
  <!--
    手机端底部固定 5 tab 栏（2026-09-27 v4.11.58 工单 七）
    —— 仅在 ≤768px 渲染（CSS 媒体查询控制，桌面端 display:none）。
    —— 5 个一级分组：竞价 / 盘中 / 复盘 / 自选 / 我的。
    —— 视觉规格严格照工单：56px 高 + env(safe-area-inset-bottom) 底部安全区，
       bg=var(--bg-card)、顶部 1px var(--border-soft) 分割线，图标 18px 在上、文字 10px 在下，
       选中态 var(--accent) + scale(1.1) + 字重 600，点击反馈 active:scale(0.92)。
    —— ⚠️ 高度常量改动时必须同步改 App.vue 的 .container.has-tabbar padding-bottom，
       否则最后一行会被 tabbar 挡住。
  -->
  <nav class="app-tabbar" aria-label="主导航">
    <router-link
      v-for="g in groups"
      :key="g.key"
      :to="g.entry"
      class="tabbar-item"
      :class="{ active: activeKey === g.key }"
      active-class=""
      exact-active-class=""
      :aria-current="activeKey === g.key ? 'page' : null"
    >
      <i class="fa tabbar-icon" :class="g.icon"></i>
      <span class="tabbar-label">{{ g.label }}</span>
    </router-link>
  </nav>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { NAV_GROUPS, groupKeyOfRoute } from '../composables/useNavGroups'

const route = useRoute()
const groups = NAV_GROUPS
const activeKey = computed(() => groupKeyOfRoute(route))
</script>

<style scoped>
.app-tabbar {
  display: none;              /* 桌面端隐藏；媒体查询内改为 flex */
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  z-index: 1000;
  height: calc(56px + env(safe-area-inset-bottom, 0px));
  padding-bottom: env(safe-area-inset-bottom, 0px);
  box-sizing: border-box;
  background: var(--bg-panel-solid);
  border-top: 1px solid var(--border-soft);
}
.tabbar-item {
  flex: 1 1 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  height: 56px;
  text-decoration: none;
  color: var(--text-muted);
  -webkit-tap-highlight-color: transparent;
  transition: color 0.15s, transform 0.15s;
}
.tabbar-icon {
  font-size: 18px;
  line-height: 1;
  transition: transform 0.15s;
}
.tabbar-label {
  font-size: 10px;
  line-height: 1;
  font-weight: 500;
  white-space: nowrap;
}
.tabbar-item.active { color: var(--accent); }
.tabbar-item.active .tabbar-icon { transform: scale(1.1); }
.tabbar-item.active .tabbar-label { font-weight: 600; }
.tabbar-item:active { transform: scale(0.92); }

@media (max-width: 768px) {
  .app-tabbar { display: flex; }
}
</style>
