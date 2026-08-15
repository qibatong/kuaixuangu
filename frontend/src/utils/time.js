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

// 会员专属时段: 工作日 9:15-15:00 (竞价 9:15-9:30 + 盘中 9:30-15:00)
// 其他时段 (盘前 / 收盘后 / 周末) 允许所有人查看(读历史快照)
export function isMemberOnlyTime() {
  const bj = bjNow()
  const day = bj.getDay()
  if (day === 0 || day === 6) return false   // 周末
  const mins = bj.getHours() * 60 + bj.getMinutes()
  return mins >= 9 * 60 + 15 && mins < 15 * 60   // 9:15-15:00
}
