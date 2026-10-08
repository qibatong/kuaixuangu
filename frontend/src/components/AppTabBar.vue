<template>
  <!--
    手机端底部固定 tab 栏（2026-09-27 v4.11.58 工单 七；v4.11.61 由 5 tab 扩为 6 tab；
       2026-10-01 主人拍板收为 5 tab：首页/竞价/盘中/复盘/我的，并去掉第 7 格搜索；
       **2026-10-04 主人指令「app端右下角我的去掉」⇒ 4 tab：首页/竞价/盘中/复盘**）
       ⚠️ 配套：账户入口移到**顶部左上角「用户中心」（首字母头像）**，见 NavBar.vue 的
       `.nav-user-btn`（仅 ≤768px 渲染）—— 没有它，/member 在手机上无处可进（有登录守卫）。
    —— 仅在 ≤768px 渲染（CSS 媒体查询控制，桌面端 display:none）。
    —— 一级分组：竞价 / 盘前资讯 / 盘中 / 复盘 / 自选 / 我的
       （主人在 v4.11.61 反馈「盘前资讯是和竞价、盘中、这些放一行」⇒ 盘前资讯升为一级 tab）。
    —— 视觉规格严格照工单：56px 高 + env(safe-area-inset-bottom) 底部安全区，
       bg=var(--bg-card)、顶部 1px var(--border-soft) 分割线，图标 18px 在上、文字 10px 在下，
       选中态 var(--accent) + scale(1.1) + 字重 600，点击反馈 active:scale(0.92)。
    —— ⚠️ 高度常量改动时必须同步改 App.vue 的 .container.has-tabbar padding-bottom，
       否则最后一行会被 tabbar 挡住。

    —— 2026-09-27 v4.11.63《快选股移动端追加清单》§三：右侧追加**第 7 格「搜索」**
       （组件 StockSearch，非路由项、不算一级分组）。
       为什么不能只把入口放在 NavBar：**.nav-bar 不是 sticky**，页面往下滚一屏就够不着了，
       而"看盘中想直接看某只票"恰恰发生在滚到表格中段的时候；这条 tabbar 是 fixed 的。
       ⚠️ 它用 .ss-root--tabbar/.ss-tab 自己的类，**不占用 .tabbar-item**。
       🔴 2026-10-01 主人拍板：**该格已移除**（「上面已经搜索了」），底部收为 4 格
          ⇒ 顶栏搜索改移动端常驻（StockSearch.vue 的 ≤768 隐藏规则已删）；
          StockSearch 的 `variant="tabbar"` 变体保留在组件内（SSR 用例仍在测它），只是不再被使用。
  -->
  <nav class="app-tabbar" aria-label="主导航">
    <router-link
      v-for="t in tabs"
      :key="t.key"
      :to="t.path"
      class="tabbar-item"
      :class="{ active: activeKey === t.key }"
      active-class=""
      exact-active-class=""
      :aria-current="activeKey === t.key ? 'page' : null"
    >
      <i class="fa tabbar-icon" :class="t.icon"></i>
      <span class="tabbar-label">{{ t.label }}</span>
    </router-link>
  </nav>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { TABBAR_TABS, tabbarKeyOfRoute } from '../composables/useNavGroups'

const route = useRoute()
// 2026-10-01 主人拍板：底部 **4 格 = 首页/竞价/盘中/我的**（原 6 组 + 第 7 格搜索）。
//   去掉搜索格的理由（原话）「上面已经搜索了」⇒ 顶栏搜索改移动端常驻
//   （StockSearch.vue 里那条 ≤768 隐藏规则已删）。桌面顶部导航仍读 NAV_GROUPS（6 组不变）。
const tabs = TABBAR_TABS
const activeKey = computed(() => tabbarKeyOfRoute(route))
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
  /* 🔴 2026-10-05 深色模式：原为 var(--bg-panel-solid) = rgba(18,22,35,.98) ——
     与 native 导航栏区域露出的 windowBg(#0f1219) 差一点点 ⇒ 底栏下方一条浅色带。
     ⇒ 改用 --chrome-bg（深色 #0f1219 / 浅色 #ffffff）与系统栏完全同值。
     浅色主题下 --chrome-bg 也是 #ffffff，与原 --bg-panel-solid(#ffffff) 同值 ⇒ 零变化。 */
  background: var(--chrome-bg);
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
  font-size: var(--fs-xl);
  line-height: 1;
  transition: transform 0.15s;
}
.tabbar-label {
  font-size: var(--fs-xs);                /* 4 格更宽松 ⇒ 由 10px 提到 11px 更好点 */
  line-height: 1;
  font-weight: 400;
  white-space: nowrap;
  /* 2026-10-01 起为 4 格（首页/竞价/盘中/我的）⇒ 单格宽 = 屏宽/4
     （375px→93.8px、320px→80px），最长标签 2 字 ⇒ 极宽松；这里仍留 overflow 兜底 */
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  padding: 0 1px;
}
.tabbar-item.active { color: var(--accent); }
.tabbar-item.active .tabbar-icon { transform: scale(1.1); }
.tabbar-item.active .tabbar-label { font-weight: 600; }
.tabbar-item:active { transform: scale(0.92); }

@media (max-width: 768px) {
  .app-tabbar { display: flex; }
}
</style>
