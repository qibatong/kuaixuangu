// 《顺势而为竞价终极版》选股（后端版，2026-10-03）
// 数据源 = 我们后端 /api/his-pick（他的选股逻辑已在后端逐行等价移植）
// 参数与原件筛选条一一对应；force=1 对应原件「重新锁定 / 刷新实时涨幅」两个按钮
import { request } from './request'

export function hisPick(params = {}) {
  return request('/api/his-pick', { query: params })
}
