// 盘前资讯 API (2026-09-27 v4.11.59)
// ============================================================================
// 数据由后端 services/news_feed.py 聚合两个上游后归一化:
//   ① 猫爪  apiname="news"            —— 第一财经等财经资讯流(只有 limit 生效)
//   ② 开盘啦 doc96 7x24 快讯 / doc95 头条 / doc97 明天炒什么 / doc99 正文
// ⚠️ 上游挂掉时后端**不返 500**, 而是 ok=true + degraded=[...] + 现有数据;
//    页面必须把 degraded 显示出来, 不能用空列表假装「今天没资讯」。
import { request } from './request'

/** 7x24 快讯(两源合并去重, 时间倒序) */
export function newsFlash(limit = 80) {
  return request('/api/news/flash', { query: { limit } })
}

/** 盘前精选: 开盘啦头条 + 明天炒什么 */
export function newsPremarket() {
  return request('/api/news/premarket')
}

/** 明天炒什么 正文 */
export function guzhangFlash() {
  return request('/api/news/guzhang')
}

export function newsTopicDetail(id) {
  return request('/api/news/topic', { query: { id } })
}
