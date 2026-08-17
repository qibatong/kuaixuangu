<template>
  <!-- 全站顶部导航栏: 左品牌 logo+导航入口, 右主题/字号/账户工具 -->
  <nav class="nav-bar">
    <div class="nav-left">
      <router-link to="/" class="nav-brand" title="快选 · AI选股">
        <img src="/logo.jpg" class="nav-logo" alt="快选">
        <div class="nav-brand-text">
          <span class="nav-brand-name">快选</span>
          <span class="nav-brand-slogan">一键筛选 · 高效复盘</span>
        </div>
      </router-link>
      <div class="nav-tabs">
      <router-link to="/" exact-active-class="router-link-active" class="nav-item">
        <i class="fa fa-home"></i> 选股
      </router-link>
      <router-link to="/auction" exact-active-class="router-link-active" class="nav-item">
        <i class="fa fa-bullhorn"></i> 竞价异动
      </router-link>
      <router-link to="/market" exact-active-class="router-link-active" class="nav-item">
        <i class="fa fa-radar"></i> 市场雷达
      </router-link>
      <router-link to="/ladder" exact-active-class="router-link-active" class="nav-item">
        <i class="fa fa-sitemap"></i> 连板天梯
      </router-link>
      <router-link to="/history" exact-active-class="router-link-active" class="nav-item">
        <i class="fa fa-history"></i> 历史回看
      </router-link>
      <router-link to="/invite" exact-active-class="router-link-active" class="nav-item">
        <i class="fa fa-share-alt"></i> 邀请
      </router-link>
      <router-link v-if="user.isAdmin" to="/admin" exact-active-class="router-link-active" class="nav-item">
        <i class="fa fa-shield"></i> 管理
      </router-link>
      </div>
    </div>

    <div class="nav-tools">
      <!-- 背景明暗切换器 -->
      <div class="theme-picker" title="切换背景(登录后自动保存)">
        <span class="theme-label"><i class="fa fa-adjust"></i></span>
        <button
v-for="b in BGS" :key="b.key"
                class="theme-dot bg-dot" :class="{ active: bg === b.key }"
                :style="{ background: b.color }" :title="b.label"
                @click="setBg(b.key)"
></button>
      </div>
      <!-- 字号切换器 -->
      <div class="font-picker" title="字体大小(登录后自动保存)">
        <span class="theme-label"><i class="fa fa-font"></i></span>
        <button
v-for="f in FONTS" :key="f.key"
                class="font-btn" :class="{ active: font === f.key }"
                :style="{ fontSize: f.key === 'sm' ? '11px' : f.key === 'lg' ? '16px' : '13px' }"
                :title="f.label" @click="setFont(f.key)"
>
A
</button>
      </div>
      <!-- 账户工具: 已登录显示用户名(点击弹下拉: 资料/改密/退出)/会员标识 -->
      <div v-if="user.isLoggedIn" class="user-tools">
        <div class="user-dropdown" ref="userDropdown">
          <button class="user-name-btn" title="账户操作" @click="toggleMenu">
            <i class="fa fa-user-circle"></i> {{ user.username }}
            <i class="fa fa-caret-down" :class="{ 'caret-up': menuOpen }"></i>
          </button>
          <!-- 下拉菜单: 资料/改密/退出 -->
          <div v-show="menuOpen" class="user-menu">
            <button class="menu-item" @click="menuOpen = false; profileModal.open()"><i class="fa fa-id-card"></i> 个人资料</button>
            <button class="menu-item" @click="menuOpen = false; changePwdModal.open()"><i class="fa fa-key"></i> 修改密码</button>
            <button class="menu-item menu-logout" @click="menuOpen = false; logout()"><i class="fa fa-sign-out"></i> 退出登录</button>
          </div>
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
      </div>
    </div>

    <!-- 改密弹层(全站唯一, 改密按钮来自 NavBar) -->
    <ChangePwdModal ref="changePwdModal" />
    <!-- 个人资料弹层(手机号/邮箱/微信名) -->
    <ProfileModal ref="profileModal" />
  </nav>
</template>

<script setup>
// 全站导航栏: 左页面入口 tabs, 右主题/字号/账户工具(主题+字号从 hero 迁来)
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { useTheme, BGS, FONTS } from '../composables/useTheme'
import { useUserStore } from '../stores/user'
import { showToast } from '../utils/toast'
import ChangePwdModal from './ChangePwdModal.vue'
import ProfileModal from './ProfileModal.vue'

const router = useRouter()
const user = useUserStore()
const { bg, font, setBg, setFont } = useTheme()

