import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// 构建产物输出到 dist/, 由 Nginx 静态托管
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: false,
    // 2026-09-30 v4.11.83 (P2-2): echarts(+zrender 依赖) 单独成块。
    //   ① 名字稳定 ⇒ 该块内容变了才换 hash, 与业务代码互不牵连(缓存复用);
    //   ② 配合 src/utils/echarts.js 的按需注册, 该块从 1.13MB 降到只含
    //      line/bar/candlestick/sankey/tree + 7 个组件的体量。
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules/echarts') || id.includes('node_modules/zrender')) return 'echarts'
        }
      }
    }
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
