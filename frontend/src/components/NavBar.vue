<template>
  <!-- 全站顶部导航栏: 左品牌 logo+导航入口, 右主题/字号/账户工具 -->
  <nav class="nav-bar" aria-label="主导航">
    <div class="nav-left">
      <!-- 2026-09-28: 品牌副标题随 tab 改名同步（AI选股 → 竞价选股），避免站点 tooltip 指向不存在的一级概念 -->
      <router-link to="/" class="nav-brand" title="快选 · 竞价选股">
        <img src="/logo.jpg" class="nav-logo" alt="快选">
        <div class="nav-brand-text">
          <span class="nav-brand-name">快选</span>
          <span class="nav-brand-slogan">一键筛选 · 高效复盘</span>
        </div>
      </router-link>
      <!-- 2026-09-27 v4.11.58 信息架构改造(工单 三.2): 9 个平铺 tab → 一级分组
           （v4.11.61 起为 6 个：竞价 / 盘前资讯 / 盘中 / 复盘 / 自选 / 我的）。
           组内二级页由 NavBar 下方的 GroupNav pill 行切换（标了 hidePills 的组没有);
           手机端(≤768px)本块整体隐藏，改用底部 AppTabBar。
           ★ 组定义唯一来源 = composables/useNavGroups.js，勿在此另抄一份。
           ★ 手动 active：/ 作为「竞价」入口时，router-link 自动 active 会前缀匹配全站恒亮。 -->
      <div class="nav-tabs">
        <router-link
          v-for="g in NAV_GROUPS"
          :key="g.key"
          :to="g.entry"
          class="nav-item"
          :class="{ active: activeGroup === g.key }"
          active-class=""
          exact-active-class=""
          :title="g.items.map((i) => i.label).join(' · ')"
        >
          <i class="fa" :class="g.icon"></i> {{ g.label }}
        </router-link>
      </div>
    </div>

    <div class="nav-tools">
      <!-- 2026-09-27 v4.11.63《快选移动端追加清单》§三: 全局股票快速搜索(桌面入口)。
           手机端(≤768px)本组件自我隐藏，改由底部 AppTabBar 第 7 格承担 ——
           因为 .nav-bar 不 sticky，滚一屏就够不着这里了。 -->
      <StockSearch variant="nav" />
      <!-- 主题快捷切换(2026-08-18 主人要求: 主题设置移出下拉菜单, 导航栏直接可见) -->
      <div class="theme-quick" title="切换主题">
        <button
  v-for="b in BGS" :key="b.key"
          class="nav-theme-dot" :class="{ active: bg === b.key }"
          :style="{ background: b.color }" :title="b.label"
          :aria-label="'切换主题：' + b.label"
          @click="setBg(b.key)"
></button>
      </div>
      <!-- 🔴 2026-09-27 v4.11.65：已登录的「用户名 + 下拉菜单（我的会员 / 个人信息 / 修改密码 /
           退出登录 / 字号 / 字体族）」**整块从顶部移除**，收进「我的」页
           （views/MemberView.vue 的「账户」卡片）。主人原话：
             「3、首页的用户收进 我的 里面。」+ 澄清「是，从顶部移除、整块收进『我的』」。
           顶部只保留：品牌 / 一级分组 / 全局搜索 / 主题圆点（+ 未登录时的登录·注册）。
           ★ 未登录时**必须**留登录入口 —— /member 有登录守卫，若顶部也不给入口，
             未登录用户将无处可登录。 -->
      <div v-if="!user.isLoggedIn" class="user-tools">
        <router-link to="/login" class="mini-btn login-btn">登录</router-link>
        <!-- 2026-09-21 注册放开: 未登录时暴露注册入口 -->
        <router-link v-if="regOpen" :to="{ path: '/login', query: { mode: 'register' } }" class="mini-btn reg-btn">注册</router-link>
      </div>
    </div>
  </nav>
</template>

<script setup>
// 全站导航栏: 左品牌 + 一级分组入口, 右全局搜索 / 主题圆点
//   ★ 2026-09-27 v4.11.65: 账户区块（用户名下拉 + 我的会员/个人信息/修改密码/退出登录/字号/字体族）
//     已整体迁出到「我的」页 —— 见 views/MemberView.vue 的「账户」卡片。
//     本组件不再 import ChangePwdModal / ProfileModal，也不再持有 useTheme 的 font/fontFam。
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useTheme, BGS } from '../composables/useTheme'
import { useUserStore } from '../stores/user'
import { NAV_GROUPS, groupKeyOfRoute } from '../composables/useNavGroups'
import StockSearch from './StockSearch.vue'

