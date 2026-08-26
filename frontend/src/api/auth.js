// 认证相关 API
import { request } from './request'

export function login(body) {
  // body: { login: 用户名/手机号/邮箱, password }
  return request('/api/login', { method: 'POST', auth: false, body })
}



export function changePassword(old_password, new_password) {
  return request('/api/change-password', { method: 'POST', body: { old_password, new_password } })
}

export function forgot(email) {
  return request('/api/forgot', { method: 'POST', auth: false, body: { email } })
}

export function reset(token, password) {
  return request('/api/reset', { method: 'POST', auth: false, body: { token, password } })
}

export function ping() {
  return request('/api/stocks?action=ping')
}

export function getProfile() {
  return request('/api/profile')
}

export function updateProfile(body) {
  return request('/api/profile', { method: 'POST', body })
}

export function forgotCheck(login) {
  return request('/api/forgot/check', { method: 'POST', auth: false, body: { login } })
}

// 邮箱认证(2026-08-17): 新注册强制验证
export function verifyEmail(uid, code) {
  return request('/api/verify-email', { method: 'POST', auth: false, body: { uid, code } })
}

export function resendVerify(uid) {
  return request('/api/resend-verify', { method: 'POST', auth: false, body: { uid } })
}
