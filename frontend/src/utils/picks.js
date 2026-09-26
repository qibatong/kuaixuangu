// 今日票状态判定 + 战报聚合（纯函数，供盘中盯盘台「今日票战报」使用 + 单测）
// ============================================================================
// 为什么单独抽出来：判定规则要同时被「逐票状态标签」和「聚合卡（涨停X/炸板X）」使用，
// 两处各写一遍必然漂移；且它是纯函数 ⇒ 可直接单测，不需要浏览器。
//
// 🔴 关于「炸板」：真实炸板 = **当日曾触及涨停、现在不在涨停价**，判定它必须知道
//   当日最高涨幅（peakChange）。现有可用的实时行情接口（/api/quotes）只回
//   price/realChange/entityChange/volRatio/turnover，**不含最高价** ⇒ 拿不到就不判，
//   `brokenKnown=false` 让上层显示「—」而不是编一个数出来。
//    （这是本项目反复踩的「静默用默认值冒充真值」的反面做法。）

/** 各板块涨停幅度(%)。科创板/创业板注册制后 ST 同样是 20%，故不按 ST 降档。 */
export function limitPctOf(code, name = '') {
  const c = String(code || '').trim()
  const nm = String(name || '')
  if (/^(30|68)/.test(c)) return 20          // 创业板 300/301、科创板 688/689
  if (/^(4|8|92)/.test(c)) return 30         // 北交所 43/83/87/920
  if (/ST/i.test(nm)) return 5               // 主板 ST
  return 10
}

/** 封板判定容差(%)：涨停价四舍五入到分后，涨幅常显示 9.98/10.01，不能要求恰好等于 10。 */
export const LIMIT_SLACK = 0.25

function num(v) {
  if (v === null || v === undefined || v === '') return NaN
  return Number(v)
}

/**
 * 逐票状态。
 * @param {{code:string,name?:string,change:number,peakChange?:number}} it
 * @returns {{key:'limit'|'broken'|'high'|'down'|'flat'|'unknown',label:string}}
 */
export function pickState(it) {
  if (!it) return { key: 'unknown', label: '—' }
  const c = num(it.change)
  if (!isFinite(c)) return { key: 'unknown', label: '—' }
  const limit = limitPctOf(it.code, it.name)
  if (c >= limit - LIMIT_SLACK) return { key: 'limit', label: '封板' }
  const peak = num(it.peakChange)
  if (isFinite(peak) && peak >= limit - LIMIT_SLACK) return { key: 'broken', label: '炸板' }
  if (c >= 3) return { key: 'high', label: '冲高' }
  if (c < 0) return { key: 'down', label: '翻绿' }
  return { key: 'flat', label: '微涨' }
}

/**
 * 战报聚合（剔除无实时涨幅的票，避免把 null 当 0 计入平均）。
 * brokenKnown=false 表示数据源不给最高价 ⇒ 炸板数不可知，上层应显示「—」。
 */
export function summarizePicks(list) {
  const arr = []
  let brokenKnown = false
  for (const it of list || []) {
    if (!it) continue
    const c = num(it.change)
    if (!isFinite(c)) continue
    arr.push(it)
    const peak = num(it.peakChange)
    if (isFinite(peak)) brokenKnown = true
  }
  const out = {
    count: arr.length,
    limitCount: 0,
    brokenCount: 0,
    brokenKnown,
    upCount: 0,
    downCount: 0,
    avgChange: null,
  }
  if (!arr.length) return out
  let sum = 0
  for (const it of arr) {
    const c = num(it.change)
    sum += c
    const st = pickState(it)
    if (st.key === 'limit') out.limitCount++
    else if (st.key === 'broken') out.brokenCount++
    if (c > 0) out.upCount++
    else if (c < 0) out.downCount++
  }
  out.avgChange = sum / arr.length
  return out
}

/** 按状态优先级排序：封板/炸板最前，翻绿最后（工单「二期体验 2」，顺手做掉）。 */
const STATE_ORDER = { limit: 0, broken: 1, high: 2, flat: 3, down: 4, unknown: 5 }
export function sortByState(list) {
  return [...(list || [])].sort((a, b) => {
    const oa = STATE_ORDER[pickState(a).key] ?? 9
    const ob = STATE_ORDER[pickState(b).key] ?? 9
    if (oa !== ob) return oa - ob
    return (num(b.change) || 0) - (num(a.change) || 0)
  })
}

/**
 * 求均值，**自动剔除缺失值**，一个有效样本都没有时返回 null。
 * 🔴 关键：不做「空数组 → 0」的兜底 —— 无样本和「平均涨幅 0%」是两件完全不同的事，
 *    兜成 0 就是本项目反复出现的「静默用默认值冒充真值」。
 */
export function avgOf(values) {
  const arr = []
  for (const v of values || []) {
    const n = num(v)
    if (isFinite(n)) arr.push(n)
  }
  if (!arr.length) return null
  return arr.reduce((a, b) => a + b, 0) / arr.length
}
