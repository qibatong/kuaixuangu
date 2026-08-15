<template>
  <div class="container">
    <div class="hero-section">
      <!-- 第一行: logo + 品牌名(横排, 视觉中心) -->
      <div class="hero-brand">
        <img src="/logo.jpg" class="hero-logo" alt="快选 Kuaixuan">
        <span class="dominant-title">快选</span>
      </div>
      <!-- 第二行: slogan(独立居中, 在 logo+快选 下方) -->
      <div class="hero-slogan">一键筛选 · 高效复盘 · 客观输出标的</div>
    </div>
    <NavBar />
    <router-view />
    <Watermark />
    <div class="footnote">
      <i class="fa fa-bullhorn"></i> 9:30前可唯一选股并缓存 | 9:30后仅更新实时涨幅 | 实时涨幅＜竞价涨幅自动标绿 | 通达信导入：首次需下载工具并勾选通达信「监控剪贴板」一次 | 股票池10小时防刷新锁定
    </div>
    <div class="disclaimer">本平台仅提供软件工具使用权，不构成任何投资建议，股市有风险，投资需谨慎。</div>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import Watermark from './components/Watermark.vue'
import NavBar from './components/NavBar.vue'

onMounted(() => {
  if (/Android|iPhone|iPad|iPod|Mobile|MicroMessenger/i.test(navigator.userAgent)) {
    document.body.classList.add('is-mobile')
  }
})
</script>

<style scoped>
/* hero 改纵向布局: 第一行 logo+品牌名 横排居中, 第二行 slogan 独立居中 */
.hero-section { flex-direction: column; gap: 6px; align-items: center; }
.hero-brand { display: flex; align-items: center; gap: 12px; }

/* 品牌 slogan: 紧邻品牌名下方, 体现产品定位 */
.hero-slogan {
  margin-top: 2px;
  font-size: 13px;
  color: var(--accent);
  letter-spacing: 1px;
  font-weight: 500;
  opacity: 0.92;
}
/* 网页底部免责声明 */
.disclaimer {
  text-align: center;
  font-size: 12px;
  color: var(--text-muted);
  margin: 8px 16px 24px;
  line-height: 1.7;
  opacity: 0.75;
  letter-spacing: 0.3px;
}
</style>