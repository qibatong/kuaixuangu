// 股性功能 API: 排行 + 个股画像
import { request } from './request'

// 股性排行(综合分降序), 支持分页/最少涨停数过滤
export function stockTemperRank(page = 1, size = 50, minZt = 0) {
  return request('/api/stock-temper/rank', { query: { page, size, min_zt: minZt } })
}

// 单只股票股性画像; refresh=1 强制刷新日K缓存
export function stockTemperProfile(code, refresh = 0) {
  return request(`/api/stock-temper/${code}`, { query: { refresh } })
}