import { onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Capacitor } from '@capacitor/core'
import { App } from '@capacitor/app'
import { showToast } from '../utils/toast'

/**
 * 安卓物理返回键接管（2026-10-04 安卓壳）。
 *
 * ★ 为什么必须做：原生 WebView 里按返回键，**默认行为是直接退出 App**——
 *   用户点进个股详情/消息页后一按返回就回到桌面，体验上等于"页面打不开第二次"。
 *
 * 规则：
 *   · 有历史 ⇒ `router.back()`（网页内返回，与浏览器一致）
 *   · 已在根路径/无历史 ⇒ **双击退出**：2s 内再按一次才 `App.exitApp()`，并给 Toast 提示
 *
 * 🔴 纪律（同 `usePolling` / `useMediaQuery`）：必须在 **setup 顶层**调用，
 *    写在 `onMounted` 里卸载时解绑不了 ⇒ 切页后监听器残留、返回键被重复处理。
 *
 * @returns {import('vue').Ref<boolean>} 是否已接管（仅安卓壳内为 true；网页恒 false）
 */
export function useAndroidBack() {
  const router = useRouter()
  const armed = ref(false)
  let lastBackAt = 0
  let handle = null

  const onBack = ({ canGoBack }) => {
    // Capacitor 的 canGoBack 来自 WebView.canGoBack() —— 注意它只认 **真实的 history 记录**,
    // SPA 内部 router.push 产生的记录算在内, 但首次进入的那条**不算**可返回。
    if (canGoBack && window.location.pathname !== '/') {
      router.back()
      return
    }
    const now = Date.now()
    if (now - lastBackAt < 2000) {
      App.exitApp()
      return
    }
    lastBackAt = now
    showToast('再按一次退出快选股', 'info')
  }

  const setup = async () => {
    if (!Capacitor.isNativePlatform() || Capacitor.getPlatform() !== 'android') return
    try {
      handle = await App.addListener('backButton', onBack)
      armed.value = true
    } catch (e) {
      // 网页端/插件未就绪时静默失败：不影响正常浏览
      console.warn('[useAndroidBack] 返回键接管失败:', e && e.message)
    }
  }
  setup()

  onUnmounted(async () => {
    if (handle && typeof handle.remove === 'function') {
      try {
        await handle.remove()
      } catch (e) {
        /* 已卸载 */
      }
    }
    handle = null
  })

  return armed
}
