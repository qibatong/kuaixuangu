<template>
  <!-- 全站顶部导航栏: 左品牌 logo+导航入口, 右主题/字号/账户工具 -->
  <nav class="nav-bar" aria-label="主导航">
    <div class="nav-left">
      <router-link to="/" class="nav-brand" title="快选 · AI选股">
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
      <!-- 账户工具: 已登录显示用户名(点击弹下拉: 字号/资料/改密/退出)/会员标识 -->
      <div v-if="user.isLoggedIn" class="user-tools">
        <div class="user-dropdown" ref="userDropdown">
          <button class="user-name-btn" title="账户操作" @click="toggleMenu">
            <i class="fa fa-user-circle"></i> {{ user.username }}
            <i class="fa fa-caret-down" :class="{ 'caret-up': menuOpen }"></i>
          </button>
        </div>
        <span v-if="user.memberLevel === 2" class="member-badge vip-badge" title="VIP · 永久权限">VIP</span>
        <span v-else-if="user.memberLevel === 1" class="member-badge paid-badge" title="付费会员">付费会员</span>
        <span v-else-if="user.isAdmin" class="member-badge admin-badge" title="管理员">管理员</span>
        <span v-else-if="user.memberDaysLeft >= 0" class="member-badge trial-badge" :title="'免费试用剩余 ' + user.memberDaysLeft + ' 天, 到期请联系管理员开通'">试用{{ user.memberDaysLeft }}天</span>
        <!-- 到期前 2 天续费提醒(付费会员+试用都提示, 联系管理员续费) -->
        <span v-if="!user.isAdmin && user.memberLevel !== 2 && user.memberDaysLeft >= 0 && user.memberDaysLeft <= 2"
              class="member-badge renew-badge" title="请尽快续费, 联系管理员(微信号 poet-1986)">
          <i class="fa fa-exclamation-circle"></i> 还剩{{ user.memberDaysLeft }}天续费
        </span>
      </div>

      <div v-else class="user-tools">
        <router-link to="/login" class="mini-btn login-btn">登录</router-link>
        <!-- 2026-09-21 注册放开: 未登录时暴露注册入口 -->
        <router-link v-if="regOpen" :to="{ path: '/login', query: { mode: 'register' } }" class="mini-btn reg-btn">注册</router-link>
      </div>
      <!-- 下拉菜单: Teleport 到 body — 2026-08-18 iOS Safari 修复:
           fixed 元素在 .nav-tools(overflow滚动容器)内会被 Safari 当容器内容处理 → 被视图遮挡;
           Teleport 到 body 后脱离所有容器/滚动上下文, 必然在视图顶层 -->
      <Teleport to="body">
        <div v-show="menuOpen" ref="menuRef" class="user-menu" :style="menuPos">
          <!-- 2026-08-22 主题设置区块: 背景色 + 字号 + 字体族, 三档独立 -->
          <div class="menu-settings">
            <!-- 字号 -->
            <div class="menu-setting-row">
              <span class="menu-setting-label"><i class="fa fa-font"></i> 字号</span>
              <button
                v-for="f in FONTS" :key="f.key"
                class="menu-font" :class="{ active: font === f.key }"
                :style="{ fontSize: f.key === 'sm' ? '12px' : f.key === 'lg' ? '16px' : '13px' }"
                :title="f.label" :aria-label="'字号：' + f.label" @click="setFont(f.key)"
              >A</button>
            </div>
            <!-- 字体族: 霞鹜等宽 / 思源黑体 / 思源宋体 — 全部 SIL OFL 1.1 免费商用 -->
            <div class="menu-setting-row" style="margin-top:8px;">
              <span class="menu-setting-label"><i class="fa fa-text-height"></i> 字体</span>
            </div>
            <div class="menu-fontfam-list">
              <button
                v-for="ff in FONT_FAMILIES" :key="ff.key"
                class="menu-fontfam" :class="{ active: fontFam === ff.key }"
                :title="ff.desc" :aria-label="'字体：' + ff.label" @click="setFontFam(ff.key)"
              >
                <span class="ff-label" :style="{ fontFamily: ff.family }">{{ ff.label }}</span>
                <span class="ff-desc">{{ ff.desc }}</span>
              </button>
            </div>
          </div>
          <div class="menu-sep"></div>
          <button class="menu-item" @click="menuOpen = false; router.push('/member')"><i class="fa fa-crown"></i> 我的会员</button>
          <button class="menu-item" @click="menuOpen = false; profileModal.open()"><i class="fa fa-id-card"></i> 个人信息</button>
          <button class="menu-item" @click="menuOpen = false; changePwdModal.open()"><i class="fa fa-key"></i> 修改密码</button>
          <button class="menu-item menu-logout" @click="menuOpen = false; logout()"><i class="fa fa-sign-out"></i> 退出登录</button>
        </div>
      </Teleport>
    </div>    <!-- 改密弹层(全站唯一, 改密按钮来自 NavBar) -->
    <ChangePwdModal ref="changePwdModal" />
    <!-- 个人信息弹层(手机号/邮箱/微信名) -->
    <ProfileModal ref="profileModal" />
  </nav>
