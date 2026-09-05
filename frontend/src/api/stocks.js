// 选股相关 API
import { request } from './request'

export function fetchStocks(action, filterParams, mode = 'auction', force = false) {
  // force=true: 主动重锁(绕过当日幂等, 9:25 后同参自动 lock 会直读当日批次)
  return request('/api/stocks', {
    query: { action, mode, ...(force ? { force: 1 } : {}), ...filterParams }
  })
}

export function stockChart(code, period = 'day') {
  return request('/api/stock/chart', { query: { code, period } })
}

// 2026-09-05 B 方案(拆分独立行情接口): /api/stocks 不再下发全市场 spotMap,
// 前端对"不在返回名单的锁定票"等少量 code 按需取实时价。codes 为 6 位代码数组。
export function fetchQuotes(codes) {
  if (!codes || !codes.length) return Promise.resolve({ ok: true, quotes: {} })
  return request('/api/quotes', { query: { codes: codes.join(',') } })
}

// 2026-09-04 prefs 双读合并: 首屏 App.useTheme.load() + StockView/PoolView.loadUserPrefs()
// 都调 getPrefs → 截图实测同一次打开请求 2 次(140ms+1.17s 撞首屏并发排队)。
// getPrefs 做 30s 记忆化 + 在途请求去重: 同一页面生命周期内仅发一次网络请求,
// 其余调用命中内存缓存(主题/筛选偏好低频变化, 30s 新鲜度足够; savePrefs 后立即失效)。
let _prefsCache = null
let _prefsTs = 0
let _prefsInflight = null
const PREFS_TTL = 30 * 1000

export function getPrefs() {
  const now = Date.now()
  if (_prefsCache && now - _prefsTs < PREFS_TTL) {
    return Promise.resolve(_prefsCache)
  }
  if (_prefsInflight) return _prefsInflight   // 并发去重: 复用同一在途请求
  _prefsInflight = request('/api/prefs')
    .then(d => { _prefsCache = d; _prefsTs = Date.now(); return d })
    .finally(() => { _prefsInflight = null })
  return _prefsInflight
}

function invalidatePrefs() {
  _prefsCache = null
  _prefsTs = 0
}

export function getDefaultFilters() {
  // 全局默认筛选参数(管理员后台可调), 未自定义偏好的用户使用
  return request('/api/prefs/defaults')
}

export function savePrefs(settings) {
  invalidatePrefs()   // 保存后清缓存, 下次 getPrefs 拿最新
  return request('/api/prefs', { method: 'POST', body: { settings } })
}
