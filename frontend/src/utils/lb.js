// 连板高度标签(P0, 2026-09-23 主人拍板)—— **纯展示层**的映射与文案。
//
// 数据来源: 后端名单行下发的 `lb` 字段 = 该股**买入前一日**的真实连板数
//   (0 = 前一日未涨停, 1 = 首板, 2 = 二板, ... 5 = 五板及以上); `lbDate` = 该连板数取自哪天涨停池。
//   后端口径见 backend/app/api/stocks.py 的 _fill_lb()。
//
// 🔴 三条边界(报告 §6「反向警示」, 由 lb.test.js 断言守住):
//   ① **不参与筛选** —— 它描述"昨天有多强", 而这份名单的成交价是"今天开盘";
//   ② **不参与排序** —— 保持名单原有评分序, 标签只是附加信息;
//   ③ **不给方向结论** —— 只给同档历史数字与样本量。档位实测**非单调**
//      (昨4板持有5日 +10.87% / 昨5板+ −6.17%), 写"越高越好"就是错的。

import { pct } from './format.js'
import { LB_STATS, LB_OVERALL, LB_WINDOW, LB_BASE_WINDOW, lbStatOf } from './lbStats.js'

export { LB_STATS, LB_OVERALL, LB_WINDOW, LB_BASE_WINDOW, lbStatOf }

// 6 档, 与回测分档 1:1 对应。
// 🔴 不要把「昨4板」与「昨5板+」合并成一档: 两档持有 5 日实测 +10.87% vs −6.17%,
//    合并后悬停提示里的数字会变成没有意义的平均值(2026-09-23 定稿时改的)。
export const LB_LEVELS = ['新启动', '昨首板', '昨2板', '昨3板', '昨4板', '昨5板+']

// 每档在提示里的「大白话」释义
const LB_PLAIN = ['买入前一日未涨停', '买入前一日为首板', '买入前一日为 2 连板',
  '买入前一日为 3 连板', '买入前一日为 4 连板', '买入前一日为 5 连板及以上']

// lb(原始连板数) → 档位 0~5; 未知(null/undefined/空串/非数字/负数) → null(不打标)
export function lbLevel(lb) {
  if (lb === null || lb === undefined || lb === '' || isNaN(lb)) return null
  const n = Math.trunc(Number(lb))
  if (!isFinite(n) || n < 0) return null
  return n >= 5 ? 5 : n
}

// 档位 → 胶囊文案; 未知 → ''(调用方据此不渲染, 而不是渲染成「新启动」——
// 把「没取到」显示成「新启动」等于把故障伪装成结论)
//
// 🔴 2026-09-28 主人拍板：**0 档（买入前一日未涨停）也不渲染标签** —— 名单里绝大多数票都是
//    这一档，「新启动」只会把版面搞脏、不提供信息。故 0 档与"未知"一样返回 ''。
//    （`LB_LEVELS[0]` 的文案与 `LB_STATS` 的第 0 档**照旧保留** —— 它们是口径文案/统计数据的
//      权威副本，`lbTip`/`lbStatOf` 仍按老口径工作；本次只改"要不要显示胶囊"。）
export function lbLabel(lb) {
  const lvl = lbLevel(lb)
  return lvl === null || lvl === 0 ? '' : LB_LEVELS[lvl]
}

// 档位 → 配色类名(红梯度沿用 A 股「红=强」惯例; 与「异动」橙标签语义可分)
//
// ⚠️ 类名刻意叫 `lb-tag` 而不是 `lb-badge`: AuctionView / MarketView / YidongView 各有一份
//    **同名** `.lb-badge`(含义是「N板」橙标签, 均为 scoped)。虽然 scoped 不会串样式,
//    但同名 5 处会让将来改配色的人找到错的目标 —— 故本处另起一名。
export function lbClass(lb) {
  const lvl = lbLevel(lb)
  return lvl === null ? '' : 'lb-tag-lv' + lvl
}

// 悬停提示: 该档历史统计(只给数字与样本量, 不给方向结论)
export function lbTip(lb, lbDate) {
  const lvl = lbLevel(lb)
  if (lvl === null) return ''
  const lines = ['买入前一日连板高度：' + LB_LEVELS[lvl] + '（' + LB_PLAIN[lvl] + '）']
  if (lbDate) lines.push('连板数取自 ' + lbDate + ' 涨停池')
  const s = lbStatOf(lvl)
  if (s) {
    lines.push('')
    lines.push('同档历史（样本 n=' + s.n + '，该档出票 ' + s.all + ' 只）')
    lines.push('  持有 1 日均值  ' + pct(s.h1))
    lines.push('  持有 5 日均值  ' + pct(s.h5))
    lines.push('  当日涨停率    ' + Number(s.zt).toFixed(1) + '%')
    if (s.n < 10) lines.push('  ⚠ 样本偏少（n<10），仅作方向参考')
    lines.push('')
    lines.push('样本区间 ' + LB_WINDOW + '；口径 = 买入日开盘价 → 第 N 日收盘价。')
    lines.push('历史统计不构成收益承诺。')
  }
  return lines.join('\n')
}

// 口径条文案(名单上方那一行): 只讲「这份名单整体是什么口径」, 不给个股方向
export function overallText() {
  return '本名单为竞价接力口径：历史持有 1 日均值 ' + pct(LB_OVERALL.h1)
    + '（胜率 ' + LB_OVERALL.w1 + '%）、持有 3 日 ' + pct(LB_OVERALL.h3)
    + '、持有 5 日 ' + pct(LB_OVERALL.h5) + '（胜率 ' + LB_OVERALL.w5 + '%）。'
    + '名称下方的连板标签表示该股「买入前一日」的连板高度。'
}

// 口径条脚注(样本量与免责)
export function overallFoot() {
  return '样本 ' + LB_OVERALL.n + ' 条 / ' + LB_OVERALL.days + ' 个交易日（' + LB_WINDOW
    + '），随样本积累更新；历史统计不构成收益承诺。'
}

// 整列的档位计数(供"本页 N 只"小结: 例如「昨4板 1 只」)
export function countByLevel(list) {
  const out = {}
  ;(list || []).forEach((it) => {
    const lvl = lbLevel(it && it.lb)
    if (lvl === null) return
    out[lvl] = (out[lvl] || 0) + 1
  })
  return out
}

// 金额/涨跌配色类: 复用全局 .up / .down(红涨绿跌), 0 与缺失不配色
export function dirClass(v) {
  if (v === null || v === undefined || isNaN(v) || Number(v) === 0) return ''
  return Number(v) > 0 ? 'up' : 'down'
}
