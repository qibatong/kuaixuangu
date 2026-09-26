// AI 竞价预测报告接口 (2026-08-27 加功能限制: 仅付费/VIP, 需登录)
// 2026-09-25 双模型: 取数接口新增 model 参数('xgb' 默认 | 'lgb')。
//   ★ 默认值必须是 'xgb' —— 不传即等于改造前行为, 老调用零回归。
//   ★ model='lgb' 时后端读 /opt/kuaixuan/aipick/output/lgb(与 XGB 产物严格隔离)。
import { request } from './request'

// 报告日期列表(JSON)
export function aipickDates(model = 'xgb') {
  return request('/api/aipick/dates', { query: { model } })
}

// 实时行情(实时涨幅): codes 用逗号拼接
// 注意: 实时行情是当时的真实市场价格, 与模型无关, 故不带 model。
export function aipickRealtime(codes) {
  return request('/api/aipick/realtime', { query: { codes: codes.join(',') } })
}

// 预测报告数据(JSON): 不传 date 取最新, 传 date(YYYY-MM-DD) 回看特定日期
// 2026-08-27: App 内直接拉 JSON 原生渲染表格, 取代嵌套 iframe 老页面
export function aipickData(date = '', model = 'xgb') {
  const path = date ? `/api/aipick/data/${date}` : '/api/aipick/data'
  return request(path, { query: { model } })
}
