// 异动 / 停牌风险 API —— 后端 `app/api/dev.py`（《快选股异动停牌风险功能工单》§四）
//
// 口径（★ 与工单正文相反，2026-09-27 主人已拍板取交易所口径）：
//   偏离值 = 区间首尾相减 =（期末收盘/期初前收盘 − 1）×100% −（对应指数同区间 − 1）×100%
//   三条线：3 日 ±20/30/40%、10 日 +100%、30 日 +200%（按板块，详见后端 services/dev_risk.py）
import { request } from './request'

// 单只个股全量：三条偏离线 + 明日触发空间 + 未来十日投影。
// ★ 实时重算（不读结果表）⇒ 任意代码都能出数；**算不出时仍返 200 且 ok:false**，
//   前端必须区分 ok:false（票算不出，带 reason）与请求失败（接口挂了）。
export function devRisk(code) {
  return request('/api/dev/risk', { query: { code } })
}

// 盘后批量结果（dev_risk_daily）里的「明日预警」名单。
// 默认只回 red/yellow；all=1 回全部有标签的票；board=main|gem|star|bse 过滤板块。
export function devTomorrow(query = {}) {
  return request('/api/dev/tomorrow', { query })
}

// 今日已触发（任一窗口 status=「触发」）
export function devToday(query = {}) {
  return request('/api/dev/today', { query })
}

// 体检：结果表覆盖日期/行数 + 状态分布 + 五个指数各自根数（排障用）
// ⚠️ 当前 UI **未接入**（故会被 Rollup tree-shake 掉），保留是给排障时直接用 curl 或将来做「体检」面板。
//    已接入的只有上面 risk（计算器）与 tomorrow（名单 + 选股名单徽章）两个。
export function devStatus() {
  return request('/api/dev/status')
}

// 手动触发全市场盘后扫描（管理员）。正常由 kx-worker 交易日 15:45 自动跑。
// ⚠️ 同上，当前 UI 未接入（需要管理员权限，排障时 POST 即可）。
export function devScan(date = '') {
  return request('/api/dev/scan', { method: 'POST', query: date ? { date } : {} })
}