const changePwdModal = ref(null)
const profileModal = ref(null)
// 用户名下拉菜单
const menuOpen = ref(false)
const userDropdown = ref(null)

function toggleMenu() {
  menuOpen.value = !menuOpen.value
}

function onDocClick(e) {
  if (menuOpen.value && userDropdown.value && !userDropdown.value.contains(e.target)) {
    menuOpen.value = false
  }
}

onMounted(() => document.addEventListener('click', onDocClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))

function logout() {
  if (!confirm('确定退出当前账号？')) return
  user.clearSession()
  showToast('已退出登录', 'success')
  router.replace('/login')
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
  font-size: 18px; font-weight: 800;
  color: var(--accent); letter-spacing: 10px;
  line-height: 1.1;
  padding-right: 4px; /* 抵消 letter-spacing 右侧留白, 让整体居中更准 */
}
.nav-brand-text { display: flex; flex-direction: column; align-items: center; gap: 3px; line-height: 1.1; }
.nav-brand-slogan {
  font-size: 11px;
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
  font-size: 13px;
  color: var(--text-secondary);
  text-decoration: none;
  border: 1px solid transparent;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.nav-item:hover { background: var(--bg-hover); color: var(--text-main); }
.nav-item.router-link-active {
  background: rgba(255, 180, 0, 0.15);
  border-color: var(--accent);
  color: var(--accent);
  font-weight: 600;
}

/* 主题/字号 picker(从 hero 迁来) */
.theme-picker {
  display: inline-flex; align-items: center; gap: 6px;
  background: var(--bg-input); border: 1px solid var(--border-soft);
  border-radius: 20px; padding: 4px 10px;
}
.font-picker {
  display: inline-flex; align-items: center; gap: 4px;
  background: var(--bg-input); border: 1px solid var(--border-soft);
  border-radius: 20px; padding: 4px 10px;
}
.theme-label { color: var(--text-muted); font-size: 12px; }
.theme-dot {
  width: 16px; height: 16px; border-radius: 50%;
  border: 2px solid var(--border-soft); cursor: pointer; padding: 0;
  transition: transform 0.15s, border-color 0.15s;
}
.theme-dot:hover { transform: scale(1.2); }
.theme-dot.active { border-color: var(--text-main); box-shadow: 0 0 6px var(--border-soft); }
.font-btn {
  min-width: 22px; height: 22px; line-height: 1;
  border: 1px solid var(--border-soft); border-radius: 12px;
  background: var(--bg-input); color: var(--text-secondary);
  font-size: 13px; font-weight: 600; cursor: pointer; padding: 0 5px;
  transition: transform 0.15s, border-color 0.15s, background 0.15s, color 0.15s;
}
.font-btn:hover { transform: scale(1.1); border-color: var(--accent); }
.font-btn.active { border-color: var(--accent); background: var(--accent); color: #fff; }

/* 账户工具 */
.user-tools { display: flex; align-items: center; gap: 6px; }
.user-dropdown { position: relative; }
.user-name-btn {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 13px; color: var(--text-secondary);
  background: var(--bg-input); border: 1px solid var(--border-soft);
  border-radius: 6px; padding: 4px 10px; cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.user-name-btn:hover { background: var(--bg-hover); color: var(--text-main); border-color: var(--accent); }
.caret-up { transform: rotate(180deg); }
/* 下拉菜单: 不透明背景(按主题覆盖, 避免与 --bg-card 半透明融背景) */
.user-menu {
  position: absolute; top: calc(100% + 6px); right: 0; z-index: 1000;
  min-width: 150px;
  background-color: #1f2230;          /* 默认深色主题: 深灰实色 */
  color: #eef2ff;
  border: 1.5px solid #3a3e50;
  border-radius: 8px;
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.45);
  padding: 5px;
  display: flex; flex-direction: column; gap: 2px;
}
.user-menu .menu-item {
  display: flex; align-items: center; gap: 8px;
  width: 100%; text-align: left;
  background: transparent; border: none;
  color: #eef2ff;
  font-size: 13px; padding: 8px 12px;
  border-radius: 6px; cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.user-menu .menu-item:hover { background: rgba(255, 255, 255, 0.08); color: #ffffff; }
.user-menu .menu-item i { width: 15px; text-align: center; color: #b0b6c8; }
.user-menu .menu-logout { color: #ff6a6a; }
.user-menu .menu-logout:hover { background: rgba(255, 106, 106, 0.15); color: #ff8585; }
.user-menu .menu-logout i { color: #ff6a6a; }
/* 浅色主题: 纯白菜单 + 深灰文字 */
body[data-bg="light"] .user-menu {
  background-color: #ffffff;
  color: #1a1d26;
  border-color: #c8ccd6;
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.18);
}
body[data-bg="light"] .user-menu .menu-item { color: #1a1d26; }
body[data-bg="light"] .user-menu .menu-item:hover { background: rgba(0, 0, 0, 0.06); color: #000000; }
body[data-bg="light"] .user-menu .menu-item i { color: #555; }
/* 纯黑主题: 更深 */
body[data-bg="black"] .user-menu {
  background-color: #0a0a0e;
  border-color: #2a2a30;
}
.mini-btn {
  background: var(--bg-input); border: 1px solid var(--border-soft);
  color: var(--text-secondary); border-radius: 6px;
  font-size: 12px; padding: 4px 10px; cursor: pointer;
  text-decoration: none; transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.mini-btn:hover { background: var(--bg-hover); color: var(--text-main); border-color: var(--accent); }
.logout-btn:hover { color: #ff6a6a; border-color: #ff6a6a; }
.login-btn { color: var(--accent); border-color: var(--accent); }

/* 会员等级标识 */
.member-badge {
  display: inline-flex; align-items: center;
  font-size: 11px; font-weight: 600;
  border-radius: 10px; padding: 1px 8px;
  white-space: nowrap;
}
.vip-badge { background: #ffd70022; color: #d4a017; border: 1px solid #ffd70088; }
.paid-badge { background: rgba(255, 90, 90, 0.15); color: #ff6a6a; border: 1px solid rgba(255, 90, 90, 0.5); }
.admin-badge { background: rgba(90, 160, 255, 0.15); color: #5aa0ff; border: 1px solid rgba(90, 160, 255, 0.5); }
.trial-badge { background: rgba(180, 108, 255, 0.12); color: #b56cff; border: 1px solid rgba(180, 108, 255, 0.4); }
/* 到期前 2 天续费提醒: 橙色高亮(紧急) */
.renew-badge { background: rgba(255, 160, 40, 0.15); color: #ffa028; border: 1px solid rgba(255, 160, 40, 0.55); animation: renew-pulse 1.8s infinite; }
@keyframes renew-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.6; } }
body[data-bg="light"] .renew-badge { color: #b05e00; border-color: #c07a10; }

/* 浅色主题高亮 */
body[data-bg="light"] .nav-item.router-link-active {
  background: rgba(255, 180, 0, 0.2); border-color: #c79100; color: #8a5500;
}

/* ===================== 移动端适配 (<=768px) ===================== */
@media (max-width: 768px) {
  .nav-bar { padding: 6px 4px; gap: 6px; margin-bottom: 10px; }
  .nav-left { gap: 6px; width: 100%; }
  .nav-brand { gap: 5px; padding: 0 6px 0 2px; }
  .nav-logo { width: 26px; height: 26px; border-radius: 6px; }
  .nav-brand-name { font-size: 14px; }
  .nav-brand-slogan { display: none; }
  /* 导航项横向滑动(7 个入口一排滑, 不换行占纵向空间) */
  .nav-tabs { flex-wrap: nowrap; overflow-x: auto; -webkit-overflow-scrolling: touch; scrollbar-width: none; padding-bottom: 2px; width: 100%; }
  .nav-tabs::-webkit-scrollbar { display: none; }
  .nav-item { flex-shrink: 0; padding: 5px 10px; font-size: 12px; gap: 4px; }
  /* 工具区紧凑 */
  .nav-tools { gap: 6px; flex-wrap: nowrap; overflow-x: auto; -webkit-overflow-scrolling: touch; scrollbar-width: none; max-width: 100%; }
  .nav-tools::-webkit-scrollbar { display: none; }
  .theme-picker, .font-picker { padding: 3px 8px; gap: 4px; }
  .theme-label { display: none; }
  .theme-dot { width: 14px; height: 14px; }
  .font-btn { min-width: 20px; height: 20px; }
  /* 手机上用户名按钮紧凑保留(点击弹下拉), 会员徽标省略文本 */
  .user-name-btn { padding: 3px 8px; font-size: 12px; max-width: 110px; overflow: hidden; text-overflow: ellipsis; }
  .user-menu { min-width: 140px; top: calc(100% + 4px); }
  .user-menu .menu-item { padding: 9px 12px; font-size: 13px; }
  .mini-btn { padding: 4px 8px; font-size: 11px; white-space: nowrap; }
  .member-badge { font-size: 10px; padding: 1px 6px; }
}
</style>