// 金额/数值格式化工具(纯函数, 供各视图复用 + 单测)
// yi: 元 → 亿(2位)
export function yi(v) {
  if (v === null || v === undefined || isNaN(v)) return '-'
  return (v / 1e8).toFixed(2)
}

// signed: 带符号数值(2位小数), 正数加 +; 缺失/非法返回 '—' (防 NaN%)
// 2026-09-11 P0-3: 统一用 '—' 表示"未测到"(落库未知被读侧还原成 null),
// 与"-"(不适用: 无极弱档/无概念)区分开, 避免未知继续显示成 0.00。
export function signed(v) {
  if (v === null || v === undefined || v === '' || isNaN(v)) return '—'
  return (v > 0 ? '+' : '') + Number(v).toFixed(2)
}

// amtText: 金额自适应显示(>=1亿 显示亿, 否则显示万)
export function amtText(v) {
  if (!v || v <= 0) return '-'
  return v >= 1e8 ? (v / 1e8).toFixed(2) + '亿' : (v / 1e4).toFixed(0) + '万'
}

// sealDailyText: 连续多日封单视图专用 —— ≥1亿 显示「80.8亿」(整数亿去掉尾随 .0, 如 7.7e9→77亿),
// 否则显示「9116万」(取整), 0/空 → '-'(与模板逐位对齐: 80.8亿 / 77亿 / 9116万 / -)
export function sealDailyText(v) {
  const n = Number(v) || 0
  if (!n) return '-'
  if (n >= 1e8) {
    const s = (n / 1e8).toFixed(1)
    return (s.endsWith('.0') ? s.slice(0, -2) : s) + '亿'
  }
  return Math.round(n / 1e4) + '万'
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

// pct: 百分比拼接(带符号 2 位); 缺失 → '—'(同 signed, P0-3)
export function pct(v) {
  if (v === null || v === undefined || v === '' || isNaN(v)) return '—'
  return (v > 0 ? '+' : '') + Number(v).toFixed(2) + '%'
}

// fmtNum: 定点小数 + 可选后缀; 缺失/非法 → '—'
// 2026-09-11 P0-3: 落库时"未知"被 NOT NULL 约束兜成 0, 读侧按 miss_fields 还原成 null
// (开关 history_null_restore)。前端必须把 null 显示成「—」而不是 0.00/0分 —— 否则
// "没测到"继续伪装成"实测 0", 与落库前的语义脱节。
// 2026-09-30 v4.11.84 (P1-2 数字格式统一): **|v| ≥ 1000(4 位整数起)** 才上千分位。
//   🔴 与体检报告建议(≥10000)有出入, 理由: 报告给的痛例本身就是 4 位整数 —— 竞价额
//      "6168.86万"、市值 "1234.5亿" 正落在 1000~9999 这一档, 按 ≥10000 它们**一个都不会**
//      加上分隔符(实测确认), 等于没修; 而 3 位以下(评分 94分 / 涨幅 10.03%)本来就不需要逗号,
//      所以取 1000 既不增噪又真正解决"位数靠数"。Intl 实例按小数位缓存。
const _NF_CACHE = new Map()
function _nf(digits) {
  let f = _NF_CACHE.get(digits)
  if (!f) {
    f = new Intl.NumberFormat('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
    _NF_CACHE.set(digits, f)
  }
  return f
}
export function fmtNum(v, digits = 1, suffix = '') {
  if (v === null || v === undefined || v === '' || isNaN(v)) return '—'
  const n = Number(v)
  return (Math.abs(n) >= 1000 ? _nf(digits).format(n) : n.toFixed(digits)) + suffix
}

/**
 * 首封时间(HH:MM)。
 * 📌 状态(2026-10-04 主人决定): 「昨涨停」表的「首封」列**已撤下**, 本函数**当前无引用**
 *    (仅单测在用), 保留是因为它是纯函数零成本, 将来若要加回只需模板加 2 行。
 * 入参是**秒级 Unix 时间戳**(开盘啦 flash 涨停池 first_limit_up, 后端透传为 firstLimitUp),
 * 按**北京时间**取 HH:MM。0 / 空 / 非数字 → '-'。
 *
 * 🔴 必须显式 +8 小时再用 getUTC*: 东八区时间戳直接 toLocaleTimeString 会随**运行环境时区**漂移,
 *    在 UTC 机器上(服务器/CI)会显示成 01:30 而不是 09:30。
 */
export function firstSealText(ts) {
  const n = Number(ts)
  if (!Number.isFinite(n) || n <= 0) return '-'
  const d = new Date(n * 1000 + 8 * 3600 * 1000)
  return String(d.getUTCHours()).padStart(2, '0') + ':' + String(d.getUTCMinutes()).padStart(2, '0')
}
