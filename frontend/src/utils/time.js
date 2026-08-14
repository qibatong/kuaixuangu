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

// 会员专属时段: 工作日 9:15-15:00 (竞价 9:15-9:30 + 盘中 9:30-15:00)
// 其他时段 (盘前 / 收盘后 / 周末) 允许所有人查看(读历史快照)
export function isMemberOnlyTime() {
  const bj = bjNow()
  const day = bj.getDay()
  if (day === 0 || day === 6) return false   // 周末
  const mins = bj.getHours() * 60 + bj.getMinutes()
  return mins >= 9 * 60 + 15 && mins < 15 * 60   // 9:15-15:00
}
