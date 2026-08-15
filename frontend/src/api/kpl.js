// 开盘啦(kpl)数据接口
import { request } from './request'

export function kplSentiment() {
  return request('/api/kpl/sentiment')
}

export function kplBidSeal() {
  return request('/api/kpl/bid-seal')
}

export function kplBidBoom() {
  return request('/api/kpl/bid-boom')
}

export function kplBroken(day = '') {
  // day: ''=今日 / 'yesterday'=上一交易日 / 'YYYY-MM-DD'=指定日
  return request('/api/kpl/broken', { query: day ? { day } : {} })
}

export function kplLadder() {
  return request('/api/kpl/ladder')
}

export function kplBoardRank(date = '') {
  return request(`/api/kpl/board-rank${date ? `?date=${date}` : ''}`)
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

export function kplBidQiangcang() {
  return request('/api/kpl/bid-qiangcang')
}

export function kplYestZt() {
  return request('/api/kpl/yest-zt')
}

export function kplYestBroken() {
  return request('/api/kpl/yest-broken')
}

export function kplYesterdayPerf() {
  return request('/api/kpl/yesterday-perf')
}

// 板块轮动历史(多日 Top10 + 强度/量能/多窗口排名趋势, source: kpl/em/ths)
export function sectorRotation(days = 10, source = 'kpl') {
  return request(`/api/kpl/sector-rotation?days=${days}&source=${source}`)
}
