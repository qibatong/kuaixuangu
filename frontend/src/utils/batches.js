// 批次挑选（纯函数 + 单测）
// ============================================================================
// 「今日票战报」要回答的是：**今天 9:25 定格选出的那批票，现在怎么样了**。
// 所以挑批次的判据必须与后端 /api/stocks 的首屏回显完全一致：
//   ① 只在**当天**批次里挑（没有当天批次 ⇒ 退回最近交易日，并由上层显式标注「非今日」）；
//   ② 当天批次必须 `freeze_ready === true` —— 后端给这个字段的语义是
//      「该批次建立在当日 9:25 定格之上」。
//      🔴 定格尚未落库时产生的批次，其竞价字段整批取自**上一交易日**
//      （2026-09-18 主人看到的「刷出来是昨天的数据」就是这个，样本 #1674）。
//      所以 NOT-ready 的当天批次**宁可不显示**，也不能当成今日战报。
//      ⚠️ 判据只能由后端算（前端拿不到 snapshot_bid 的落库时刻）—— 这里只消费，不自己猜时刻。
//
// 优先级：当天 lock(用户主动锁) > 当天 auto(系统 9:26 统一下发) > 当天其它。

function tsOf(b) {
  const v = Number(b && b.ts)
  return isFinite(v) ? v : 0
}

function dateOf(b) {
  return String((b && b.batch_date) || '')
}

/**
 * @param {Array} batches /api/history 的 batches
 * @param {string} today   北京日期 YYYY-MM-DD
 * @returns {{batch: Object|null, isToday: boolean, pending: boolean}}
 *   pending=true 表示「今天确实有批次，但定格还没落库」⇒ 上层显示等待文案，
 *   而不是显示上一交易日的名单冒充今日。
 */
export function pickReportBatch(batches, today) {
  const arr = (batches || []).filter((b) => b && b.id !== undefined && b.id !== null)
  if (!arr.length) return { batch: null, isToday: false, pending: false }

  const todayArr = arr.filter((b) => dateOf(b) === today)
  if (todayArr.length) {
    const ready = todayArr.filter((b) => b.freeze_ready === true)
    if (!ready.length) return { batch: null, isToday: true, pending: true }
    const pick = ready.find((b) => b.action === 'lock' && !b.auto_applied)
      || ready.find((b) => !!b.auto_applied)
      || ready[0]
    return { batch: pick, isToday: true, pending: false }
  }

  // 无当天批次 → 退回最近交易日（上层必须标注「非今日」）
  const sorted = [...arr].sort((a, b) => dateOf(b).localeCompare(dateOf(a)) || tsOf(b) - tsOf(a))
  const date = dateOf(sorted[0])
  const sameDate = sorted.filter((b) => dateOf(b) === date)
  const pick = sameDate.find((b) => b.action === 'lock' && !b.auto_applied)
    || sameDate.find((b) => !!b.auto_applied)
    || sameDate[0]
  return { batch: pick, isToday: false, pending: false }
}
