import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { setupGlobalErrorCapture } from './utils/logger'
// 2026-09-30 v4.11.84 (P1-5): Font Awesome 4.7 **本地子集**(5KB) —— 原先 index.html 走 cdnjs 同步外链,
//   既阻塞首屏, 断网/内网/被墙时全站图标还会变方框。子集由 scripts/_kx_gen_fa_subset.py 生成,
//   只含 @font-face + 源码实际引用的图标(字体 77KB 在 public/fonts/)。**新增图标要重跑生成脚本。**
import './styles/fontawesome-subset.css'
// 🔴 2026-10-05 (S5): **移除** `@fontsource/noto-sans-sc/400.css` + `/700.css` 的全局静态 import。
//   原因(实测): 这两行会把 **203 条 @font-face**(全部 unicode 子集声明)塞进入口 CSS ——
//   入口 CSS 原始体积 319KB(gzip 114KB), 且登录页只有 ~30 个汉字却触发 11 个中文子集
//   woff2 下载 ⇒ 首访 683KB 里 459KB 是字体, FCP/LCP 4.58s 的主因。
//   现在: 默认字体 = 系统中文黑体栈(main.css 的 var(--font-system)), 首访**零 CJK 字体字节**;
//   「思源黑体/思源宋体/霞鹜等宽」三个可选字体全部改为**选中时才动态加载**
//   (见 composables/useTheme.js 的 _FONT_CSS), 默认用户与不切字体的用户永不下载。
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
