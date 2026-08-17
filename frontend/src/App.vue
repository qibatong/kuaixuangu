<template>
  <div class="container">
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