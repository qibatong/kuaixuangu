<template>
  <div class="stat-cards">
    <div class="stat-card"><div class="stat-num">{{ stats.total ?? '-' }}</div><div class="stat-label">注册用户</div></div>
    <div class="stat-card"><div class="stat-num">{{ stats.today ?? '-' }}</div><div class="stat-label">今日注册</div></div>
    <div class="stat-card"><div class="stat-num">{{ stats.active ?? '-' }}</div><div class="stat-label">活跃用户(有选股)</div></div>
    <div class="stat-card"><div class="stat-num">{{ stats.invited ?? '-' }}</div><div class="stat-label">受邀注册</div></div>
    <div class="stat-card"><div class="stat-num small">{{ stats.top_inviter ? stats.top_inviter.username : '-' }}</div><div class="stat-label">Top邀请人({{ stats.top_inviter ? stats.top_inviter.n + '人' : '' }})</div></div>
  </div>
</template>

<script setup>
// 管理后台用户统计卡片(纯展示, 数据由父组件注入)
defineProps({
  stats: { type: Object, default: () => ({}) }
})
</script>

<style scoped>
.stat-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 14px; }
.stat-card { background: var(--bg-hover); border: 1px solid var(--border-soft); border-radius: 10px; padding: 16px; text-align: center; }
.stat-num { font-size: 26px; font-weight: 700; color: #ffd700; }
.stat-num.small { font-size: 16px; color: #a0e0ff; }
.stat-label { margin-top: 6px; color: #aaa; font-size: 12px; }

/* 浅色主题覆盖 */
body[data-bg="light"] .stat-num { color: #1a1d26; }
body[data-bg="light"] .stat-num.small { color: #1a1d26; }
</style>
