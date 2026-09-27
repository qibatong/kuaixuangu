// 开盘啦(kpl)数据接口
import { request } from './request'

export function kplSentiment() {
  return request('/api/kpl/sentiment', { cache: 60 })
}

export function kplMarketBrief() {
  return request('/api/kpl/market-brief', { cache: 30 })
}

export function kplIndexBrief() {
  return request('/api/kpl/index-brief', { cache: 30 })
}

export function kplBidSeal(date = '') {
  return request('/api/kpl/bid-seal', { query: date ? { date } : {} })
}

export function kplBidNet(date = '') {
  return request('/api/kpl/bid-net', { query: date ? { date } : {} })
}

export function kplBidBoom(date = '') {
  return request('/api/kpl/bid-boom', { query: date ? { date } : {} })
}

export function kplBroken(day = '', date = '') {
  // day: ''=今日 / 'yesterday'=上一交易日 / 'YYYY-MM-DD'=指定日; date: 历史回看
  // ★ 2026-09-27 v4.11.67: 原来是**二选一**(`date ? {date} : (day ? {day} : {})`) ⇒ 只要带上 date
  //   就把 day 丢掉, 后端只能按 date 取"当日炸板" ⇒ 非交易日(前端会自动把 datePicker 设成最近
  //   交易日)下「今炸板」与「昨炸板」拿到**同一份数据**。二者语义不同, 都需要送达:
  //   date = 看哪一天, day = 看哪一类(当日/昨日)。后端已支持 `date=D & day=yesterday`
  //   =「D 这一天的昨炸板」。
  const query = {}
  if (date) query.date = date
  if (day) query.day = day
  return request('/api/kpl/broken', { query })
}

export function kplLadder(date = '') {
  return request(`/api/kpl/ladder${date ? `?date=${date}` : ''}`)
}

export function kplZtEchelon() {
  return request('/api/kpl/zt-echelon', { cache: 60 })
}

// 连板天梯盘后生成的日期列表(降序)
export function kplLadderDates() {
  return request('/api/ladder/dates')
}

export function kplBoardRank(date = '') {
  return request(`/api/kpl/board-rank${date ? `?date=${date}` : ''}`)
}

export function kplBoardStocks(code, date = '') {
  return request(`/api/kpl/board-stocks?code=${code}${date ? `&date=${date}` : ''}`)
}

// 题材异动榜(2026-09-21 新增, 主源猫爪板块指数 theme_daily/theme_members+screening, 失败降级东财):
// 左栏板块榜(概念 gn/行业 hy) + 右栏成分股
export function emConceptRank(type = 'gn') {
  return request(`/api/kpl/em-concept-rank?type=${type}`)
}
export function emBoardMembers(code) {
  return request(`/api/kpl/em-board-members?code=${code}`)
}


export function kplHotRank(source = 'kpl', date = '') {
  return request(`/api/kpl/hot-rank?source=${source}${date ? `&date=${date}` : ''}`, { cache: 60 })
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
