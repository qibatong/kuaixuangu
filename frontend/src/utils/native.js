import { Capacitor } from '@capacitor/core'

/**
 * 运行环境判定（2026-10-04 安卓壳）。
 *
 * 网页（浏览器/PWA）⇒ false；Capacitor 壳（安卓 APK）⇒ true。
 *
 * 用途：
 *   · **Windows 专属功能**在安卓上要隐藏：通达信导入工具 `tdx_import.exe`、
 *     「下载自选股(自动导入)」按钮（`PoolView.vue`），安卓上点了也用不了。
 *   · 壳内才需要的行为：物理返回键接管（`useAndroidBack`）。
 *
 * 🔴 判定必须用 `isNativePlatform()`（读的是 WebView 注入的 Capacitor 标记），
 *    不要自己 UA 嗅探 —— WebView UA 里那串 `; wv` 在国内 ROM 上不可靠。
 */
export function isNative() {
  try {
    return Capacitor.isNativePlatform()
  } catch (e) {
    return false
  }
}

/**
 * 安卓环境（Capacitor 壳 **或** 安卓浏览器）—— 用于屏蔽在安卓上根本用不了的能力。
 *
 * 2026-10-04 实测依据：安卓 Chrome 的 WebPush 必须经 Google FCM，国内网络连不上，
 * subscribe 直接抛 `Registration failed - push service error`；壳内 WebView 同理。
 * iOS Safari（添加到主屏幕后）走 Apple APNs，**不受影响** ⇒ 只屏蔽安卓，iPhone 保留推送。
 *
 * 若将来接了厂商通道（小米/华为/OPPO Push）或 FCM 可用，把这里改回 false 即可恢复入口。
 */
export function isAndroidEnv() {
  if (isNative()) return true
  try {
    return /Android/i.test(navigator.userAgent || '')
  } catch (e) {
    return false
  }
}
