// 站内消息（2026-10-04）
// 分两类（后端 api/notices.py 同口径，改动务必两边一起看）：
//   · broadcast 站方广播：系统更新提醒 / 停服维护 —— 管理员后台发布，落 notices 表
//   · account   账户事件：会员到期 / 今日次数用尽 —— **实时推导**，不落表
//     ⇒ account 类**不支持标记已读**（它是"状态"不是"通知"），条件消失才消失。
import { request } from './request'

/** 聚合消息列表 + 未读数（红点用 unread） */
export function fetchNotices() {
  return request('/api/notices')
}

/**
 * 已读回执（只对 broadcast 生效）
 * @param {{ids?: number[], all?: boolean}} payload
 */
export function markNoticesRead(payload) {
  return request('/api/notices/read', { method: 'POST', body: payload })
}

// ---- 管理端 ----
export function adminNotices() {
  return request('/api/admin/notices')
}

export function adminPublishNotice(payload) {
  return request('/api/admin/notices', { method: 'POST', body: payload })
}

export function adminOffNotice(id) {
  return request('/api/admin/notices/off', { method: 'POST', body: { id } })
}
