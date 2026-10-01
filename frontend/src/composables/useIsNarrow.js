import { onBeforeUnmount, ref } from 'vue'

/**
 * 窄屏判定（<=768px），**响应式**（转屏/缩放窗口会跟着变），SSR 安全。
 *
 * 2026-10-01 首页改版引入：手机端首页要换成「盯盘台」，而桌面端首页必须**一字不动**
 * => 不能只用 CSS 藏（那样组件仍会挂载、白白拉数据/出网），需要 JS 判据决定**是否挂载**。
 *
 * 断点 768px 与全站既有约定一致（见 QuickGrid.vue / main.css 的 768 档），
 * 不引入新的断点碎片（_verify/breakpoint_guard.js 棘轮）。
 */
export function useIsNarrow(query = '(max-width: 768px)') {
  const narrow = ref(false)
  try {
    // SSR（冒烟用例）/老浏览器：没有 window.matchMedia => 恒 false（等同桌面，最保守）
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return narrow
    const mq = window.matchMedia(query)
    narrow.value = !!mq.matches
    const on = (e) => { narrow.value = !!e.matches }
    if (typeof mq.addEventListener === 'function') {
      mq.addEventListener('change', on)
      onBeforeUnmount(() => mq.removeEventListener('change', on))
    } else if (typeof mq.addListener === 'function') {
      mq.addListener(on)                     // 老 Safari
      onBeforeUnmount(() => mq.removeListener(on))
    }
  } catch (e) { /* 隐私模式等：保持 false */ }
  return narrow
}
