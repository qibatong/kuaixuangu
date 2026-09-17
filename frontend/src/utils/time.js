// 北京时间工具
export function bjNow() {
  const now = new Date()
  return new Date(now.getTime() + 8 * 60 * 60 * 1000 + now.getTimezoneOffset() * 60 * 1000)
}

export function isBefore930() {
  const bj = bjNow()
  return bj.getHours() < 9 || (bj.getHours() === 9 && bj.getMinutes() < 30)
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
   选股闸门 v3 (2026-09-17 主人拍板重做)
   ---------------------------------------------------------
   与后端 backend/app/services/picker/mode.py 的 is_pick_open **同口径**
   (常量与文案必须逐字一致, 有后端单测 test_pick_block_msg_shared_with_frontend 把关)。

   🔴 v4.11.22 旧口径是「9:00-9:26 整段禁选」, 把 **9:15-9:25 竞价主窗口**
   (主人的真实选股来源) 一起治死了 → 9/17 早盘事故 → v4.11.26 已整体回退。
   本版**只挡两段**(都是"必然给出非当日定格名单"的时段):

     ① [09:00:00, 09:15:00)  盘前 PREOPEN —— 用的是**上交易日**定格
                             (设计如此, 但用户会误认为当日);
     ② [09:25:00, 09:25:35]  竞价已定格但**当日 9_25 尚未落库**
                             (采集下限 20s + 重采窗口, 实测落库 09:25:23~09:25:32)
                             → 后端 load_snapshot_full 会静默回退昨日。

   **09:15:00-09:24:59 放开** —— 竞价数据在变但那是用户要看的实时竞价;
   非交易日(周末)不拦 —— 回放最近交易日定格是既有功能。
   ≥09:25:36 之后时间维放行, 但调用方还须叠加**快照维**(当日 9_25 已落库)。
   秒级粒度: 9:25:35 仍拦(secs=33935), 9:25:36 放行(33936)。
   ========================================================= */
export const PICK_BLOCK1_FROM = 9 * 3600                  // 09:00:00
export const PICK_BLOCK1_TO = 9 * 3600 + 15 * 60          // 09:15:00 (不含)
export const PICK_BLOCK2_FROM = 9 * 3600 + 25 * 60        // 09:25:00
export const PICK_BLOCK2_TO = 9 * 3600 + 25 * 60 + 35     // 09:25:35 (含)
// 时间维放行起点(= 第二段拦截结束的下一秒); 此后还须过快照维
export const PICK_OPEN = PICK_BLOCK2_TO + 1               // 09:25:36
// 文案与后端 mode.PICK_BLOCK_MSG_TIME / PICK_BLOCK_MSG_SNAP 逐字一致
export const PICK_BLOCK_MSG_TIME = '9:15 后开放 · 正在等待 9:25 竞价定格'
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
  if (secs >= PICK_BLOCK1_FROM && secs < PICK_BLOCK1_TO) return true
  if (secs >= PICK_BLOCK2_FROM && secs <= PICK_BLOCK2_TO) return true
  return false
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

