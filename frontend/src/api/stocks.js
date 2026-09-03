// 选股相关 API
import { request } from './request'

export function fetchStocks(action, filterParams, mode = 'auction', force = false) {
  // force=true: 主动重锁(绕过当日幂等, 9:25 后同参自动 lock 会直读当日批次)
  return request('/api/stocks', {
    query: { action, mode, ...(force ? { force: 1 } : {}), ...filterParams }
  })
}

export function stockChart(code, period = 'day') {
  return request('/api/stock/chart', { query: { code, period } })
}

export function getPrefs() {
  return request('/api/prefs')
}

export function getDefaultFilters() {
  // 全局默认筛选参数(管理员后台可调), 未自定义偏好的用户使用
  return request('/api/prefs/defaults')
}

export function savePrefs(settings) {
  return request('/api/prefs', { method: 'POST', body: { settings } })
}
