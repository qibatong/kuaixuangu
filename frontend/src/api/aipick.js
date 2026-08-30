// AI 竞价预测报告接口 (2026-08-27 加功能限制: 仅付费/VIP, 需登录)
import { request } from './request'

// 报告日期列表(JSON)
export function aipickDates() {
  return request('/api/aipick/dates')
}

// 实时行情(实时涨幅): codes 用逗号拼接
export function aipickRealtime(codes) {
  return request('/api/aipick/realtime', { query: { codes: codes.join(',') } })
}

// 预测报告数据(JSON): 不传 date 取最新, 传 date(YYYY-MM-DD) 回看特定日期
// 2026-08-27: App 内直接拉 JSON 原生渲染表格, 取代嵌套 iframe 老页面
export function aipickData(date = '') {
  const path = date ? `/api/aipick/data/${date}` : '/api/aipick/data'
  return request(path)
}