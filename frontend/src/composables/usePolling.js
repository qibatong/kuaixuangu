// usePolling: 统一轮询 composable
// - 自动清理定时器(onBeforeUnmount)
// - visibilitychange 暂停(页面不可见时不空跑, 回来立即刷新一次)
// - enabled 条件控制(如历史模式暂停实时刷新)
// 2026-09-05 新增失败退避(backoff):
//   fn 可返回 boolean 或 Promise<boolean> 表示成败(false=失败)。
//   连续失败时轮询间隔按 intervalMs × 2^失败次数 递增(上限 maxBackoffMs),
//   成功一次即重置。用于调用方内部已 try/catch 静默的场景(如 AuctionView
//   ensureTabData), 避免服务端抖动时被前端以固定频率持续打。
//   向后兼容: fn 不返回值(undefined)视为成功, 行为与改造前一致。
import { onBeforeUnmount, ref, watch } from 'vue'

export function usePolling(fn, intervalMs,
                           { enabled = true, immediate = true,
                             backoff = false, maxBackoffMs = 5 * 60 * 1000 } = {}) {
  let timer = null
  let failStreak = 0
  const active = ref(enabled)
  const lastError = ref(null)

  function currentDelay() {
    if (!backoff || failStreak <= 0) return intervalMs
    // 2^失败次数 递增, 封顶 maxBackoffMs(默认 5 分钟)
    return Math.min(intervalMs * Math.pow(2, failStreak), maxBackoffMs)
  }

  function schedule() {
    stop()
    if (!active.value) return
    timer = setTimeout(tick, currentDelay())
  }

  async function tick() {
    if (!active.value) return
    let ok = true
    try {
      const r = fn()
      if (r && typeof r.then === 'function') {
        const v = await r
        ok = (v === undefined) ? true : !!v     // Promise 版: undefined 视为成功
      } else if (typeof r === 'boolean') {
        ok = r
      }
    } catch (e) {
      ok = false
      lastError.value = e
    }
    if (ok) {
      failStreak = 0
      lastError.value = null
    } else {
      failStreak++
    }
    if (active.value) schedule()
  }

  function start() {
    stop()
    if (!active.value) return
    if (immediate) tick()
    else schedule()
  }

  function stop() {
    if (timer) { clearTimeout(timer); timer = null }
  }

  function onVisibility() {
    if (document.hidden) {
      stop()
    } else {
      tick()    // 回到前台立即刷新一次(并按需重排下一次)
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

  return {
    start, stop, active, lastError,
    /** 重置退避计数(用户手动刷新后立即恢复正常频率) */
    resetBackoff() { failStreak = 0 },
    /** 当前连续失败次数(供 UI 提示) */
    failCount() { return failStreak },
  }
}
