import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { setupGlobalErrorCapture } from './utils/logger'
// 思源黑体 Noto Sans SC — SIL OFL 1.1 免费商用, Google + Adobe 出品, 字重 400/700
// 分包加载, 简体中文页面首屏约 200-400KB。默认正文字体, 全局加载。
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
