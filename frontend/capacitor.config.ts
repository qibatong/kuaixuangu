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
    // 2026-10-04：以上 keyboardResize 之前是**死配置**——
    //   它属于 @capacitor/keyboard 插件，插件没装 ⇒ 这段被静默忽略。
    //   现已安装 @capacitor/keyboard@8（见 package.json），配置项才真正生效。
    //   取值说明：'body' = 插件在 web 侧压 body 高度补了一段 padding，不缩放 WebView
    //   ⇒ 不会破坏 env(safe-area-inset-*) 的固定底栏；'native' 会走 adjustResize。
    //   固定底栏 + 绝对定位元素较多的页面（现在的首页）用 'body' 更稳。
    keyboardResize: 'body',
  },
  plugins: {
    // 返回键由前端接管（见 src/composables/useAndroidBack.js）：
    // 有历史 → router.back()；无历史 → 双击退出 + Toast。
    App: {},

    // 2026-10-04：消除"首屏白屏"（品牌红启动屏 + 消失时机接管）。
    //   机制：launchAutoHide=true 时，bridge 就绪后还会**继续显示 launchShowDuration**
    //   才自动撤屏 —— 远端页面渲染慢时，用户看到的是红屏而不是白屏。
    //   两层保险：
    //   ① 新前端（已 build，部署后生效）在 App.vue mount 后主动 SplashScreen.hide()
    //     ⇒ 页面一好就撤屏，无白帧也无多余等待；
    //   ② launchShowDuration=4000 是**原生侧兜底**：前端旧版/加载失败/异常时，
    //     最多 4s 自动撤屏 —— 实测 launchAutoHide:false 会把旧前端卡死在启动屏，绝不能用。
    SplashScreen: {
      launchAutoHide: true,
      launchShowDuration: 4000,
      backgroundColor: '#e5484d',
      showSpinner: false,
      splashFullScreen: false,
    },
  },
}

export default config
