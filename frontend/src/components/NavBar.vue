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
      <!-- 账户工具: 已登录显示用户名/会员标识/改密/退出 -->
      <div v-if="user.isLoggedIn" class="user-tools">
        <span class="user-name" :title="user.username"><i class="fa fa-user-circle"></i> {{ user.username }}</span>
        <span v-if="user.memberLevel === 2" class="member-badge vip-badge" title="VIP · 永久权限">VIP</span>
        <span v-else-if="user.memberLevel === 1" class="member-badge paid-badge" title="付费会员">付费会员</span>
        <span v-else-if="user.isAdmin" class="member-badge admin-badge" title="管理员">管理员</span>
        <span v-else-if="user.memberDaysLeft >= 0" class="member-badge trial-badge" :title="'免费试用剩余 ' + user.memberDaysLeft + ' 天, 到期请联系管理员开通'">试用{{ user.memberDaysLeft }}天</span>
        <!-- 到期前 2 天续费提醒(付费会员+试用都提示, 联系管理员续费) -->
        <span v-if="!user.isAdmin && user.memberLevel !== 2 && user.memberDaysLeft >= 0 && user.memberDaysLeft <= 2"
              class="member-badge renew-badge" title="请尽快续费, 联系管理员(微信号 poet-1986)">
          <i class="fa fa-exclamation-circle"></i> 还剩{{ user.memberDaysLeft }}天续费
        </span>
        <button class="mini-btn" title="修改个人资料(手机号/邮箱/微信名)" @click="profileModal.open()">资料</button>
        <button class="mini-btn" title="修改密码" @click="changePwdModal.open()">改密</button>
        <button class="mini-btn logout-btn" title="退出当前账号" @click="logout">退出</button>
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
import { ref } from 'vue'
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
  color: var(--accent); letter-spacing: 6px;
  line-height: 1.1;
  padding-right: 2px; /* 抵消 letter-spacing 右侧留白, 让整体居中更紧 */
}
.nav-brand-text { display: flex; flex-direction: column; gap: 1px; line-height: 1.1; }
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
.user-name {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 13px; color: var(--text-secondary); padding: 0 6px;
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
  /* 手机上隐藏用户名文本(保留会员徽标), 节省空间 */
  .user-name { display: none; }
  .mini-btn { padding: 4px 8px; font-size: 11px; white-space: nowrap; }
  .member-badge { font-size: 10px; padding: 1px 6px; }
}
</style>