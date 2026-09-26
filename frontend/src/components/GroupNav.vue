<template>
  <!--
    二级页 pill 行（2026-09-27 v4.11.58 工单 三.2）
    —— 挂在 NavBar 下方，随当前路由所在的一级分组，横向列出该组二级页。
    —— 手机端同样显示：底部 tab 只切「一级分组」，组内二级页由这一行 pill 切换，
       二级切换**不改变底部 tab 的高亮**（底部高亮只认分组）。
    —— 只有 1 个二级页的组（盘中 / 自选）不渲染，避免出现孤零零一个 pill。
  -->
  <nav v-if="group && items.length > 1" class="group-nav" :aria-label="group.label + ' · 组内导航'">
    <span class="group-nav-label"><i class="fa" :class="group.icon"></i> {{ group.label }}</span>
    <router-link
      v-for="it in items"
      :key="it.path"
      :to="it.path"
      class="group-nav-item"
      :class="{ active: isActive(it) }"
      active-class=""
      exact-active-class=""
    >{{ it.label }}</router-link>
  </nav>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useUserStore } from '../stores/user'
import { groupKeyOfRoute, groupByKey } from '../composables/useNavGroups'

const route = useRoute()
const user = useUserStore()

const group = computed(() => groupByKey(groupKeyOfRoute(route)))

// 管理后台入口只对管理员露出（非管理员即便看到也会被路由守卫挡回首页）
const items = computed(() => {
  const g = group.value
  if (!g) return []
  return g.items.filter((it) => !it.adminOnly || user.isAdmin)
})

// 手动比对 path，不用 router-link 自动 active：
// 自动模式的 `router-link-active` 对 `/` 是前缀匹配，会让「选股名单」在全站恒亮。
function isActive(it) {
  return route.path === it.path
}

// NAV_GROUPS 由 NavBar / AppTabBar 共用；此处显式引用一次，避免打包器误判该文件未被使用
void NAV_GROUPS
</script>

<style scoped>
.group-nav {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin: -8px 0 14px;      /* 紧贴 NavBar 下沿（NavBar 自身 margin-bottom: 18px） */
  padding: 6px 8px;
  background: var(--bg-card);
  border: 1px solid var(--border-soft);
  border-radius: 10px;
}
.group-nav-label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 0.75rem;
  font-weight: 700;
  color: var(--text-muted);
  padding-right: 8px;
  margin-right: 2px;
  border-right: 1px solid var(--border-soft);
  white-space: nowrap;
}
.group-nav-label .fa { color: var(--accent); }
.group-nav-item {
  display: inline-flex;
  align-items: center;
  padding: 5px 13px;
  border-radius: 999px;              /* pill */
  border: 1px solid transparent;
  background: rgba(255, 255, 255, 0.04);
  color: var(--text-secondary);
  font-size: 0.8125rem;
  text-decoration: none;
  white-space: nowrap;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.group-nav-item:hover { background: var(--bg-hover); color: var(--text-main); }
.group-nav-item.active {
  background: rgba(255, 180, 0, 0.15);
  border-color: var(--accent);
  color: var(--accent);
  font-weight: 600;
}

/* 浅色主题: 与 NavBar 一套克制白底细边 */
body[data-bg="light"] .group-nav {
  background: #ffffff;
  border-color: #d9dde5;
  box-shadow: 0 1px 4px rgba(30, 40, 60, 0.06);
}
body[data-bg="light"] .group-nav-label { color: #8a8f9c; border-right-color: #e3e6ec; }
body[data-bg="light"] .group-nav-item {
  background: #f2f4f8;
  color: #3a3f4c;
  border-color: #e0e3ea;
}
body[data-bg="light"] .group-nav-item:hover { background: #e8ebf1; color: #1a1d26; }
body[data-bg="light"] .group-nav-item.active {
  background: #fff;
  color: #c62828;
  border-color: rgba(198, 40, 40, 0.45);
  font-weight: 700;
}

@media (max-width: 768px) {
  .group-nav {
    gap: 5px;
    padding: 5px 6px;
    margin: -6px 0 10px;
    /* 二级 pill 多于一屏时横滑，不换行占纵向空间 */
    flex-wrap: nowrap;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: none;
  }
  .group-nav::-webkit-scrollbar { display: none; }
  .group-nav-label { font-size: 0.6875rem; padding-right: 6px; }
  .group-nav-item { flex-shrink: 0; padding: 4px 11px; font-size: 0.75rem; }
}
</style>
