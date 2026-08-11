// 选股相关 API
import { request } from './request'

export function fetchStocks(action, filterParams, mode = 'auction') {
  return request('/api/stocks', { query: { action, mode, ...filterParams } })
}

export function getPrefs() {
  return request('/api/prefs')
}

export function savePrefs(settings) {
  return request('/api/prefs', { method: 'POST', body: { settings } })
}
