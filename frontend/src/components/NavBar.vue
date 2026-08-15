<template>
  <!-- 全站顶部导航栏: 左页面入口 tabs, 右主题/字号/账户工具 -->
  <nav class="nav-bar">
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
      <!-- 账户工具: 已登录显示用户名/改密/退出 -->
      <div v-if="user.isLoggedIn" class="user-tools">
        <span class="user-name" :title="user.username"><i class="fa fa-user-circle"></i> {{ user.username }}</span>
        <button class="mini-btn" title="修改密码" @click="changePwdModal.open()">改密</button>
        <button class="mini-btn logout-btn" title="退出当前账号" @click="logout">退出</button>
      </div>
      <div v-else class="user-tools">
        <router-link to="/login" class="mini-btn login-btn">登录</router-link>
      </div>
    </div>

    <!-- 改密弹层(全站唯一, 改密按钮来自 NavBar) -->
    <ChangePwdModal ref="changePwdModal" />
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

const router = useRouter()
const user = useUserStore()
const { bg, font, setBg, setFont } = useTheme()

const changePwdModal = ref(null)

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
  padding: 8px 4px;
  margin: 0 0 18px;
}
.nav-tabs { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.nav-tools { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.nav-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
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

/* 浅色主题高亮 */
body[data-bg="light"] .nav-item.router-link-active {
  background: rgba(255, 180, 0, 0.2); border-color: #c79100; color: #8a5500;
}
</style>