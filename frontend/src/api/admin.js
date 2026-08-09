// 管理端 API (仅管理员可用)
import { request } from './request'

export function adminUsers(params = {}) {
  const q = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== '') q.set(k, v) })
  return request('/api/admin/users?' + q.toString())
}

export function adminScoring() {
  return request('/api/admin/scoring')
}

export function saveScoring(scoring) {
  return request('/api/admin/scoring', { method: 'PUT', body: { scoring } })
}
