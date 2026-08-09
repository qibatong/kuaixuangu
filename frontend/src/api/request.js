// 统一请求封装: 自动注入 token, 统一错误处理(401 清会话跳登录)
import { useUserStore } from '../stores/user'
import { logFront } from '../utils/logger'

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
  if (resp.status === 401 && auth) {
    logFront('warn', `API 401 登录失效: ${method} ${url}`)
    // 登录态失效: 清会话并跳登录
    user.clearSession()
    if (window.location.pathname !== '/login') {
      window.location.href = '/login'
    }
    throw new Error(data.msg || '登录已过期，请重新登录')
  }
  if (!resp.ok || !data.ok) {
    logFront('warn', `API 失败: ${method} ${url} -> ${resp.status} ${data.msg || ''}`)
    throw new Error(data.msg || '请求失败(' + resp.status + ')')
  }
  return data
}
