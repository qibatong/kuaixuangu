// P3(2026-09-12) 本地筛选相关 API
import { request } from './request'

// 当日全市场预计算评分快照 —— 前端据此在浏览器内完成筛选(改条件秒出)。
// 后端 frontend_local_filter 开关关闭 / 物化表不可用 / 非 VIP 时:
//   * 开关关与表不可用 → {ok:true, enabled:false, list:[]}
//   * 非 VIP          → HTTP 403
// 两种都不是故障, 调用方一律**静默回退**原后端筛选路径(不重试、不打扰用户)。
export function fetchPickerSnapshot(date) {
  return request('/api/picker/snapshot', { query: date ? { date } : {} })
}
