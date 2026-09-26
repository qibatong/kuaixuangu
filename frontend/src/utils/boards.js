// 板块名归一化与「涨停数」合并（纯函数 + 单测）
// ============================================================================
// 背景：盯盘台「题材榜」要显示 题材名 | 涨停数 | 涨幅 | 主力净额。
//   涨幅/主力净额来自板块榜接口（/api/kpl/board-rank），
//   涨停数来自涨停梯队聚合（/api/kpl/zt-echelon 的 boards[].count）。
//   两者是**两个独立上游**，板块名不会逐字一致（「机器人」vs「机器人概念」、
//   「半导体」vs「半导体及元件」…）⇒ 必须归一化后模糊匹配。
//
// 🔴 匹配不上就返回 null，由上层显示「—」并说明口径 —— 绝不用 0 顶替
//   （「涨停数 0」和「没有这个板块的数据」是两件事，混起来就是静默造假）。

/** 去掉括号补充、常见后缀词与空白，得到可比较的板块主名。 */
export function normBoardName(s) {
  let x = String(s || '').trim()
  if (!x) return ''
  x = x.replace(/[（(【[].*?[)）】\]]/g, '')      // 去括号补充说明
  x = x.replace(/\s+/g, '')
  x = x.replace(/(概念股|概念|板块|指数|行业|产业|主题)$/g, '')
  x = x.replace(/[ⅠⅡⅢIVX]+$/g, '')               // 罗马数字分级后缀
  return x
}

/**
 * 把涨停数映射表合并进板块列表。
 * @param {Array} boards   板块榜 [{name, boardCode, change, mainNet, ...}]
 * @param {Array} counts   涨停梯队题材 [{name, count, maxLadder}]
 * @param {{key?:string}} [opt] key 指定输出字段名（默认 limitCount）
 * @returns {Array} 新数组（不修改入参），每项多一个 limitCount(number|null)
 */
export function mergeLimitCount(boards, counts, opt = {}) {
  const outKey = opt.key || 'limitCount'
  const exact = new Map()
  const parts = []
  for (const c of counts || []) {
    const n = normBoardName(c && c.name)
    if (!n) continue
    const v = Number(c.count)
    if (!isFinite(v)) continue
    if (!exact.has(n)) exact.set(n, { count: v, name: c.name, maxLadder: c.maxLadder })
    if (n.length >= 2) parts.push({ n, count: v })
  }
  // 长名优先，避免「半导体」抢走「半导体设备」的匹配
  parts.sort((a, b) => b.n.length - a.n.length)

  return (boards || []).map((b) => {
    const n = normBoardName(b && b.name)
    let hit = exact.get(n)
    if (!hit && n) {
      for (const p of parts) {
        if (n.includes(p.n) || p.n.includes(n)) { hit = { count: p.count }; break }
      }
    }
    return { ...b, [outKey]: hit ? hit.count : null }
  })
}

/**
 * 题材榜默认排序：涨停数降序（null 恒排最后），同数按主力净额降序。
 * —— 对应工单「默认按涨停数排」。
 */
export function sortBoardsByLimit(list) {
  return [...(list || [])].sort((a, b) => {
    const ha = a && a.limitCount !== null && a.limitCount !== undefined
    const hb = b && b.limitCount !== null && b.limitCount !== undefined
    if (ha !== hb) return ha ? -1 : 1
    if (ha && hb && a.limitCount !== b.limitCount) return b.limitCount - a.limitCount
    return (Number(b && b.mainNet) || 0) - (Number(a && a.mainNet) || 0)
  })
}
