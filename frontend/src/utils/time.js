// 北京时间工具
export function bjNow() {
  const now = new Date()
  return new Date(now.getTime() + 8 * 60 * 60 * 1000 + now.getTimezoneOffset() * 60 * 1000)
}

export function isBefore930() {
  const bj = bjNow()
  return bj.getHours() < 9 || (bj.getHours() === 9 && bj.getMinutes() < 30)
}

// 重新选股(锁定)截止: **15:00(收盘)前允许**。演进:
//   · 2026-09-20 主人拍板「ai选股放开到10点」→ 上限 9:30 放宽到 10:00(工作日);
//   · 2026-09-24 主人拍板「10 点以后也不要锁定」→ 上限再放宽到 **15:00**;
//     同日拍板「**周末也放开**」→ 去掉原「周末直接 false」分支。
// 与后端 `api/stocks` 快照池 lock 条件同口径: 后端 `scorer.bj_now()` **无工作日判断**,
// 周末/盘后一律走『最近交易日定格』回放 ⇒ 前端不再单独拦周末, 否则与后端行为不一致
// (周末点「锁定」本可出名单, 却弹一个对不上的「禁止重新选股」)。15:00 盘后才拒绝。
// 9:15-9:26:30 竞价段的拦截归 `isPickGateOn()`(它会给「竞价进行中」那个正确文案),
// 故本函数**不重复拦竞价段**, 避免用错文案盖掉选股闸门。
// @param {Date} [bj] 可注入"北京时间视图"的 Date(测试用), 默认取当前
export function isBeforeRelockEnd(bj) {
  const t = bj || bjNow()
  return t.getHours() < 15
}

export function pad2(n) { return String(n).padStart(2, '0') }

export function bjTimeStr() {
  const bj = bjNow()
  return `${pad2(bj.getHours())}:${pad2(bj.getMinutes())}:${pad2(bj.getSeconds())}`
}

// 日期+时间一体显示(体验优化: 日期和时间分开显示没意义, 合并一行)
export function bjDateTimeStr() {
  const bj = bjNow()
  return `${pad2(bj.getMonth() + 1)}-${pad2(bj.getDate())} ${pad2(bj.getHours())}:${pad2(bj.getMinutes())}:${pad2(bj.getSeconds())}`
}

export function fmtDate(d) {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}

export function fmtTsDate(ts) {
  return fmtDate(new Date(ts * 1000))
}

// 今天(北京时间) → 'YYYY-MM-DD'
export function todayBj() {
  const d = new Date(Date.now() + 8 * 3600 * 1000)
  return d.toISOString().slice(0, 10)
}

// 秒级时间戳(按北京时间显示) → 'YYYY-MM-DD HH:MM'；非时间戳原样返回
export function fmtTsTime(ts) {
  if (!ts) return '-'
  if (typeof ts === 'number' && ts > 1000000000) {
    const d = new Date((ts + 8 * 3600) * 1000)
    return d.toISOString().replace('T', ' ').slice(0, 16)
  }
  return String(ts)
}

// 秒级时间戳(按北京时间显示) → 'YYYY-MM-DD'
export function fmtBjDay(ts) {
  if (!ts) return '-'
  const d = new Date((Number(ts) + 8 * 3600) * 1000)
  return d.toISOString().slice(0, 10)
}

// 盘中时段: 工作日 9:30-15:00 (现涨/实时涨幅自动刷新窗口)
export function isIntradayNow() {
  const bj = bjNow()
  const day = bj.getDay()
  if (day === 0 || day === 6) return false   // 周末
  const mins = bj.getHours() * 60 + bj.getMinutes()
  return mins >= 9 * 60 + 30 && mins < 15 * 60   // 9:30-15:00
}

// 会员专属时段: 工作日 9:15-15:00 (竞价 9:15-9:30 + 盘中 9:30-15:00)
// 其他时段 (盘前 / 收盘后 / 周末) 允许所有人查看(读历史快照)
export function isMemberOnlyTime() {
  const bj = bjNow()
  const day = bj.getDay()
  if (day === 0 || day === 6) return false   // 周末
  const mins = bj.getHours() * 60 + bj.getMinutes()
  return mins >= 9 * 60 + 15 && mins < 15 * 60   // 9:15-15:00
}

