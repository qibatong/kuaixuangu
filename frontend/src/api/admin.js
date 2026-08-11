// 管理端 API (仅管理员可用)
import { request } from './request'

export function adminUsers(params = {}) {
  const q = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== '') q.set(k, v) })
  return request('/api/admin/users?' + q.toString())
}

export function adminScoring(mode = 'auction') {
  return request(`/api/admin/scoring?mode=${mode}`)
}

export function saveScoring(scoring, mode = 'auction') {
  return request(`/api/admin/scoring?mode=${mode}`, { method: 'PUT', body: { scoring } })
}

export function setUserExpire(uid, payload) {
  // payload: {duration:'week'|'month'|'quarter'|'year'} | {days:N} | {expire_at:'YYYY-MM-DD'}
  return request('/api/admin/users/expire', { method: 'POST', body: { uid, ...payload } })
}

export function resetUserPassword(uid, password) {
  // 管理员重置用户密码: uid + 新密码
  return request('/api/admin/users/reset-password', { method: 'POST', body: { uid, password } })
}

export function bidSnapshot(date, timePoint = '9_25', limit = 50) {
  return request(`/api/stats/bid-snapshot?date=${date}&time_point=${timePoint}&limit=${limit}`)
}
