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
