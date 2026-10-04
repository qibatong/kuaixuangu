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
