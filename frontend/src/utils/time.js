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
