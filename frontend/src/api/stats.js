// 战绩分析 API
import { request } from './request'

export function fetchPerformance(query = {}) {
  return request('/api/stats/performance', { query })
}

export function auctionOverview(date = '') {
  return request('/api/stats/auction-overview', { query: date ? { date } : {} })
}

export function auctionSnapshot(date, timePoint) {
  return request('/api/stats/auction-snapshot', { query: { date, time_point: timePoint } })
}

// 个股三时点封单对比(9:15/9:20/9:25 一个视图看全)
export function bidSnapshotStock(date, code) {
  return request('/api/stats/bid-snapshot-stock', { query: { date, code } })
}

// 全市场三时点封单榜(三层排序: 9:25涨停 > 9:20涨停回落 > 9:15涨停回落)
export function bidSnapshot3points(date, limit = 100) {
  return request('/api/stats/bid-snapshot-3points', { query: { date, limit } })
}

// 竞价精选(ZH 选股): 涨停基因 × 高开≥3% × 竞价放量占昨量 5~10%
export function zhPicks(date = '') {
  return request('/api/stats/zh-picks', { query: date ? { date } : {} })
}
