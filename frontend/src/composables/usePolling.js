// usePolling: 统一轮询 composable
// - 自动清理定时器(onBeforeUnmount)
// - visibilitychange 暂停(页面不可见时不空跑, 回来立即刷新一次)
// - enabled 条件控制(如历史模式暂停实时刷新)
import { onBeforeUnmount, ref, watch } from 'vue'

export function usePolling(fn, intervalMs, { enabled = true, immediate = true } = {}) {
  let timer = null
  const active = ref(enabled)

  function run() {
    try { fn() } catch (e) { /* 静默 */ }
  }

  function start() {
    stop()
    if (!active.value) return
    if (immediate) run()
    timer = setInterval(run, intervalMs)
  }

  function stop() {
    if (timer) { clearInterval(timer); timer = null }
  }

  function onVisibility() {
    if (document.hidden) {
      stop()
    } else {
      run()    // 回到前台立即刷新一次
      start()
    }
  }

  watch(active, (v) => {
    if (v) { document.addEventListener('visibilitychange', onVisibility); start() }
    else { stop(); document.removeEventListener('visibilitychange', onVisibility) }
  })

  onBeforeUnmount(() => {
    stop()
    document.removeEventListener('visibilitychange', onVisibility)
  })

  if (active.value) {
    document.addEventListener('visibilitychange', onVisibility)
    start()
  }

  return { start, stop, active }
}
