// 战绩分析 API
import { request } from './request'

export function fetchPerformance(query = {}) {
  return request('/api/stats/performance', { query })
}

// 2026-09-30 v4.11.83 实机体检修复: auction-overview 首屏被调用多次(实机抓包 3 次/首屏)。
//   本接口被「轮询探服务日期 refreshServeDate()」与「整页加载 loadAll()」两处各调一次,
//   首屏 + 首次轮询会落在同一两秒内 ⇒ 3 次请求。这里做**在途去重 + 3 秒记忆化**
//   (与 api/stocks.js 的 getPrefs 同一套路), 人工刷新(>3s)与历史回看(带 date)不受影响:
//   date 参与缓存键, 切换日期必重新取数。
const _AO_TTL = 3000
let _aoKey = null
let _aoCacheAt = 0
let _aoData = null
let _aoInflight = null

export function auctionOverview(date = '') {
  const key = String(date || '')
  const now = Date.now()
  if (_aoData !== null && _aoKey === key && now - _aoCacheAt < _AO_TTL) {
    return Promise.resolve(_aoData)
  }
  if (_aoInflight && _aoInflight.key === key) return _aoInflight.p
  const p = request('/api/stats/auction-overview', { query: date ? { date } : {} })
    .then((d) => { _aoData = d; _aoKey = key; _aoCacheAt = Date.now(); return d })
    .finally(() => { _aoInflight = null })
  _aoInflight = { key, p }
  return p
}

export function auctionSnapshot(date, timePoint) {
  return request('/api/stats/auction-snapshot', { query: { date, time_point: timePoint } })
}

// 个股三时点封单对比(9:15/9:20/9:25 一个视图看全)
export function bidSnapshotStock(date, code) {
  return request('/api/stats/bid-snapshot-stock', { query: { date, code } })
}

// 全市场三时点封单榜(三层排序: 9:25涨停 > 9:20涨停回落 > 9:15涨停回落)
export function bidSnapshot3points(date, limit = 100) {
  return request('/api/stats/bid-snapshot-3points', { query: { date, limit } })
}

// 连续 N 日竞价封单(多列并排: 每日 9:15/9:20/9:25 封单额 + 涨幅 + 环比趋势)
// 数据源 = 猫爪 daily_auc_fd 分时封单 ⇒ **历史交易日与今日同等可用**
// (与 bidSnapshot3points 读自采库不同: 后者历史日 9:15/9:20 两列是旧代码产物, 恒空)
// 连续 N 日竞价封单(2026-09-30: 页面由 5 日改 4 日, 缺省值一并对齐)
export function bidSealDaily(end = '', days = 4, limit = 200) {
  return request('/api/stats/bid-seal-daily', { query: { end, days, limit } })
}

// 竞价精选(ZH 选股): 涨停基因 × 高开≥3% × 竞价放量占昨量 5~10%
export function zhPicks(date = '') {
  return request('/api/stats/zh-picks', { query: date ? { date } : {} })
}
