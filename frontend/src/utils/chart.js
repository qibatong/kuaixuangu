// 图表展示通用 helpers (抽出来便于单测, StockChartModal 组件复用)
// 所有函数均是纯函数: 数字/null/字符串输入 → 字符串输出

/** 数值格式化(2位小数, null/空 → '-') */
export function fmtNum(v) {
  return v == null || v === '' ? '-' : Number(v).toFixed(2)
}

/** 成交量额长格式: 万亿 / 亿 / 万 / 整数(四档) */
export function fmtVol(v) {
  if (v == null || v === '') return '-'
  const n = Number(v)
  if (!isFinite(n)) return '-'
  if (Math.abs(n) >= 1e12) return (n / 1e12).toFixed(2) + '万亿'
  if (Math.abs(n) >= 1e8)  return (n / 1e8).toFixed(2) + '亿'
  if (Math.abs(n) >= 1e4)  return (n / 1e4).toFixed(2) + '万'
  return n.toFixed(0)
}

/** 成交量轴标签短格式: 1位小数, 不加千位分隔, 紧凑用于 y-axis */
export function fmtVolShort(v) {
  const n = Number(v) || 0
  if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(1) + '亿'
  if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(1) + '万'
  return n.toFixed(0)
}

/** 涨跌幅带 % 号 (2位小数, 空值 → '-') */
export function fmtPct(v) {
  if (v == null || v === '' || Number.isNaN(Number(v))) return '-'
  const n = Number(v)
  return (n > 0 ? '+' : '') + n.toFixed(2) + '%'
}
