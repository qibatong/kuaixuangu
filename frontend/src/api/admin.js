// 管理端 API (仅管理员可用)
import { request } from './request'

export function adminUsers(params = {}) {
  const q = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== '') q.set(k, v) })
  return request('/api/admin/users?' + q.toString())
}

// 邀请关系链: 被谁邀请 + 邀请了谁(含注册 IP)
export function adminUserInvites(targetUid) {
  return request(`/api/admin/user-invites?target_uid=${targetUid}`)
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

export function setUsersExpire(uids, payload) {
  // 批量设置到期(2026-08-17): {uids:[...]} + 同上 payload
  return request('/api/admin/users/expire-batch', { method: 'POST', body: { uids, ...payload } })
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
  // 管理员代编辑用户资料: fields = {phone?, email?, wx_name?, remark?, pay_remark?}
  return request('/api/admin/users/profile', { method: 'POST', body: { uid, ...fields } })
}

export function adminCreateUser(body) {
  // 管理员代创建账号: body = {username, password, phone, email, member_level?, expire_at?, invite_code?, wx_name?, remark?, pay_remark?}
  return request('/api/admin/users/create', { method: 'POST', body })
}

export function adminDeleteUser(payload) {
  // 管理员删除用户: payload = {uid} 或 {username}
  return request('/api/admin/users/delete', { method: 'POST', body: payload })
}

export function bidSnapshot(date, timePoint = '9_25', limit = 50) {
  return request(`/api/stats/bid-snapshot?date=${date}&time_point=${timePoint}&limit=${limit}`)
}

/* ================= 运营后台新增(2026-09-21 会员体系重构) ================= */

// 运营看板: 用户结构 + 14 日趋势 + 配额使用
export function adminDashboard() {
  return request('/api/admin/dashboard')
}

// 到期预警: ?days=7&tab=expiring|expired|all
export function adminExpiring(days = 7, tab = 'expiring') {
  return request(`/api/admin/expiring?days=${days}&tab=${tab}`)
}

// 风控视图: 同 IP 注册/邀请、重复领取、top 邀请人
export function adminRisk() {
  return request('/api/admin/risk')
}

// 邀请排行榜
export function adminInviteRank() {
  return request('/api/admin/invite-rank')
}

// 短信用量概览
export function adminSmsUsage() {
  return request('/api/admin/sms-usage')
}

// 审计日志(分页 + 按动作筛选)
export function adminAudit(page = 1, pageSize = 20, action = '') {
  const q = new URLSearchParams({ page, pageSize })
  if (action) q.set('action', action)
  return request('/api/admin/audit?' + q.toString())
}

export function adminAuditActions() {
  return request('/api/admin/audit/actions')
}

// 用户详情抽屉: 资料 + 邀请关系 + 签到 + 手机号领取 + 审计
export function adminUserDetail(targetUid) {
  return request(`/api/admin/user-detail?target_uid=${targetUid}`)
}

// 重置某用户配额: feature 省略则全部
export function adminResetQuota(uid, feature) {
  return request('/api/admin/users/reset-quota', { method: 'POST', body: feature ? { uid, feature } : { uid } })
}

// 批量延长到期 + 可选一并设等级
export function adminExtendPlus(uids, days, setLevel = null) {
  const body = { uids, days }
  if (setLevel !== null && setLevel !== undefined && setLevel !== '') body.set_level = setLevel
  return request('/api/admin/users/extend-plus', { method: 'POST', body })
}

// 权益配置(MEMBER_CONF_KEYS 10 项)
export function adminMemberConf() {
  return request('/api/admin/member-conf')
}

export function saveAdminMemberConf(conf) {
  return request('/api/admin/member-conf', { method: 'PUT', body: { conf } })
}

// 批量导入会员: csv 文本, 每行 "用户名,手机号,天数[,邀请码]"
export function adminImportUsers(csv, defaultPassword = '') {
  return request('/api/admin/users/import', { method: 'POST', body: { csv, default_password: defaultPassword } })
}

// 导出用户 CSV(浏览器直接下载, 不走 request 封装)
export function adminUsersExportUrl() {
  return '/api/admin/users/export'
}

/* ============ 用户行为记录(2026-09-22 v4.11.35) ============ */

// 某用户的登录记录 + 功能使用记录(用户详情抽屉)
export function adminUserActivity(targetUid, days = 30) {
  return request(`/api/admin/user-activity?target_uid=${targetUid}&days=${days}`)
}

// 全站登录流水: {days, result, kw, limit, offset}
export function adminLoginLog(params = {}) {
  const q = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') q.set(k, v) })
  return request('/api/admin/login-log?' + q.toString())
}

// 某日功能使用排行: {date, feature, limit}
export function adminUsageRank(params = {}) {
  const q = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') q.set(k, v) })
  return request('/api/admin/usage-rank?' + q.toString())
}

// 活跃趋势: 按日去重用户数 / 操作次数 / 登录成功数
export function adminActiveUsers(days = 30) {
  return request(`/api/admin/active-users?days=${days}`)
}
