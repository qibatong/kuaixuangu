// 「快选股」安卓 App 下载 —— 全站唯一来源（落地页首屏弹层 / 中部下载区块 / 后续页脚都引这里）
//
// 2026-10-08 主人要求：**未登录访客也要能下载 App**。
//   · 未登录访客看到的首页 = views/LandingView.vue（匿名落地页），
//     原先整页只有「注册 / 登录」两个出口，没有任何下载入口 ⇒ 本模块就是那个入口的配置源。
//
// 两个入口的分工（别搞混，微信行为完全不同）：
//   ① APP_PAGE_URL（下载页 /app.html）＝ **唯一推荐入口**
//      微信内打开会弹「请在浏览器中打开」引导遮罩；页面自带安装教程 / 版本 / 大小 / 风险提示。
//      🔴 二维码扫出来的就是它 —— 微信只拦 APK 直链，不拦网页。
//   ② APP_APK_URL（APK 直链）＝ 仅桌面浏览器「一键到手」；**微信内点它毫无反应**。
//
// ⚠️ 升级版本时：改 APP_VERSION（APK 文件名随之派生）+ 换 frontend/public/app-qr.png。
//    二维码编码的是**下载页 URL**（不是 APK），下载页 URL 长期不变 ⇒ 二维码可长期复用。
export const APP_VERSION = 'v1.8.3'
export const APP_SIZE = '26.5 MB'
export const APP_MIN_OS = 'Android 7.0'

// 下载页（生产机 /opt/kuaixuan/dist/app.html）；用相对路径 ⇒ 测试机/生产机都能自测
export const APP_PAGE_URL = '/app.html'

// APK 直链（生产机 /opt/kuaixuan/download/）；文件名带版本号，故由 APP_VERSION 派生
export const APP_APK_URL = `/download/kuaixuan-${APP_VERSION.replace(/^v/, '')}.apk`

// 下载二维码（frontend/public/app-qr.png → 线上 /app-qr.png）
export const APP_QR = '/app-qr.png'
