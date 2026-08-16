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

export function getAdminDefaults() {
  return request('/api/admin/defaults')
}

export function saveAdminDefaults(defaults, force = false) {
  // force=true: 保存后强制清除所有用户筛选偏好, 全量立即生效(保留主题设置)
  return request('/api/admin/defaults', { method: 'PUT', body: { defaults, force } })
}

export function setUserExpire(uid, payload) {
  // payload: {duration:'week'|'month'|'quarter'|'year'} | {days:N} | {expire_at:'YYYY-MM-DD'}
  return request('/api/admin/users/expire', { method: 'POST', body: { uid, ...payload } })
}

export function resetUserPassword(uid, password) {
  // 管理员重置用户密码: uid + 新密码
  return request('/api/admin/users/reset-password', { method: 'POST', body: { uid, password } })
}

export function adminSetMemberLevel(uid, level) {
  // 设置会员等级: level 0=免费试用 1=付费会员 2=VIP老师
  return request('/api/admin/users/member-level', { method: 'POST', body: { uid, level } })
}

export function adminSetUserProfile(uid, fields) {
  // 管理员代编辑用户资料: fields = {phone?, email?, wx_name?, remark?}
  return request('/api/admin/users/profile', { method: 'POST', body: { uid, ...fields } })
}

export function bidSnapshot(date, timePoint = '9_25', limit = 50) {
  return request(`/api/stats/bid-snapshot?date=${date}&time_point=${timePoint}&limit=${limit}`)
}
