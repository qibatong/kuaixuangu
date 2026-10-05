// 会员中心 API(2026-09-21 会员体系重构)
import { request } from './request'

// 会员总览: 等级/到期/配额/签到/邀请 一把返回
export function memberOverview() {
  return request('/api/member/overview')
}

// 仅配额(轮询轻量接口)
export function memberQuota() {
  return request('/api/member/quota')
}

// 签到状态 + 最近记录
export function memberCheckin() {
  return request('/api/member/checkin')
}

// 执行签到(送配额)
export function doCheckin() {
  return request('/api/member/checkin', { method: 'POST' })
}

// 权益说明: 免费/会员/VIP 三档配额
export function memberPlans() {
  return request('/api/member/plans')
}

/* ---------------- 邀请(已有接口, 补充封装) ---------------- */

export function myInvite() {
  return request('/api/invite')
}

export function refreshInvite() {
  return request('/api/invite/refresh', { method: 'POST' })
}

/**
 * U8 会员价值回顾(2026-10-06 第二批): ?days=30
 * 🔴 只讲"你实际用了多少", **不讲战绩** —— 战绩无法归因到个人, 拿它做续费话术就是编数字。
 *    所以这里没有"选出多少只涨停", 只有 usage_daily 的真实计数。
 */
export function memberValueReview(days = 30) {
  return request('/api/member/value-review?days=' + encodeURIComponent(days))
}
