import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 构建产物输出到 dist/, 由 Nginx 静态托管
export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: false
  },
  server: {
    port: 5173,
    proxy: {
      // 开发环境代理到后端, 生产由 Nginx 反代
      '/api': {
        target: 'http://127.0.0.1:8010',
        changeOrigin: true
      },
      '/download': {
        target: 'http://127.0.0.1:8010',
        changeOrigin: true
      }
    }
  }
})
