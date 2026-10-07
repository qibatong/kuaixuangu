// 站内消息（2026-10-04 建立 / 2026-10-06 改到消息中心）
//
// 🔴 2026-10-06 起：所有「按条操作」一律传 **nkey**，不要再传数字 id。
//    原因：notices.id 是普通 INTEGER PRIMARY KEY（无 AUTOINCREMENT），公告被删后
//    id 会被新公告复用，notice_reads 里的旧回执会错挂到新公告上（2026-10-04 生产事故：
//    用户拿到全新公告却被判成"已读"，红点永远不亮）。id 仍在返回体里（nid），仅作展示/兼容。
//
// 三分类（主人 2026-10-05 拍板精简）：system 系统 / account 账户会员 / trade 交易时点
//   · system  系统公告、运营活动、邀请成功、签到、免费次数用尽
//   · account 会员到期与续费（落表，但带 expire_at 快照自愈：续费后自动消失）
//   · trade   竞价开始 / 名单就绪 / 尾盘抢筹 —— 只在交易日的固定时点出现
import { request } from './request'

/** 聚合消息列表 + 未读数（红点用 unread） */
export function fetchNotices() {
  return request('/api/notices')
}

/**
 * 已读回执
 * @param {{nkeys?: string[], all?: boolean}} payload
 * ⚠️ 旧字段 ids 后端仍兼容（会在库里换成 nkey），但新代码不要再用。
 */
export function markNoticesRead(payload) {
  return request('/api/notices/read', { method: 'POST', body: payload })
}

/** 单条删除：只对自己隐藏，不删公告本体（别人还得看） */
export function deleteNotices(nkeys) {
  return request('/api/notices/delete', { method: 'POST', body: { nkeys } })
}

/** 消息内行动按钮点击上报（触达漏斗的 click 一环） */
export function trackNoticeClick(nkey) {
  return request('/api/notices/click', { method: 'POST', body: { nkey } })
}

/** 推送偏好（读取） */
export function fetchNoticePrefs() {
  return request('/api/notices/prefs')
}

/**
 * 推送偏好（保存，局部更新）
 * 🔴 只影响"推不推送"，**站内消息始终可见可查** —— 隐藏站内消息会让用户
 *    找不到上次那条到期提醒；真正打扰人的是推送。
 */
export function saveNoticePrefs(patch) {
  return request('/api/notices/prefs', { method: 'POST', body: patch })
}

// ---- 管理端 ----
export function adminNotices() {
  return request('/api/admin/notices')
}

export function adminPublishNotice(payload) {
  return request('/api/admin/notices', { method: 'POST', body: payload })
}

/** A6/M7 消息模板库(2026-10-07)：运营反复发的那几类消息不必每次重敲 */
export function adminTemplates() {
  return request('/api/admin/notice-templates')
}
export function adminSaveTemplate(payload) {
  return request('/api/admin/notice-templates', { method: 'POST', body: payload })
}
export function adminDeleteTemplate(id) {
  return request(`/api/admin/notice-templates/${id}`, { method: 'DELETE' })
}

/** 编辑公告（已投递的只允许改正文/有效期/行动按钮） */
export function adminEditNotice(payload) {
  return request('/api/admin/notices/edit', { method: 'POST', body: payload })
}

/** 撤回：优先传 nkey，id 仅兼容 */
export function adminOffNotice(payload) {
  return request('/api/admin/notices/off', { method: 'POST', body: payload })
}

/**
 * 发布前人数预估：{total, pushable}（防误发全量）
 * target='tag' 时必须同时给 tag 名，否则预估的是全员。
 */
export function adminNoticeCount(target, tag = '') {
  let url = '/api/admin/notices/count?target=' + encodeURIComponent(target || 'all')
  if (tag) url += '&tag=' + encodeURIComponent(tag)
  return request(url)
}

/** 到期预警「一键提醒」：{uids:[...]} */
export function adminExpireRemind(uids) {
  return request('/api/admin/expire-remind', { method: 'POST', body: { uids } })
}
