<template>
  <div class="container">
    <div class="hero-section">
      <img src="/logo.png" class="hero-logo" alt="快选 Kuaixuan">
      <div class="hero-text">
        <span class="dominant-title">快选</span><br>
        <span class="title-sub">AI选股，仅供参考</span>
      </div>
    </div>
    <router-view />
    <div class="footnote">
      <i class="fa fa-bullhorn"></i> 9:30前可唯一选股并缓存 | 9:30后仅更新实时涨幅 | 实时涨幅＜竞价涨幅自动标绿 | 通达信导入：首次需下载工具并勾选通达信「监控剪贴板」一次 | 股票池10小时防刷新锁定
    </div>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'

// 移动端检测: 隐藏通达信按钮, 显示手机引导条
onMounted(() => {
  if (/Android|iPhone|iPad|iPod|Mobile|MicroMessenger/i.test(navigator.userAgent)) {
    document.body.classList.add('is-mobile')
  }
})
</script>
