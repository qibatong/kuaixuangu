// 用户行为上报(2026-09-22, v4.11.35)
// ============================================================================
// 🔴 计数口径: **用户主动操作一次 = 1 次**(主人 2026-09-22 拍板)。
//    所以在**动作回调**里调一次 —— 千万别放进轮询定时器、接口封装或 watch 里,
//    那样会把 30s 轮询也记进去(实测线上 /api/stocks 单日 8,174 次里绝大多数是轮询,
//    记进去这个数字就彻底没意义了)。
// ★ 失败静默: 埋点不重要到可以打断用户; 后端同样整段 try/except 兜住。
import { request } from './request'

// 本会话已上报过的功能(只给"打开即算一次"的纯浏览页用, 避免反复上报)
const _seen = new Set()

export function trackUsage(feature, blocked = false) {
  try {
    const p = request('/api/activity/track', { method: 'POST', body: { feature, blocked } })
    if (p && typeof p.catch === 'function') p.catch(() => {})
  } catch (e) { /* 静默 */ }
}

// 一次会话内只上报一次(用于「题材异动/市场雷达」这类没有动作按钮、打开即浏览的功能页)
export function trackUsageOnce(feature) {
  if (_seen.has(feature)) return
  _seen.add(feature)
  trackUsage(feature)
}
