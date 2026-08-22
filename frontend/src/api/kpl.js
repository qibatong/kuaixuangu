// 开盘啦(kpl)数据接口
import { request } from './request'

export function kplSentiment() {
  return request('/api/kpl/sentiment')
}

export function kplMarketBrief() {
  return request('/api/kpl/market-brief')
}

export function kplBidSeal(date = '') {
  return request('/api/kpl/bid-seal', { query: date ? { date } : {} })
}

export function kplBidNet() {
  return request('/api/kpl/bid-net')
}

export function kplBidBoom(date = '') {
  return request('/api/kpl/bid-boom', { query: date ? { date } : {} })
}

export function kplBroken(day = '', date = '') {
  // day: ''=今日 / 'yesterday'=上一交易日 / 'YYYY-MM-DD'=指定日; date: 历史回看
  return request('/api/kpl/broken', { query: date ? { date } : (day ? { day } : {}) })
}

export function kplLadder(date = '') {
  return request(`/api/kpl/ladder${date ? `?date=${date}` : ''}`)
}

export function kplBoardRank(date = '') {
  return request(`/api/kpl/board-rank${date ? `?date=${date}` : ''}`)
}

export function kplBoardStocks(code, date = '') {
  return request(`/api/kpl/board-stocks?code=${code}${date ? `&date=${date}` : ''}`)
}

export function kplHotRank(source = 'kpl', date = '') {
  return request(`/api/kpl/hot-rank?source=${source}${date ? `&date=${date}` : ''}`)
}

export function kplLhb(date = '') {
  return request(`/api/kpl/lhb${date ? `?date=${date}` : ''}`)
}

export function kplLhbDetail(code, date = '') {
  return request('/api/kpl/lhb-detail', { query: { code, date } })
}

export function kplZtReason(code) {
  return request('/api/kpl/zt-reason', { query: { code } })
}

export function kplWpqc() {
  return request('/api/kpl/wpqc')
}

export function kplBidQiangcang(date = '') {
  return request('/api/kpl/bid-qiangcang', { query: date ? { date } : {} })
}

export function kplYestZt(date = '') {
  return request('/api/kpl/yest-zt', { query: date ? { date } : {} })
}

export function kplYestBroken(date = '') {
  return request('/api/kpl/yest-broken', { query: date ? { date } : {} })
}

export function kplYesterdayPerf() {
  return request('/api/kpl/yesterday-perf')
}

// 板块轮动历史(多日 Top10 + 强度/量能/多窗口排名趋势, source: kpl/em/ths)
export function sectorRotation(days = 10, source = 'kpl') {
  return request(`/api/kpl/sector-rotation?days=${days}&source=${source}`)
}

// 异动监管(开盘啦 doc90/doc108/doc109 + 热门股偏离值 GetPianLiZhi_Hot)
export function kplYidongRealtime() {
  return request('/api/kpl/yidong-realtime')
}

export function kplYidongHot() {
  return request('/api/kpl/yidong-hot')
}

export function kplYidongMonitor() {
  return request('/api/kpl/yidong-monitor')
}

export function kplYidongMulti() {
  return request('/api/kpl/yidong-multi')
}