</template>

<script setup>
// 全站导航栏: 左页面入口 tabs, 右主题/字号/账户工具(主题+字号从 hero 迁来)
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useTheme, BGS, FONTS, FONT_FAMILIES } from '../composables/useTheme'
import { useUserStore } from '../stores/user'
import { NAV_GROUPS, groupKeyOfRoute } from '../composables/useNavGroups'
import { logoutApi } from '../api/auth'
import { showToast } from '../utils/toast'
import ChangePwdModal from './ChangePwdModal.vue'
import ProfileModal from './ProfileModal.vue'

const router = useRouter()
const route = useRoute()
const user = useUserStore()
// 当前路由落在哪个一级分组（竞价 / 盘中 / 复盘 / 自选 / 我的）——顶部一级 tab 高亮依据
const activeGroup = computed(() => groupKeyOfRoute(route))
const { bg, font, fontFam, setBg, setFont, setFontFam } = useTheme()

const changePwdModal = ref(null)
const profileModal = ref(null)
// 注册入口开关(2026-09-21 放开注册, 由后端 /api/register/config 决定)
const regOpen = ref(true)
// 用户名下拉菜单
const menuOpen = ref(false)
const userDropdown = ref(null)
const menuRef = ref(null)      // Teleport 到 body 后的菜单引用(用于点击外部关闭判断)
// fixed 定位菜单坐标(2026-08-18 iOS Safari 修复: absolute 菜单被 nav-tools 滚动容器裁剪)
const menuPos = ref({})

function toggleMenu() {
  if (!menuOpen.value && userDropdown.value) {
    // 以用户名按钮右下角为锚点, fixed 定位菜单(viewport 坐标系, 不受父级 overflow 影响)
    const r = userDropdown.value.querySelector('.user-name-btn').getBoundingClientRect()
    const mw = Math.max(190, r.width)   // 菜单最小宽
    menuPos.value = {
      top: Math.min(r.bottom + 6, window.innerHeight - 260) + 'px',
      left: Math.max(8, r.right - mw) + 'px',
      minWidth: mw + 'px'
    }
  }
  menuOpen.value = !menuOpen.value
}

// fixed 菜单打开后, 页面滚动时自动关闭(避免位置错位)
function onScrollClose() {
  if (menuOpen.value) menuOpen.value = false
}

function onDocClick(e) {
  // 菜单已 Teleport 到 body, 需同时判断按钮容器和菜单自身
  const inBtn = userDropdown.value && userDropdown.value.contains(e.target)
  const inMenu = menuRef.value && menuRef.value.contains(e.target)
  if (menuOpen.value && !inBtn && !inMenu) {
    menuOpen.value = false
  }
}

onMounted(() => {
  document.addEventListener('click', onDocClick)
  window.addEventListener('scroll', onScrollClose, true)   // 捕获阶段: 任何滚动容器滚动都关闭
  // 注册开关: 未登录时才查, 决定是否显示「注册」入口
  if (!user.isLoggedIn) {
    import('../api/auth').then(({ registerConfig }) => {
      registerConfig().then((d) => { regOpen.value = d.open !== false }).catch(() => {})
    }).catch(() => {})
  }
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  window.removeEventListener('scroll', onScrollClose, true)
})

