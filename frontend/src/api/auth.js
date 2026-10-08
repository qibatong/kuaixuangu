// 认证相关 API
import { request } from './request'

export function login(body) {
  // body: { login: 用户名/手机号, password }
  return request('/api/login', { method: 'POST', auth: false, body })
}

// 2026-10-08 主人要求「修改密码增加手机短信验证」：
//   · 发码**不传手机号** —— 服务端按登录态取 users.phone（前端 store 只有 username，
//     不保证等于手机号），号码也不必经过浏览器；
//   · 改密必须带 code（服务端校验 scene=changepw，与发码一致）。
export function sendChangePwdSms() {
  return request('/api/change-password/send-code', { method: 'POST' })
}

export function changePassword(old_password, new_password, code) {
  return request('/api/change-password', { method: 'POST', body: { old_password, new_password, code } })
}

// 主动退出登录(2026-09-22 v4.11.35 新增): 仅清本地 token 时后端无感知,
// 登录记录里就缺「主动退出」这一半。失败不阻塞前端清理(退出一定要成功)。
export function logoutApi() {
  return request('/api/logout', { method: 'POST' }).catch(() => null)
}

/* ---------------- 注册(手机号 + 验证码, 2026-09-21 放开) ---------------- */

// 注册开关与赠送天数
export function registerConfig() {
  return request('/api/register/config', { auth: false })
}

// 发送注册验证码(手机号已注册 → 409)
export function sendRegisterSms(phone) {
  return request('/api/register/send', { method: 'POST', auth: false, body: { phone } })
}

// body: { phone, code, password, invite_code? }
export function register(body) {
  return request('/api/register', { method: 'POST', auth: false, body })
}

// 邀请码prefill: 返回邀请人打码名 + 奖励天数; 无效 → 404
export function inviteInfo(code) {
  return request('/api/invite-info', { auth: false, query: { code } })
}

/* ---------------- 找回密码(手机号) ---------------- */

export function sendForgotSms(phone) {
  return request('/api/forgot-phone/send', { method: 'POST', auth: false, body: { phone } })
}

export function resetByPhone(phone, code, password) {
  return request('/api/reset-by-phone', { method: 'POST', auth: false, body: { phone, code, new_password: password } })
}

export function forgotCheck(login) {
  return request('/api/forgot/check', { method: 'POST', auth: false, body: { login } })
}

// 自己的最近登录记录(2026-10-07 U13 设备管理的可见性)：用于自查是否有陌生登录
export function myLogins(limit = 20) {
  return request('/api/auth/logins', { query: { limit } })
}

/* ---------------- 个人资料 ---------------- */

export function ping() {
  return request('/api/stocks?action=ping')
}

export function getProfile() {
  return request('/api/profile')
}

export function updateProfile(body) {
  return request('/api/profile', { method: 'POST', body })
}
