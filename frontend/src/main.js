import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { setupGlobalErrorCapture } from './utils/logger'
// 思源黑体 Noto Sans SC — SIL OFL 1.1 免费商用, Google + Adobe 出品, 字重 400/700
// 分包加载, 简体中文页面首屏约 200-400KB。默认正文字体, 全局加载。
// 2026-09-30 v4.11.84 (P1-5): Font Awesome 4.7 **本地子集**(5KB) —— 原先 index.html 走 cdnjs 同步外链,
//   既阻塞首屏, 断网/内网/被墙时全站图标还会变方框。子集由 scripts/_kx_gen_fa_subset.py 生成,
//   只含 @font-face + 源码实际引用的图标(字体 77KB 在 public/fonts/)。**新增图标要重跑生成脚本。**
import './styles/fontawesome-subset.css'
import '@fontsource/noto-sans-sc/400.css'
import '@fontsource/noto-sans-sc/700.css'
// 可选字体按需加载(霞鹜等宽 lxgw / 思源宋体 serif): 不再全局 import,
// 由 useTheme.js 在用户切换字体族时动态加载, 默认用户首屏不下载(约省 22MB)。
import './styles/main.css'

setupGlobalErrorCapture()

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')

// 2026-10-03 · PWA 离线壳（主人决策：先做 PWA，再谈 APK 封装）
//   · 只在 **生产构建 + https/localhost** 注册（SW 需要安全上下文；dev 下注册会干扰 HMR）。
//   · 注册失败**静默**（老浏览器/内网 http 访问只是没有离线能力，不影响正常使用）。
//   · 发现新版本 ⇒ 派 `kx:sw-update`，由 PwaBar.vue 显示「刷新」按钮 —— 🔴 **不自动 reload**，
//     竞价盘中强刷会把用户正在看的名单打断。
//   · ⚠️ 部署后要验 `/sw.js` 的 content-type 是 application/javascript（生产 nginx 的 SPA 回退
//     曾把静态资源吞成 text/html ⇒ 注册失败且报错很难懂，见 AGENTS.md v4.11.84）。
if (import.meta.env.PROD && 'serviceWorker' in navigator &&
    (location.protocol === 'https:' || location.hostname === 'localhost')) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').then((reg) => {
      if (!reg) return
      // 挂载前就已经有 waiting（例如上次刷新时刚发版）也要提示
      if (reg.waiting) window.dispatchEvent(new CustomEvent('kx:sw-update'))
      reg.addEventListener('updatefound', () => {
        const sw = reg.installing
        if (!sw) return
        sw.addEventListener('statechange', () => {
          if (sw.state === 'installed' && navigator.serviceWorker.controller) {
            window.dispatchEvent(new CustomEvent('kx:sw-update'))
          }
        })
      })
    }).catch(() => { /* 不支持/不安全上下文：无离线能力，照常使用 */ })
  })
}
