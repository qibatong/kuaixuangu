import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { setupGlobalErrorCapture } from './utils/logger'
// 霞鹜文楷等宽 LXGW WenKai Mono — SIL OFL 1.1 免费商用开源字体
// 字体分包 woff2 + unicode-range 按需加载, 只下载实际用到的字形片段
import 'lxgw-wenkai-webfont/lxgwwenkaimono-regular.css'
import 'lxgw-wenkai-webfont/lxgwwenkaimono-bold.css'
import './styles/main.css'

setupGlobalErrorCapture()

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