/* =========================================================
   选股闸门 v4 (2026-09-18 主人拍板: 只认当日 9:25 定格)
   ---------------------------------------------------------
   与后端 backend/app/services/picker/mode.py 的 is_pick_open **同口径**
   (常量与文案必须逐字一致, 有后端单测 test_pick_block_msg_shared_with_frontend 把关)。

   演进: v4.11.22「9:00-9:26 整段禁选」→ 误伤 9:15-9:25 且掐死前端自动加载 → 回退;
        v4.11.27「只挡 9:00-9:15 与 9:25:00-9:25:35」→ 竞价段放行;
        v4.11.29(本版)主人拍板:**选股本就是竞价结束后才选, 竞价过程数据在变,
        选出来的股没意义** ⇒ 只挡一段。

   本版**只挡一段**:
     [09:15:00, 09:26:30]  竞价进行中 + 当日 9_25 定格尚未落库。
       此时取数会回退**上一交易日** 9:25(竞涨幅/竞价额整批错位, 竞涨幅占评分权重 34%),
       且竞价过程数据本身每 10 秒在变 —— 双重不可信 ⇒ 不出名单。
     · 00:00-09:14:59 **盘前放行**(用上交易日定格是设计内功能) → 由顶栏标注来源日期明示;
     · ≥09:26:31 时间维放行, 但还须叠加**快照维**(当日 9_25 已落库);
     · 非交易日(周末)不拦 —— 回放最近交易日定格是既有功能(同样标注)。
   秒级粒度: 9:14:59 放行(secs=33299), 9:15:00 拦(33900); 9:26:30 拦(33990), 9:26:31 放行(33991)。

   v4.11.30(2026-09-19)主人拍板: 末端 09:25:35 → **09:25:50** —— 换猫爪源后 9:25 定格
     数据落库更晚(需等交易所撮合完成), 原放行点会取到未完成快照致名单错位。
   v4.11.45(2026-09-24)主人拍板: 末端 09:25:50 → **09:26:30** —— 定格那一枪已固定打在
     09:26:30(`auction_snapshot._BID25_FREEZE_SEC`), 放行点必须跟着定格走, 否则用户在
     09:25:51~09:26:30 打开必撞"定格尚未落库"。
   ========================================================= */
export const PICK_BLOCK_FROM = 9 * 3600 + 15 * 60         // 09:15:00 (含) 竞价开始即禁
export const PICK_BLOCK_TO = 9 * 3600 + 26 * 60 + 30      // 09:26:30 (含) 与后端定格首采时刻对齐(原 09:25:50)
// 时间维放行起点(= 拦截段结束的下一秒); 此后还须过快照维
export const PICK_OPEN = PICK_BLOCK_TO + 1                // 09:26:31
// 文案与后端 mode.PICK_BLOCK_MSG_TIME / PICK_BLOCK_MSG_SNAP 逐字一致
export const PICK_BLOCK_MSG_TIME = '竞价进行中 · 9:25 定格后开放'
export const PICK_BLOCK_MSG_SNAP = '9:25 竞价定格尚未落库 · 稍后自动恢复'

/**
 * 当前是否处于选股禁用时段(时间维)。
 * 非交易日(周末)不拦 —— 回放最近交易日定格是既有功能。
 * @param {Date} [bj] 可注入"北京时间视图"的 Date(测试用), 默认取当前
 */
export function isPickBlockedTime(bj) {
  const t = bj || bjNow()
  const day = t.getDay()
  if (day === 0 || day === 6) return false      // 周末: 不拦
  const secs = t.getHours() * 3600 + t.getMinutes() * 60 + t.getSeconds()
  return secs >= PICK_BLOCK_FROM && secs <= PICK_BLOCK_TO
}

/**
 * 闸门是否**正在生效** = 后端开关启用 且 处于禁用时段(2026-09-17 增加)。
 *
 * 为什么需要它: 前端置灰原先只看时间、不看后端开关 → 把 `settings.pick_window_guard`
 * 置 0 时只关掉了后端, 前端 9:00-9:26 依旧置灰**且连自动加载都不发请求**
 * (9/17 早盘用户 9:15-9:26 完全点不动的根因)。加上开关维后, 关开关 = 前后端同时放行。
 *
 * @param {boolean} enabled 后端开关状态(来自 /api/stocks?action=ping 的 pickGateEnabled)
 * @param {Date} [bj] 可注入的"北京时间视图" Date(测试用), 默认取当前
 * @returns {boolean} true = 当前应拦截/置灰
 */
export function isPickGateOn(enabled, bj) {
  return !!enabled && isPickBlockedTime(bj)
}

