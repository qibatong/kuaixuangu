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
.stat-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: var(--s3); margin-bottom: var(--s4); }
.stat-card { background: var(--bg-subtle); border: 1px solid var(--border-soft); border-radius: var(--r-lg); padding: var(--s4); text-align: center; }
.stat-num { font-size: var(--fs-display); font-weight: 700; color: var(--gold); }
.stat-num.small { font-size: var(--fs-lg); color: var(--accent-text); }
.stat-label { margin-top: var(--s2); color: var(--text-muted); font-size: var(--fs-xs); }

/* 浅色主题覆盖 */
body[data-bg="light"] .stat-num { color: #1a1d26; }
body[data-bg="light"] .stat-num.small { color: #1a1d26; }
</style>