const route = useRoute()
const user = useUserStore()
// 当前路由落在哪个一级分组（竞价 / 盘前资讯 / 盘中 / 复盘 / 自选 / 我的）——顶部一级 tab 高亮依据
const activeGroup = computed(() => groupKeyOfRoute(route))
const { bg, setBg } = useTheme()

// 注册入口开关(2026-09-21 放开注册, 由后端 /api/register/config 决定)
const regOpen = ref(true)

onMounted(() => {
  // 注册开关: 未登录时才查, 决定是否显示「注册」入口
  if (!user.isLoggedIn) {
    import('../api/auth').then(({ registerConfig }) => {
      registerConfig().then((d) => { regOpen.value = d.open !== false }).catch(() => {})
    }).catch(() => {})
  }
})
</script>

<style scoped>
.nav-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  background: var(--bg-card);
  border: 1px solid var(--border-soft);
  border-radius: 10px;
  /* 与 .page-shell 内容对齐: container 4 + 自有 4 = 8px 缩进 */
  padding: 10px 8px;
  margin: 0 0 18px;
}
/* 左侧: 品牌 logo + 导航入口 */
.nav-left { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; min-width: 0; }
.nav-brand {
  display: inline-flex; align-items: center; gap: 8px;
  text-decoration: none; padding: 2px 8px 2px 4px;
  border-right: 1px solid var(--border-soft);
}
.nav-logo {
  width: 34px; height: 34px;
  border-radius: 8px; object-fit: cover;
  display: block;
}
.nav-brand-name {
  font-size: 1.125rem; font-weight: 800;
  color: var(--accent); letter-spacing: 10px;
  line-height: 1.1;
  padding-left: 5px; /* 补偿 letter-spacing 末尾 10px 空白, 让"快选"视觉中点 = 几何中点 */
  margin-left: 4px; /* 2026-08-17 主人反馈"往右边移一点点": 整体右移 4px */
}
.nav-brand-text { display: flex; flex-direction: column; align-items: center; gap: 3px; line-height: 1.1; }
.nav-brand-slogan {
  font-size: 0.75rem;
  color: var(--text-muted);
  opacity: 0.85;
  white-space: nowrap;
  letter-spacing: 0.5px;
  line-height: 1.1;
}
.nav-tabs { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.nav-tools { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.nav-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border-radius: 8px;
  font-size: 0.8125rem;
  color: var(--text-secondary);
  text-decoration: none;
  border: 1px solid transparent;
  /* 2026-08-17 主人反馈: 非选中态完全透明像普通文字, 看上去不可点击.
     加淡背景让所有 tab 看上去都是「按钮胶囊」, active 态用橙高亮区分 */
  background: rgba(255, 255, 255, 0.04);
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.nav-item:hover { background: var(--bg-hover); color: var(--text-main); }
/* 白色主题: 克制导航栏(2026-09-21 主人要求去红渐变, 改白底细边;
   品牌红仅保留在激活 tab 文字/边框作点缀, 不再大面积铺红) */
body[data-bg="light"] .nav-bar {
  background: #ffffff;
  border-color: #d9dde5;
  box-shadow: 0 1px 4px rgba(30, 40, 60, 0.06);
}
body[data-bg="light"] .nav-brand-name { color: #1a1d26; }
body[data-bg="light"] .nav-brand-slogan { color: #8a8f9c; }
body[data-bg="light"] .nav-brand { border-right-color: #e3e6ec; }
body[data-bg="light"] .nav-item {
  background: #f2f4f8;
  color: #3a3f4c;
  border-color: #e0e3ea;
}
body[data-bg="light"] .nav-item:hover { background: #e8ebf1; color: #1a1d26; }
body[data-bg="light"] .nav-item.active {
  background: #fff;
  color: #c62828;
  border-color: rgba(198, 40, 40, 0.45);
  font-weight: 700;
}
body[data-bg="light"] .mini-btn {
  background: #f2f4f8;
  border-color: #e0e3ea;
  color: #3a3f4c;
}
body[data-bg="light"] .mini-btn:hover { background: #e8ebf1; color: #1a1d26; }
.nav-item.active {
  background: rgba(255, 180, 0, 0.15);
  border-color: var(--accent);
  color: var(--accent);
  font-weight: 600;
}

/* 导航栏主题快捷圆点(2026-08-18 主人要求移出下拉；v4.11.65 账户区块迁走后仍留在顶部) */
.theme-quick {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 2px 4px; border-radius: 14px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.12);
}
body[data-bg="light"] .theme-quick { border-color: #e0e3ea; background: #f2f4f8; }
.nav-theme-dot {
  width: 18px; height: 18px; border-radius: 50%;
  border: 2px solid rgba(255, 255, 255, 0.35); cursor: pointer; padding: 0;
  transition: transform 0.15s, border-color 0.15s;
}
.nav-theme-dot:hover { transform: scale(1.18); }
.nav-theme-dot.active { border-color: #fff; box-shadow: 0 0 6px rgba(255, 255, 255, 0.85); }
body[data-bg="light"] .nav-theme-dot { border-color: rgba(0, 0, 0, 0.3); }
body[data-bg="light"] .nav-theme-dot.active { border-color: #1a1d26; box-shadow: 0 0 6px rgba(26, 29, 38, 0.3); }

/* 账户工具（仅未登录时渲染：登录 / 注册） */
.user-tools { display: flex; align-items: center; gap: 6px; }
/* 🔴 2026-09-27 v4.11.65：此处原有约 130 行「用户名按钮 + Teleport 到 body 的下拉菜单
   （我的会员 / 个人信息 / 修改密码 / 退出登录 / 字号 / 字体族）」样式
   （.user-dropdown / .user-name-btn / .caret-up / .user-menu / .menu-* / .menu-dot），
   随该 UI 一起**整块删除**，对应样式已重写为「我的」页的 scoped 样式（views/MemberView.vue）。
   纪律：UI 迁走时同步删样式，不留引用不到的死 CSS（否则下次改样式会改到"看不见的副本"）。 */
.mini-btn {
  background: var(--bg-input); border: 1px solid var(--border-soft);
  color: var(--text-secondary); border-radius: 6px;
  font-size: 0.75rem; padding: 4px 10px; cursor: pointer;
  text-decoration: none; transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.mini-btn:hover { background: var(--bg-hover); color: var(--text-main); border-color: var(--accent); }
.login-btn { color: var(--accent); border-color: var(--accent); }
.reg-btn { color: #fff; background: var(--accent); border-color: var(--accent); }
.reg-btn:hover { background: var(--accent); color: #fff; filter: brightness(1.1); }

/* 会员等级徽标（.member-badge / .vip-badge / .paid-badge / .admin-badge / .trial-badge /
   .renew-badge）2026-09-27 v4.11.65 随账户区块一并迁到 views/MemberView.vue 的 scoped 样式，
   本文件已无引用，故整块删除。 */

/* 浅色主题高亮(红色导航栏已在上方统一处理 router-link-active 白底红字) */

/* ===================== 移动端适配 (<=768px) ===================== */
@media (max-width: 768px) {
  .nav-bar { padding: 6px 4px; gap: 6px; margin-bottom: 10px; }
  .nav-left { gap: 6px; width: 100%; }
  .nav-brand { gap: 5px; padding: 0 6px 0 2px; }
  .nav-logo { width: 26px; height: 26px; border-radius: 6px; }
  .nav-brand-name { font-size: 0.875rem; }
  .nav-brand-slogan { display: none; }
  /* 2026-09-27 v4.11.58 信息架构改造: 手机端**隐藏**顶部一级分组导航,
     改由底部固定 AppTabBar(6 tab) 承担一级分组切换, 组内二级页用 GroupNav pill 换行/横滑。
     原先「9 个 tab 自动换行」的老行为不再需要。 */
  .nav-tabs { display: none; }
  /* 工具区自动换行(2026-08-18 主人要求: 不横滑, 放不下自动换行) */
  .nav-tools { gap: 6px; flex-wrap: wrap; overflow: visible; max-width: 100%; }
  .nav-tools::-webkit-scrollbar { display: none; }
  .mini-btn { padding: 4px 8px; font-size: 0.75rem; white-space: nowrap; }
}
</style>