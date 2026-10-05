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
  /**
   * iOS 壳（2026-10-05 新增，与安卓同一套远端加载方案：server.url 指向线上域名，
   * 网页更新即时生效，不用重发 App）。
   *
   * 🔴 contentInset: 'never' 是**关键**：
   *   Capacitor iOS 默认 'always' 会自动给 WebView 加安全区内边距 ⇒ 网页里
   *   `env(safe-area-inset-*)` 全部变 0，我们就**画不出**状态栏那条同色带
   *   （见 src/styles/main.css 的 body.is-ios-shell::before）。
   *   设成 'never' 后 WebView 全屏贴边，安全区交给网页 CSS 自己处理（与安卓壳一致）。
   */
  ios: {
    contentInset: 'never',
    // WebView 背后的底色：与启动屏 / 网页浅色底一致，避免页面切换瞬间闪黑
    backgroundColor: '#ffffff',
    // 保留回弹手感（与安卓壳观感一致）
    scrollEnabled: true,
    // 关掉长按链接预览：行情表里长按很容易被误触发，弹出半屏预览很打断操作
    allowsLinkPreview: false,
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
    // 2026-10-05（v4.11.97 第八轮）：backgroundColor 由品牌红 `#e5484d` 改 **纯白 `#ffffff`**。
    //   🔴 为什么必须连这里一起改：native 那三处（colors.xml 的 splash_background /
    //      splash_brand.xml / styles.xml）已经把**系统启动屏**改成白的，但本插件的
    //      `show()` 会**再刷一层 backgroundColor** ⇒ 只改 native 就会「白底一闪又红回来」，
    //      等于没修（主人原话：「首页启动全是红色」）。两处必须同色。
    //   ⚠️ 本文件改动**必须同步** `android/app/src/main/assets/capacitor.config.json` ——
    //      原生读的是那份 JSON，而沙箱里 `npx cap sync` 跑不了（批量删除守卫），本次手动同步。
    SplashScreen: {
      launchAutoHide: true,
      launchShowDuration: 4000,
      backgroundColor: '#ffffff',
      showSpinner: false,
      splashFullScreen: false,
    },
  },
}

export default config
