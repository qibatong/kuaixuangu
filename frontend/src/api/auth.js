// 认证相关 API
import { request } from './request'

export function login(body) {
  // body: { login: 用户名/手机号/邮箱, password }
  return request('/api/login', { method: 'POST', auth: false, body })
}

export function register({ username, password, invite_code, phone, email }) {
  return request('/api/register', { method: 'POST', auth: false, body: { username, password, invite_code, phone, email } })
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
