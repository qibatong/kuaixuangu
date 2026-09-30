// 图表展示通用 helpers (抽出来便于单测, StockChartModal 组件复用)
// 所有函数均是纯函数: 数字/null/字符串输入 → 字符串输出

/** 数值格式化(2位小数, null/空 → '-'; v4.11.84 P1-2: 整数部分 ≥1000 上千分位) */
export function fmtNum(v) {
  if (v == null || v === '') return '-'
  const n = Number(v)
  return Math.abs(n) >= 1000
    ? new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)
    : n.toFixed(2)
}

/** 成交量额长格式: 万亿 / 亿 / 万 / 整数(四档) */
// 2026-09-30 v4.11.84 (P1-2): 换算后整数部分 ≥1000 时上千分位(与 utils/format.js 的 fmtNum 同口径)。
//   例: 1450.2亿 → "1,450.20亿"; 412万 → "412.00万"(不加逗号, 位数好数)。
const _sep = (n, d) => (Math.abs(n) >= 1000
  ? new Intl.NumberFormat('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d }).format(n)
  : n.toFixed(d))

export function fmtVol(v) {
  if (v == null || v === '') return '-'
  const n = Number(v)
  if (!isFinite(n)) return '-'
  if (Math.abs(n) >= 1e12) return _sep(n / 1e12, 2) + '万亿'
  if (Math.abs(n) >= 1e8)  return _sep(n / 1e8, 2) + '亿'
  if (Math.abs(n) >= 1e4)  return _sep(n / 1e4, 2) + '万'
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