function logout() {
  if (!confirm('确定退出当前账号？')) return
  // 2026-09-22 v4.11.35: 先通知后端作废 token 并落一条「主动退出」记录,
  // 再清本地会话。后端失败不阻塞退出(本地清理是用户能感知的那一步)。
  logoutApi().finally(() => {
    user.clearSession()
    showToast('已退出登录', 'success')
    router.replace('/login')
  })
}
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
body[data-bg="light"] .user-name-btn {
  background: #f2f4f8;
  border-color: #e0e3ea;
  color: #3a3f4c;
}
body[data-bg="light"] .user-name-btn:hover { background: #e8ebf1; color: #1a1d26; }
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

/* 主题/字号 picker 已迁入账号下拉菜单(2026-08-18 主人反馈: 少用, 收进右上角账号下拉框) */

/* 账户工具 */
.user-tools { display: flex; align-items: center; gap: 6px; }
.user-dropdown { position: relative; }
.user-name-btn {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 0.8125rem; color: var(--text-secondary);
  background: var(--bg-input); border: 1px solid var(--border-soft);
  border-radius: 6px; padding: 4px 10px; cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.user-name-btn:hover { background: var(--bg-hover); color: var(--text-main); border-color: var(--accent); }
.caret-up { transform: rotate(180deg); }
/* 下拉菜单: 不透明背景(按主题覆盖, 避免与 --bg-card 半透明融背景) */
.user-menu {
  position: fixed; z-index: 99999;   /* 2026-08-18: Teleport 到 body + fixed, 视图顶层(原被 nav-tools 滚动容器裁剪/遮挡) */
  background-color: #1f2230;          /* 默认深色主题: 深灰实色 */
  color: #eef2ff;
  border: 1.5px solid #2a2a2a;
  border-radius: 8px;
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.45);
  padding: 5px;
  display: flex; flex-direction: column; gap: 2px;
}
/* 菜单顶部设置区: 字号 (主题已移到导航栏快捷切换, 2026-08-18 主人要求) */
.menu-settings {
  display: flex; flex-direction: column; gap: 7px;
  padding: 6px 10px 9px;
}
.menu-setting-row { display: flex; align-items: center; gap: 8px; }
.menu-setting-label {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 0.75rem; color: #b4b4b4; min-width: 56px; white-space: nowrap;
}
.menu-setting-label i { width: 14px; text-align: center; }
/* 导航栏主题快捷圆点(2026-08-18 主人要求移出下拉) */
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
.menu-dot {
  width: 16px; height: 16px; border-radius: 50%;
  border: 2px solid #2a2a2a; cursor: pointer; padding: 0;
  transition: transform 0.15s, border-color 0.15s;
}
.menu-dot:hover { transform: scale(1.2); }
.menu-dot.active { border-color: #ffffff; box-shadow: 0 0 6px rgba(255, 255, 255, 0.8); }
.menu-font {
  min-width: 22px; height: 22px; line-height: 1;
  border: 1px solid #2a2a2a; border-radius: 12px;
  background: transparent; color: #eef2ff;
  font-weight: 600; cursor: pointer; padding: 0 5px;
  transition: transform 0.15s, border-color 0.15s, background 0.15s, color 0.15s;
}
.menu-font:hover { transform: scale(1.1); border-color: var(--accent); }
.menu-font.active { border-color: var(--accent); background: var(--accent-deep2); color: #fff; } /* 2026-09-21 深色 #ff5c5c 底白字对比 3.03, 改 deep2 深红达 5.4:1 */
/* 字体族切换按钮组: 三个竖排选项, 选中高亮, 文字用对应字体渲染便于对比 */
.menu-fontfam-list {
  display: flex; flex-direction: column; gap: 6px; margin-top: 6px;
}
.menu-fontfam {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  width: 100%; padding: 7px 10px;
  border: 1px solid #2a2a2a; border-radius: 8px;
  background: transparent; color: #eef2ff;
  cursor: pointer; text-align: left;
  transition: transform 0.12s, border-color 0.12s, background 0.12s;
}
.menu-fontfam:hover { transform: translateY(-1px); border-color: var(--accent); }
.menu-fontfam.active {
  border-color: var(--accent);
  background: color-mix(in srgb, var(--accent) 18%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 45%, transparent);
}
.menu-fontfam .ff-label {
  font-size: 0.9375rem; font-weight: 600; line-height: 1.1;
}
.menu-fontfam .ff-desc {
  font-size: 0.75rem; color: #949494; opacity: 0.92;
  margin-left: auto;
}
.menu-sep { height: 1px; background: rgba(255, 255, 255, 0.1); margin: 4px 6px; }
.user-menu .menu-item {
  display: flex; align-items: center; gap: 8px;
  width: 100%; text-align: left;
  background: transparent; border: none;
  color: #eef2ff;
  font-size: 0.8125rem; padding: 8px 12px;
  border-radius: 6px; cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.user-menu .menu-item:hover { background: rgba(255, 255, 255, 0.08); color: #ffffff; }
.user-menu .menu-item i { width: 15px; text-align: center; color: #b4b4b4; }
.user-menu .menu-logout { color: #ff6a6a; }
.user-menu .menu-logout:hover { background: rgba(255, 106, 106, 0.15); color: #ff8585; }
.user-menu .menu-logout i { color: #ff6a6a; }
/* 浅色主题: 纯白菜单 + 深灰文字 */
body[data-bg="light"] .user-menu {
  background-color: #ffffff;
  color: #1a1d26;
  border-color: #c9c9c9;
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.18);
}
body[data-bg="light"] .user-menu .menu-item { color: #1a1d26; }
body[data-bg="light"] .user-menu .menu-item:hover { background: rgba(0, 0, 0, 0.06); color: #000000; }
body[data-bg="light"] .user-menu .menu-item i { color: #555; }
body[data-bg="light"] .menu-setting-label { color: #555; }
body[data-bg="light"] .menu-dot { border-color: #c9c9c9; }
body[data-bg="light"] .menu-dot.active { border-color: #1a1d26; box-shadow: 0 0 6px rgba(0, 0, 0, 0.25); }
body[data-bg="light"] .menu-font { border-color: #c9c9c9; color: #1a1d26; }
body[data-bg="light"] .menu-font.active { border-color: var(--accent); background: var(--accent); color: #fff; }
/* 浅色主题: 字体族按钮适配 */
body[data-bg="light"] .menu-fontfam { border-color: #c9c9c9; color: #1a1d26; background: #f7f8fb; }
body[data-bg="light"] .menu-fontfam.active {
  background: color-mix(in srgb, var(--accent) 14%, #fff);
  border-color: var(--accent);
}
body[data-bg="light"] .menu-fontfam .ff-desc { color: #666; }
body[data-bg="light"] .menu-sep { background: rgba(0, 0, 0, 0.08); }
/* 纯黑主题: 更深 */
body[data-bg="black"] .user-menu {
  background-color: #0a0a0e;
  border-color: #2a2a2a;
}
body[data-bg="black"] .menu-dot { border-color: #2a2a2a; }
body[data-bg="black"] .menu-font { border-color: #2a2a2a; }
body[data-bg="black"] .menu-fontfam { border-color: #2a2a2a; }
.mini-btn {
  background: var(--bg-input); border: 1px solid var(--border-soft);
  color: var(--text-secondary); border-radius: 6px;
  font-size: 0.75rem; padding: 4px 10px; cursor: pointer;
  text-decoration: none; transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.mini-btn:hover { background: var(--bg-hover); color: var(--text-main); border-color: var(--accent); }
.logout-btn:hover { color: #ff6a6a; border-color: #ff6a6a; }
.login-btn { color: var(--accent); border-color: var(--accent); }
.reg-btn { color: #fff; background: var(--accent); border-color: var(--accent); }
.reg-btn:hover { background: var(--accent); color: #fff; filter: brightness(1.1); }

/* 会员等级标识 */
.member-badge {
  display: inline-flex; align-items: center;
  font-size: 0.75rem; font-weight: 600;
  border-radius: 10px; padding: 1px 8px;
  white-space: nowrap;
}
.vip-badge { background: #ffd70022; color: #d4a017; border: 1px solid #ffd70088; }
.paid-badge { background: rgba(255, 90, 90, 0.15); color: #ff6a6a; border: 1px solid rgba(255, 90, 90, 0.5); }
.admin-badge { background: rgba(90, 160, 255, 0.15); color: var(--accent-text); border: 1px solid rgba(90, 160, 255, 0.5); }
.trial-badge { background: rgba(255, 180, 0, 0.12); color: #ffd700; border: 1px solid rgba(255, 180, 0, 0.4); }
/* 到期前 2 天续费提醒: 橙色高亮(紧急) */
.renew-badge { background: rgba(255, 160, 40, 0.15); color: #ffa028; border: 1px solid rgba(255, 160, 40, 0.55); animation: renew-pulse 1.8s infinite; }
@keyframes renew-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.6; } }
body[data-bg="light"] .renew-badge { color: #b05e00; border-color: #c07a10; }

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
     改由底部固定 AppTabBar(6 tab) 承担一级分组切换, 组内二级页用 GroupNav pill 横滑。
     原先「9 个 tab 自动换行」的老行为不再需要。 */
  .nav-tabs { display: none; }
  /* 工具区自动换行(2026-08-18 主人要求: 不横滑, 放不下自动换行) */
  .nav-tools { gap: 6px; flex-wrap: wrap; overflow: visible; max-width: 100%; }
  .nav-tools::-webkit-scrollbar { display: none; }
  /* 手机上用户名按钮紧凑保留(点击弹下拉), 会员徽标省略文本 */
  .user-name-btn { padding: 3px 8px; font-size: 0.75rem; max-width: 110px; overflow: hidden; text-overflow: ellipsis; }
  .user-menu { min-width: 150px; top: calc(100% + 4px); }
  .user-menu .menu-item { padding: 9px 12px; font-size: 0.8125rem; }
  .mini-btn { padding: 4px 8px; font-size: 0.75rem; white-space: nowrap; }
  .member-badge { font-size: 0.75rem; padding: 1px 6px; }
}
</style>