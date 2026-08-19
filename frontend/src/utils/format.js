// 金额/数值格式化工具(纯函数, 供各视图复用 + 单测)
// yi: 元 → 亿(2位)
export function yi(v) {
  if (v === null || v === undefined || isNaN(v)) return '-'
  return (v / 1e8).toFixed(2)
}

// signed: 带符号数值(2位小数), 正数加 +; 缺失/非法返回 '-' (防 NaN%)
export function signed(v) {
  if (v === null || v === undefined || isNaN(v)) return '-'
  return (v > 0 ? '+' : '') + Number(v).toFixed(2)
}

// amtText: 金额自适应显示(>=1亿 显示亿, 否则显示万)
export function amtText(v) {
  if (!v || v <= 0) return '-'
  return v >= 1e8 ? (v / 1e8).toFixed(2) + '亿' : (v / 1e4).toFixed(0) + '万'
}

// fmtAvg: 涨跌幅(2位+%)带符号
export function fmtAvg(v) {
  return v === null || v === undefined ? '-' : (v > 0 ? '+' : '') + v.toFixed(2) + '%'
}

// fmtT: 秒级时间戳 → HH:MM(北京时间)
export function fmtT(ts) {
  if (!ts) return '-'
  const d = new Date((ts + 8 * 3600) * 1000)
  return d.toISOString().slice(11, 16)
}

// wan: 数值取整(万元显示)
export function wan(v) {
  return v ? Number(v).toFixed(0) : '0'
}

// pct: 百分比拼接
export function pct(v) {
  if (v === null || v === undefined) return '-'
  return (v > 0 ? '+' : '') + Number(v).toFixed(2) + '%'
}
