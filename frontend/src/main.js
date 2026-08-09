import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { setupGlobalErrorCapture } from './utils/logger'
import './styles/main.css'

setupGlobalErrorCapture()

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
