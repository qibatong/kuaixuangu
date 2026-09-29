// 「主要概念」挑选（2026-09-29）
//
// 背景：竞价一进二的「概念」列直接渲染东财 f103 —— 那是一整串概念
//       （如「面板、MiniLED、华为概念、OLED、MicroLED、柔性屏、电子元件…」），
//       表格态被 CSS 限宽截成省略号、卡片态则折好几行，主人反馈「显示的概念有点多，只显示主要的就可以」。
//
// 口径（对齐全仓既有语义 + 补一层"主次"判据）：
//   ① 数量取 N=2 —— 与全仓既有「主要概念」一致：
//      concept_refresh.TRUNCATE_N = 2 / kpl.apply_board_concept_db(truncate=2) /
//      AipickReport.conceptText = concepts.slice(0, 2)。
//   ② 选哪 2 个：**按"本名单内的概念共鸣频次"降序**（同频次保持原顺序）。
//      当日的板块效应/主线，天然体现在"同一批票里有多少只带这个概念"上 ——
//      所以不需要任何外部板块热度表，零新增上游、零外部依赖、当日即刻可用。
//      （东财 f103 的顺序本身无语义，直接取前 2 个可能挑到冷门概念。）
//   ③ 以「整条概念」为单位，不做字符串截断 —— 不会把 MiniLED 切成 Mini。
//
// 注：概念全串仍原样保留在接口返回里（本函数只影响展示）。

/** 概念串 → 概念数组（东财用「、」分隔；容错半角逗号/分号） */
export function splitConcepts(concept) {
  const s = String(concept == null ? '' : concept).trim()
  if (!s || s === '-') return []
  return s.split(/[、,，;；|/]+/).map((x) => x.trim()).filter(Boolean)
}

/**
 * 从一批行里统计概念出现次数（共鸣频次）。
 * @param {Array<Object>} rows 行数组（每行含 concept 字段）
 * @returns {Map<string, number>}
 */
export function buildConceptFreq(rows) {
  const freq = new Map()
  for (const r of rows || []) {
    for (const c of splitConcepts(r && r.concept)) {
      freq.set(c, (freq.get(c) || 0) + 1)
    }
  }
  return freq
}

/**
 * 取该票的「主要概念」并拼成展示串。
 * @param {string} concept 该票概念全串（东财 f103）
 * @param {Map<string, number>} freq 本名单的概念频次（buildConceptFreq 的产物）
 * @param {number} n 取前几个（默认 2，全仓口径）
 */
export function pickMainConcept(concept, freq, n = 2) {
  const parts = splitConcepts(concept)
  if (!parts.length) return '-'
  if (parts.length <= n) return parts.join('、')
  const f = freq instanceof Map ? freq : new Map()
  const order = new Map(parts.map((x, i) => [x, i]))       // 原顺序（并列时判据）
  const sorted = parts.slice().sort((a, b) => {
    const d = (f.get(b) || 0) - (f.get(a) || 0)
    return d !== 0 ? d : (order.get(a) || 0) - (order.get(b) || 0)
  })
  return sorted.slice(0, n).join('、')
}
