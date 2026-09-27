<template>
  <!--
    二级页 pill 行（2026-09-27 v4.11.58 工单 三.2）
    —— 挂在 NavBar 下方，随当前路由所在的一级分组，横向列出该组二级页。
    —— 手机端同样显示：底部 tab 只切「一级分组」，组内二级页由这一行 pill 切换，
       二级切换**不改变底部 tab 的高亮**（底部高亮只认分组）。
    —— 只有 1 个二级页的组（盘前资讯 / 盘中 / 自选）不渲染，避免出现孤零零一个 pill。
    —— 组定义上标了 `hidePills: true` 的组也不渲染 —— 目前只有「竞价」组：
       它那 4 个二级页在 / 页内部已各有一套 tab（选股|竞价异动 / AI选股|金睛|火眼），
       pill 行是纯重复（2026-09-27 v4.11.61 主人实测反馈后去掉）。
  -->
  <nav
    v-if="group && items.length > 1 && !group.hidePills"
    class="group-nav"
    :aria-label="group.label + ' · 组内导航'"
  >
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

/* 🔴 2026-09-27 v4.11.60 严重缺陷修复（导航整块不可用）:
   本文件曾有一行 `void NAV_GROUPS`，注释写「避免打包器误判该文件未被使用」，
   但该文件只 `import { groupKeyOfRoute, groupByKey }` —— **NAV_GROUPS 从未被导入**。
   `<script setup>` 的顶层语句会被编译进 setup()，于是 setup 一执行就抛
   `ReferenceError: NAV_GROUPS is not defined`：
     · 构建完全成功（Rollup 对"未定义的自由变量"不报错，只当它是全局变量）；
     · 生产构建连 warning 都没有；
     · 结果 = **二级导航 pill 行永不渲染** ⇒ 桌面端只剩 5 个一级入口，
       手机端点"复盘"只落到 entry /ladder，组内 5 个页面（含 /news 盘前资讯）全部不可达。
   ⇒ ① 直接删掉那行（"模块被 tree-shake 掉"的担忧是多余的：本文件已从该模块导入了
        两个函数，模块不可能被摇掉）；
      ② 纪律：`<script setup>` 里出现的任何标识符必须先 import —— 见
        `frontend/_verify/nav_ssr.js` 冒烟测试（会在部署前真渲染一遍导航）。 */
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
    /* 🔴 2026-09-27 v4.11.65 修复（主人反馈「异动要放到复盘板块中」的真正成因）:
       本块原为 `flex-wrap: nowrap; overflow-x: auto; scrollbar-width: none`
       （注释写「二级 pill 多于一屏时横滑，不换行占纵向空间」）—— 实测这个折中是错的：
         复盘组有 6 个二级页，390px 手机上 pill 行可见宽 380 / 内容宽 479
         ⇒ 「异动监管」pill 实测落在屏幕坐标 341~413，**被屏幕右边缘裁掉**，
           主人（微信端）扫一眼只看到「异动监…」，因此反馈「异动没有放进复盘」。
       （Chromium 实测：桌面 1280px 下同一 pill 完整可见 ⇒ 只在手机上出问题。）
       ⇒ 改为换行：6 个二级页一次全部露出。代价是约一行 pill 的纵向高度，
         但「入口被裁掉 ⇒ 用户找不到页面」比多占 30px 严重得多。
       影响面：只有复盘组会换行（其余组二级页 ≤2 个，一行放得下）。 */
    flex-wrap: wrap;
    overflow-x: visible;
  }
  .group-nav-label { font-size: 0.6875rem; padding-right: 6px; }
  .group-nav-item { flex-shrink: 0; padding: 4px 11px; font-size: 0.75rem; }
}
</style>
