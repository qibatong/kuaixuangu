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
   选股闸门 (2026-09-16 主人拍板: 开盘日 9:00-9:26 不支持选股)
   ---------------------------------------------------------
   与后端 backend/app/services/picker/mode.py 的 is_pick_open **同口径**
   (常量与文案必须逐字一致, 有后端单测 test_pick_block_msg_shared_with_frontend 把关)。

   为什么禁: 9:00-9:15 是上交易日定格; 9:15-9:25 竞价数据在变;
   9:25-9:26 当日定格尚未落库(采集下限 20s, 实测落库 09:25:23~09:25:32)
   → 后端 load_snapshot_full 会静默回退昨日, 用户拿到的是昨天的名单。
   ========================================================= */
export const PICK_BLOCK_FROM = 9 * 60        // 9:00
export const PICK_OPEN = 9 * 60 + 26         // 9:26
// 文案与后端 mode.PICK_BLOCK_MSG_TIME / PICK_BLOCK_MSG_SNAP 逐字一致
export const PICK_BLOCK_MSG_TIME = '9:26 后开放 · 正在等待 9:25 竞价定格'
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
  const mins = t.getHours() * 60 + t.getMinutes()
  return mins >= PICK_BLOCK_FROM && mins < PICK_OPEN
}

