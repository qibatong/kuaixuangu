// 统一请求封装: 自动注入 token, 统一错误处理(401 清会话跳登录)
import { useUserStore } from '../stores/user'
import { logFront } from '../utils/logger'
import { showToast } from '../utils/toast'

async function parseResp(resp) {
  try { return await resp.json() } catch (e) { return { ok: false, msg: '响应解析失败' } }
}

export async function request(path, { method = 'GET', body, auth = true, query } = {}) {
  const user = useUserStore()
  const headers = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (auth && user.apiToken) headers['Authorization'] = 'Bearer ' + user.apiToken
  let url = path
  if (query) {
    const qs = new URLSearchParams(Object.entries(query).filter(([, v]) => v !== undefined && v !== null && String(v).trim() !== ''))
    const q = qs.toString()
    url += (url.includes('?') ? '&' : '?') + q
  }
  const resp = await fetch(url, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined
  })
  const data = await parseResp(resp)
  const errBody = data.detail || data   // FastAPI HTTPException 的 detail 嵌套兼容
  if (resp.status === 401 && auth) {
    logFront('warn', `API 401 登录失效: ${method} ${url} -> ${errBody.code || ''}`)
    if (errBody.code === 'kicked') {
      // 被另一设备登录顶出: 明确提示(2026-08-17 主人需求)
      showToast('⚠️ 账号已在另一设备登录，本设备已退出', 'error')
    }
    // 登录态失效: 清会话并跳登录
    user.clearSession()
    if (window.location.pathname !== '/login') {
      window.location.href = '/login'
    }
    throw new Error(errBody.msg || '登录已过期，请重新登录')
  }
  if (!resp.ok || !data.ok) {
    logFront('warn', `API 失败: ${method} ${url} -> ${resp.status} ${data.msg || errBody.msg || ''}`)
    const e = new Error(data.msg || errBody.msg || '请求失败(' + resp.status + ')')
    // 透传后端附加字段(如邮箱验证 need_verify_email/uid/email), 供前端分支处理
    if (data && typeof data === 'object') Object.assign(e, data)
    else if (errBody && typeof errBody === 'object') Object.assign(e, errBody)
    throw e
  }
  return data
}
