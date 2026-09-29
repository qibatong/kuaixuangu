// 「主要概念」挑选 + 非概念标签过滤（2026-09-29）
//
// 背景：竞价一进二的「概念」列直接渲染东财 f103 —— 那是**概念 + 交易属性/状态标签混在一起**的一整串。
//   生产实测(2026-09-29 名单 4 只) 原始串示例：
//     002912 中新赛克 | 互联网服务,深圳特区,机构重仓,融资融券,大数据,网络安全,安防概念,央国企改革,
//                       国产软件,人工智能,昨日涨停,工业互联网,富时罗素,标准普尔,车联网(车路云),
//                       职业教育,数据安全,昨日涨停_含一字,算力概念,数据要素,低空经济,DeepSeek概念,
//                       AI应用,东方财富热股,最近多板,趋势股,题材股
//   主人：「一进二显示的概念有点多，只显示主要的就可以」+「昨日一字涨停这种不是概念吧」。
//
// 口径：
//   ① 先过滤 **非概念**（NON_THEME_PATTERNS）：昨日涨停/涨停/连板/首板 等状态、融资融券/深股通/
//      指数成分 等属性、机构重仓/小盘股/趋势股/题材股 等风格、预增预减/高送转 等财报事件。
//      ⚠️ 过滤必须发生在**挑之前**：否则这些标签会挤占名额, 还会把频次统计带偏。
//   ② 再取 N 个（默认 **N=3**，主人 2026-09-29 定）；全仓既有「主要概念」口径是 N=2
//      （concept_refresh.TRUNCATE_N=2 / kpl.apply_board_concept_db(truncate=2)），本页按主人要求取 3。
//   ③ 选哪 3 个：**按本名单内的概念共鸣频次降序**（同频次保持原顺序）—— 当日板块效应/主线天然
//      体现在"同一批票里有多少只带这个概念"上 ⇒ 零新增上游、零外部依赖、当日即刻可用。
//      （东财 f103 顺序本身无语义, 直接取前 N 个可能挑到冷门概念。）
//   ④ 以「整条概念」为单位, 不做字符串截断 —— 不会把 MiniLED 切成 Mini。
//   ⑤ 兜底：若整串**全是被过滤的属性标签**（例如只有「次新股、融资融券」），退回原始串取前 N 个,
//      宁可显示属性标签, 也不显示空白。
//
// 注：概念全串仍原样保留在接口返回里（本模块只影响展示）。

/**
 * 非概念标签（东财 f103 里混进来的交易属性/状态）。命中即过滤。
 * 采用「包含」匹配 —— 覆盖 `昨日涨停_含一字`、`2026中报预减` 这类带前后缀的变体。
 */
export const NON_THEME_PATTERNS = [
  // 涨停/封板类状态
  '昨日', '涨停', '连板', '首板', '触板', '炸板', '一字', '高振幅', '多板', '开板',
  // 通道 / 指数成分 / 标的属性
  '融资融券', '融券', '深股通', '沪股通', '港股通', '陆股通', '富时罗素', '标准普尔', '标普',
  'MSCI', '中证500', '中证1000', '沪深300', '上证50', '上证380', '创业板50', '科创50',
  '指数', '可转债', '转债', '含B股', 'AH股', 'B股', 'H股',
  // 平台 / 风格标签
  '东方财富热股', '热股', '趋势股', '题材股', '小盘股', '小盘成长', '大盘股', '大盘成长',
  '中盘股', '价值股', '成长股', '次新股', '新股', '低价股', '高价股', '破净', '破增发价',
  '机构重仓', '基金重仓', '社保重仓', '券商重仓', '北向资金', '重仓', 'QFII', '养老金',
  '近期新高', '近期新低', '历史新高',
  // 财报 / 事件状态
  '高送转', '送转', '预增', '预减', '扭亏', '摘帽', '退市', '风险警示', '回购', '增持', '减持',
]

/** 概念串 → 概念数组（东财用半角逗号分隔；容错顿号/全角逗号/分号/竖线/斜杠） */
export function splitConcepts(concept) {
  const s = String(concept == null ? '' : concept).trim()
  if (!s || s === '-') return []
  return s.split(/[、,，;；|/]+/).map((x) => x.trim()).filter(Boolean)
}

/** 是否算「概念/题材」（非概念 = 状态/属性/风格/财报事件等标签） */
export function isThemeConcept(name) {
  const s = String(name == null ? '' : name).trim()
  if (!s) return false
  return !NON_THEME_PATTERNS.some((p) => s.includes(p))
}

/** 只保留概念/题材（过滤掉非概念标签）；全部被过滤时返回空数组 */
export function themeConcepts(concept) {
  return splitConcepts(concept).filter(isThemeConcept)
}

/**
 * 从一批行里统计**题材**出现次数（共鸣频次，已排除非概念标签）。
 * @param {Array<Object>} rows 行数组（每行含 concept 字段）
 */
export function buildConceptFreq(rows) {
  const freq = new Map()
  for (const r of rows || []) {
    for (const c of themeConcepts(r && r.concept)) {
      freq.set(c, (freq.get(c) || 0) + 1)
    }
  }
  return freq
}

/**
 * 取该票的「主要概念」并拼成展示串。
 * @param {string} concept 该票概念全串（东财 f103）
 * @param {Map<string, number>} freq 本名单的题材频次（buildConceptFreq 的产物）
 * @param {number} n 取前几个（默认 3，主人 2026-09-29 定）
 */
export function pickMainConcept(concept, freq, n = 3) {
  const raw = splitConcepts(concept)
  if (!raw.length) return '-'
  let parts = raw.filter(isThemeConcept)
  if (!parts.length) parts = raw          // 兜底: 全是属性标签时, 宁可显示也不留空
  if (parts.length <= n) return parts.join('、')
  const f = freq instanceof Map ? freq : new Map()
  const order = new Map(parts.map((x, i) => [x, i]))       // 原顺序（并列时判据）
  const sorted = parts.slice().sort((a, b) => {
    const d = (f.get(b) || 0) - (f.get(a) || 0)
    return d !== 0 ? d : (order.get(a) || 0) - (order.get(b) || 0)
  })
  return sorted.slice(0, n).join('、')
}
