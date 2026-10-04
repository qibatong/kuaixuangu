import { onUnmounted, ref } from 'vue'

/**
 * 响应式媒体查询 —— 手机端「列显隐」机制的地基（2026-10-04 手机端批次 2）。
 *
 * ★ 为什么需要：体检报告 P1-3 的遗留项 —— 此前表格「隐藏列」是**按 tab 写死**的
 *   （`StockTable.vue` 的 `isSpot` / `hasMainNet`，数据驱动、与屏宽无关），
 *   ≤480 小屏上 9 列的「昨涨停」表只能横滑才看得全，手机端读一行要来回拖。
 *   现在给出**一处声明、处处可用**的屏宽信号，页面按需 `v-if` 掉次要列即可。
 *
 * 🔴 用法纪律（与 `usePolling` 同源的坑）：必须在 **setup 顶层**调用，
 *   写在 `onMounted` 里拿不到组件实例 ⇒ 卸载时监听器不会被移除（内存泄漏 + 切页仍触发）。
 *
 * @param {string} query 媒体查询串，如 '(max-width: 480px)'
 * @returns {import('vue').Ref<boolean>} 当前是否命中
 */
export function useMediaQuery(query) {
  const hit = ref(false)
  if (typeof window === 'undefined' || !window.matchMedia) return hit   // SSR 兜底：一律不隐藏
  const mq = window.matchMedia(query)
  hit.value = mq.matches
  const onChange = (e) => { hit.value = e.matches }
  // Safari <14 只有 addListener
  if (mq.addEventListener) mq.addEventListener('change', onChange)
  else mq.addListener(onChange)
  onUnmounted(() => {
    if (mq.removeEventListener) mq.removeEventListener('change', onChange)
    else mq.removeListener(onChange)
  })
  return hit
}

/** ≤480 小屏（main.css 五档断点里的「小屏」档） */
export function useIsSmall() {
  return useMediaQuery('(max-width: 480px)')
}
