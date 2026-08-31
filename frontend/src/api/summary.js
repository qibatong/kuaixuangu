// 大V资讯(群总结)相关 API (2026-08-31)
import { request } from './request'

// 总结列表: 按日期倒序分组, 每天含 凌晨盘后/早间/午间/收盘 四个时段
export function summaryHistory() {
  return request('/api/summary/history')
}
