// 「数据更新于 HH:MM:SS」的取值器（2026-09-27 v4.11.63《快选移动端追加清单》§二·4）
// ============================================================================
// ★ 与页头那个 `{{ bjTime }}` 时钟是两回事，必须分开：
//     · 时钟    = 现在几点（每秒跳），回答"现在什么时候"；
//     · 数据戳  = 列表里的数据是什么时候取回来的（只在**成功拉到数据**时前进），
//                 回答"我现在看到的这屏数据有多新"。
//   清单要的是后者（"数据更新于"），所以不能拿时钟顶替 —— 那会让"10 分钟没更新成功"
//   看起来和"刚刚更新过"一模一样。
//
// ★ 只在成功路径调用 mark()。失败 / 降级 / 配额拦截一律**不得**推进时间戳：
//   落后时间戳本身就是告警信号（配合页头的"稍后重试"角标）。这是"让静默不可能发生"
//   在本项目里的又一次落地 —— 时间戳的意义就在于它会不动。
//
// ★ 2026-09-27《前端收尾·数据新鲜度》(总工单批次五 P0) 补 stale：
//   实时轮询模式(interval>0)下，超过 5 分钟没有成功 mark → stale=true，
//   DataStamp 据此**标灰 + 警告**。内部每 30s 检测一次(与轮询同节奏)，不发额外请求。
//   SSR/单测(无 window)不启动定时器，stale 保持 false，避免 Node 里开 setInterval。
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { bjTimeStr } from '../utils/time'

const STALE_AFTER_MS = 5 * 60 * 1000   // 5 分钟未成功刷新 = 数据停滞

export function useDataStamp() {
  const at = ref('')        // 'HH:MM:SS'；空串 = 本页还没成功取过一次
  const ok = ref(false)     // 是否至少成功过一次（决定显示「更新于 …」还是「等待首次更新…」）
  const updatedAt = ref(0)  // 最近一次成功 mark 的时刻(ms)，0 = 从未
  const stale = ref(false)  // 实时轮询模式下超过 5 分钟未成功刷新

  /** 在**成功拿到数据**之后调用。 */
  function mark() {
    at.value = bjTimeStr()
    ok.value = true
    updatedAt.value = Date.now()
    stale.value = false     // 刚成功过，立即解除停滞态
  }

  let timer = null
  function tick() {
    if (!ok.value) return
    stale.value = updatedAt.value > 0 && (Date.now() - updatedAt.value) > STALE_AFTER_MS
  }
  if (typeof window !== 'undefined') {
    onMounted(() => { tick(); timer = setInterval(tick, 30 * 1000) })
    onBeforeUnmount(() => { if (timer) clearInterval(timer) })
  }

  return { at, ok, updatedAt, stale, mark }
}
