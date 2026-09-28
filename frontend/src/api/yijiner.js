// 竞价一进二(2026-09-28 新增)
// =============================================================================
// 语义: 昨日主板首板 → 今日竞价阶段评估二连板潜力, 后端已按综合评分降序返回。
// 门禁: **严格 VIP/付费**(与「竞价异动」同强度) —— 免费试用 403 code=vip_required,
//       request.js 会把 403 的 detail 展开到 Error 上(含 code), 调用方据此弹 VipGate。
// 取数: 后端 /api/yijiner 内部复用 fetcher 涨停池 + 东财点查, **浏览器不直连东财**。
//
// ⚠️ 不加 cache: 该名单按"此刻"的行情与竞价数据算出, 缓存会给出过期名单。
//    (与竞价异动那些带 cache 的只读接口不同 —— 它们是分钟级稳定的聚合数据。)
import { request } from './request'

export function fetchYijiner() {
  return request('/api/yijiner')
}
