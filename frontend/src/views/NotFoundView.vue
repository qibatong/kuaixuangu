<template>
  <div class="page-shell notfound">
    <div class="nf-code">404</div>
    <h1 class="nf-title">页面不存在</h1>
    <p class="nf-desc">你访问的地址可能已失效、拼写有误，或链接来自旧版本。</p>
    <div class="nf-actions">
      <router-link to="/" class="nf-btn nf-btn-primary"><i class="fa fa-arrow-left"></i> 返回选股页</router-link>
    </div>
    <div class="nf-links">
      <router-link to="/auction">竞价异动</router-link>
      <router-link to="/concept">题材异动</router-link>
      <router-link to="/ladder">涨停梯队</router-link>
      <router-link to="/pool">自选股票池</router-link>
    </div>
  </div>
</template>

<script setup>
// 404 兜底页: 未知路径不再静默重定向首页, 明确告知「页面不存在」并提供常用入口
</script>

<style scoped>
.notfound {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  min-height: 60vh;
  padding: 40px 16px;
  gap: 8px;
}
.nf-code {
  font-size: 5rem;
  font-weight: 800;
  line-height: 1;
  color: var(--accent);
  letter-spacing: 2px;
}
.nf-title {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--text-main);
  margin: 8px 0 4px;
}
.nf-desc {
  font-size: 0.875rem;
  color: var(--text-secondary);
  margin-bottom: 20px;
}
.nf-actions { display: flex; gap: 10px; flex-wrap: wrap; justify-content: center; }
.nf-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 9px 18px;
  border-radius: 6px;
  font-size: 0.875rem;
  font-weight: 600;
  text-decoration: none;
  cursor: pointer;
  transition: transform 0.15s, background 0.15s, border-color 0.15s;
}
.nf-btn-primary {
  background: var(--accent-deep2);
  color: #fff;
  border: 1px solid var(--accent);
}
.nf-btn-primary:hover { transform: translateY(-1px); }
.nf-links {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  justify-content: center;
  margin-top: 20px;
  font-size: 0.8125rem;
}
.nf-links a { color: var(--text-muted); text-decoration: none; transition: color 0.15s; }
.nf-links a:hover { color: var(--accent); }
</style>
