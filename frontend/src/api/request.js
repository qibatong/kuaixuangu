// 统一请求封装: 自动注入 token, 统一错误处理(401 清会话跳登录)
// 2026-10-01 v4.11.84 (P2-4): 加 `// @ts-check` + JSDoc —— 本文件是全站数据主干的**唯一出口**,
//   字段/返回结构写错会波及所有页面, 值得类型约束。类型只做**校验**（`npm run typecheck`),
//   不参与构建(vite 仍按原样打包)。
// @ts-check
import { useUserStore } from '../stores/user'
import { logFront } from '../utils/logger'
import { showToast } from '../utils/toast'

const memCache = new Map()
const inflight = new Map()

/**
 * @typedef {Object} RequestOpts
 * @property {string} [method]      HTTP 方法, 默认 GET
 * @property {any}    [body]        请求体(GET 一般不传)
 * @property {boolean} [auth]       是否注入 Authorization, 默认 true
 * @property {Record<string, any>} [query] query 参数(undefined/null/空白串会被剔除)
 * @property {number} [cache]       GET 内存缓存秒数(同时开启在途去重)
 */

/** @param {Response} resp @returns {Promise<any>} */
async function parseResp(resp) {
  try { return await resp.json() } catch (e) { return { ok: false, msg: '响应解析失败' } }
}

/**
 * @param {string} path
 * @param {RequestOpts} [opts]
 * @returns {Promise<any>}
 */
export async function request(path, { method = 'GET', body, auth = true, query, cache } = {}) {
  const user = useUserStore()
  /** @type {Record<string, string>} */
  const headers = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (auth && user.apiToken) headers['Authorization'] = 'Bearer ' + user.apiToken
  let url = path
  if (query) {
    const qs = new URLSearchParams(Object.entries(query).filter(([, v]) => v !== undefined && v !== null && String(v).trim() !== ''))
    const q = qs.toString()
    url += (url.includes('?') ? '&' : '?') + q
  }

  // GET 请求支持前端内存缓存 + 请求去重
  if (method === 'GET' && cache) {
    const ck = 'GET ' + url
    const hit = memCache.get(ck)
    if (hit && Date.now() - hit.t < cache * 1000) return hit.data
    if (inflight.has(ck)) return inflight.get(ck)
    const p = (async () => {
      try {
        const data = await doFetch(url, method, headers, body)
        memCache.set(ck, { data, t: Date.now() })
        return data
      } finally { inflight.delete(ck) }
    })()
    inflight.set(ck, p)
    return p
  }
  return doFetch(url, method, headers, body)
}

/**
 * @param {string} url
 * @param {string} method
 * @param {Record<string, string>} headers
 * @param {any} body
 * @returns {Promise<any>}
 */
async function doFetch(url, method, headers, body) {
  const resp = await fetch(url, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined
  })
  const data = await parseResp(resp)
  const errBody = data.detail || data
  if (resp.status === 401 && headers.Authorization) {
    const user = useUserStore()
    logFront('warn', `API 401 登录失效: ${method} ${url} -> ${errBody.code || ''}`)
    if (errBody.code === 'kicked') {
      showToast('⚠️ 账号已在另一设备登录，本设备已退出', 'error')
    }
    user.clearSession()
    if (window.location.pathname !== '/login') {
      window.location.href = '/login'
    }
    throw new Error(errBody.msg || '登录已过期，请重新登录')
  }
  if (!resp.ok || !data.ok) {
    logFront('warn', `API 失败: ${method} ${url} -> ${resp.status} ${data.msg || errBody.msg || ''}`)
    // TS: Error 上没有 status/code 等自定义字段(下面 Object.assign 挂上去), 这里显式声明
    const e = /** @type {Error & Record<string, any>} */ (
      new Error(data.msg || errBody.msg || '请求失败(' + resp.status + ')'))
    if (data && typeof data === 'object') Object.assign(e, data)
    else if (errBody && typeof errBody === 'object') Object.assign(e, errBody)
    if (errBody && typeof errBody === 'object' && errBody !== data) Object.assign(e, errBody)
    e.status = resp.status
    throw e
  }
  return data
}
