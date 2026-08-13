// 开盘啦(kpl)数据接口
import { request } from './request'

export function kplSentiment() {
  return request('/api/kpl/sentiment')
}

export function kplBidSeal() {
  return request('/api/kpl/bid-seal')
}

export function kplLadder() {
  return request('/api/kpl/ladder')
}

export function kplBoardRank() {
  return request('/api/kpl/board-rank')
}

export function kplHotRank() {
  return request('/api/kpl/hot-rank')
}

export function kplZtReason(code) {
  return request('/api/kpl/zt-reason', { query: { code } })
}

export function kplWpqc() {
  return request('/api/kpl/wpqc')
}
