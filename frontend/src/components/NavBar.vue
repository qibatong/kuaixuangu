<template>
  <!-- 全站顶部导航栏: 收纳各页面入口, 当前页高亮; 移动端自动换行 -->
  <nav class="nav-bar">
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
  </nav>
</template>

<script setup>
// 全站导航栏: 页面入口收纳, 避免首页按钮堆积
import { useUserStore } from '../stores/user'

const user = useUserStore()
</script>

<style scoped>
.nav-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  background: var(--bg-card);
  border: 1px solid var(--border-soft);
  border-radius: 10px;
  padding: 8px 10px;
  margin: 0 0 18px;
}
.nav-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 18px;
  border-radius: 8px;
  font-size: 14px;
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
/* 浅色主题: 高亮用深棕底 */
body[data-bg="light"] .nav-item.router-link-active {
  background: rgba(255, 180, 0, 0.2);
  border-color: #c79100;
  color: #8a5500;
}
</style>
