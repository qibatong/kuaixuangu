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
