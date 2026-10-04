import type { CapacitorConfig } from '@capacitor/cli'

/**
 * 快选安卓壳配置（2026-10-04，方案 C：Capacitor **远端加载**壳）。
 *
 * ★ 为什么是远端加载（而不是把 dist 打进 APK）：
 *   现在前端一天能发好几版（`scripts/_kx_fe_deploy_cz.py` 一条命令换盘）。
 *   若打包本地 dist，改个文案都要重新打包 + 签名 + 让用户更新 APK，
 *   运营成本会吃掉 APK 的全部收益。远端壳 ⇒ 网页更新即生效，与现状一致。
 *
 * 🔴 url 必须是 **https + 域名**（不能是裸 IP）：
 *   · Service Worker / WebPush 只在安全上下文（https 或 localhost）可用；
 *   · 生产证书 CN=www.kuaixuangu.cn（可信 CA，有效期至 2027-03-01），已实测
 *     `https://www.kuaixuangu.cn` 返回 200 且就是当前线上版本。
 *   · 裸域 `kuaixuangu.cn` 目前**未解析**（curl 返回 000），只用 www。
 */
const config: CapacitorConfig = {
  appId: 'com.kuaixuan.app',
  appName: '快选',
  webDir: 'dist',
  server: {
    url: 'https://www.kuaixuangu.cn',
    // 远端加载：页面/接口全走线上域名，前端**零改动**（api 全是相对路径 /api/...）
    cleartext: false,
  },
  android: {
    // 不允许 http 混合内容：全站 https，避免 WebView 静默拦掉资源
    allowMixedContent: false,
    // 键盘弹出时**缩放页面**而不是平移 WebView（网页侧已用 env(safe-area-inset-*)，
    // 缩放会破坏固定底栏；平移是更稳的选择）
    keyboardResize: 'body',
  },
  plugins: {
    // 返回键由前端接管（见 src/composables/useAndroidBack.js）：
    // 有历史 → router.back()；无历史 → 双击退出 + Toast。
    App: {},
  },
}

export default config
