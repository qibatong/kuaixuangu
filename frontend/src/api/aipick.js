// AI 竞价预测报告接口 (2026-08-27 加功能限制: 仅付费/VIP, 需登录)
// 注意: 报告内容是 HTML, 不能用通用 JSON request(); 这里对 HTML 用带鉴权的文本请求
import { useUserStore } from '../stores/user'
import { showToast } from '../utils/toast'
import { request } from './request'

// 报告日期列表(JSON)
export function aipickDates() {
  return request('/api/aipick/dates')
}

// 拉取报告 HTML(文本): latest 或指定日期 detail
export async function aipickReportUrl(date = '') {
  const path = date ? `/api/aipick/detail/${date}` : '/api/aipick/latest'
  const html = await fetchHtml(path)
  return html
}

async function fetchHtml(path) {
  const user = useUserStore()
  const headers = {}
  if (user.apiToken) headers['Authorization'] = 'Bearer ' + user.apiToken
  const resp = await fetch(path, { headers })
  if (resp.status === 401) {
    user.clearSession()
    window.location.href = '/login'
    throw new Error('未登录或登录已过期')
  }
  if (resp.status === 403) {
    const e = new Error('该功能仅限 VIP/付费会员使用，请升级后访问')
    showToast('⚠️ 该报告仅限 VIP/付费会员查看', 'error')
    throw e
  }
  if (!resp.ok) throw new Error('加载失败(' + resp.status + ')')
  return await resp.text()
}