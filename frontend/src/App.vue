<template>
  <div class="container">
    <div class="hero-section">
      <img src="/logo.jpg" class="hero-logo" alt="快选 Kuaixuan">
      <div class="hero-text">
        <span class="dominant-title">快选</span><br>
        <span class="title-sub">AI选股，仅供参考</span>
      </div>
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
    </div>
    <router-view />
    <Watermark />
    <div class="footnote">
      <i class="fa fa-bullhorn"></i> 9:30前可唯一选股并缓存 | 9:30后仅更新实时涨幅 | 实时涨幅＜竞价涨幅自动标绿 | 通达信导入：首次需下载工具并勾选通达信「监控剪贴板」一次 | 股票池10小时防刷新锁定
    </div>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { useTheme, BGS, FONTS } from './composables/useTheme'
import Watermark from './components/Watermark.vue'

const { bg, font, setBg, setFont, load } = useTheme()

// 启动加载主题(prefs/本地); 移动端检测
onMounted(async () => {
  if (/Android|iPhone|iPad|iPod|Mobile|MicroMessenger/i.test(navigator.userAgent)) {
    document.body.classList.add('is-mobile')
  }
  load()
})
</script>

<style scoped>
.theme-picker {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: var(--bg-card);
  border: 1px solid var(--border-soft);
  border-radius: 20px;
  padding: 6px 12px;
}
.theme-label { color: var(--text-muted); font-size: 13px; }
.theme-dot {
  width: 18px; height: 18px; border-radius: 50%;
  border: 2px solid var(--border-soft);
  cursor: pointer; padding: 0; transition: transform 0.15s, border-color 0.15s;
}
.theme-dot:hover { transform: scale(1.2); }
.theme-dot.active { border-color: var(--text-main); box-shadow: 0 0 6px var(--border-soft); }
.font-picker {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: var(--bg-card);
  border: 1px solid var(--border-soft);
  border-radius: 20px;
  padding: 6px 12px;
}
.font-btn {
  min-width: 22px; height: 22px; line-height: 1;
  border: 1px solid var(--border-soft);
  border-radius: 12px;
  background: var(--bg-input);
  color: var(--text-secondary);
  font-size: 13px; font-weight: 600;
  cursor: pointer; padding: 0 5px;
  transition: transform 0.15s, border-color 0.15s, background 0.15s, color 0.15s;
}
.font-btn:hover { transform: scale(1.1); border-color: var(--accent); }
.font-btn.active { border-color: var(--accent); background: var(--accent); color: #fff; }
</style>