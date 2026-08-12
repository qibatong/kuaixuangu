// 选股相关 API
import { request } from './request'

export function fetchStocks(action, filterParams, mode = 'auction') {
  return request('/api/stocks', { query: { action, mode, ...filterParams } })
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
