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

// 板块名称与对应题材描述（双脑竞价首页「主线雷达」的资讯行用它，口径同旧独立页 _desc 模糊匹配）
export function kplHotPlates() {
  return request('/api/kpl/hot-plates', { cache: 60 })
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

// ★ 2026-09-28 新增「断板反包」：后端 kpl.fetch_fanbao_stocks() 早就存在（供盘后 PNG 天梯图用），
//   但**没有任何 API 暴露** ⇒ 网页端一直看不到这块信息。现补一个只读端点。
export function kplFanbao(date = '') {
  return request(`/api/kpl/fanbao${date ? `?date=${date}` : ''}`)
}

// 连板天梯盘后生成的日期列表(降序)
export function kplLadderDates() {
  return request('/api/ladder/dates')
}

/** 双脑竞价聚合（只读、零新增上游出网；后端 60s 缓存。⚠️ 不吃 aipick 配额） */
export function chaozhiOverview(pickDate = '') {
  // pickDate 非空 = 回看某日研判（后端会绕过 60s 缓存直接算，避免与当日互相污染）
  return request('/api/chaozhi/overview' + (pickDate ? '?pick_date=' + encodeURIComponent(pickDate) : ''),
                 { cache: 60 })
}

/** 核按钮 / 大幅低开榜（2026-10-06 主人口径，见后端 chaozhi.load_risk_list）。
 *  只读本地 aipick 库（features 的 9:25 定格快照 + 昨日侧标签），**无出网、不吃配额**；盘后数据不再变 ⇒ 30s 足够。 */
export function chaozhiRiskList(date = '') {
  return request('/api/chaozhi/risk-list' + (date ? '?date=' + encodeURIComponent(date) : ''),
                 { cache: 30 })
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

// ★ 2026-09-28 新增：龙虎榜「机构/游资」标签。
//   列表接口(doc100)的原始字段只有 11 个、**没有**机构/游资线索 ⇒ 后端只能按代码拉席位明细
//   (doc101)汇总，故做成独立端点、由前端**按需**调用（切到那两个 tab 时才发一次），
//   避免把 55 只逐个明细的开销压到龙虎榜首屏上。
export function kplLhbTags(codes, date = '') {
  const query = { codes: (codes || []).join(',') }
  if (date) query.date = date
  return request('/api/kpl/lhb-tags', { query })
}

export function kplZtReason(code) {
  return request('/api/kpl/zt-reason', { query: { code } })
}

export function kplWpqc() {
  return request('/api/kpl/wpqc')
}

export function kplBidQiangcang(date = '') {
  // 2026-09-28: 前端缓存按「是否回看」分流。
  //   历史日期的竞价数据不可变 → 300s 安全; **实时路径必须真的到后端** ——
  //   服务端每个请求都要重算"现涨"(_apply_change_for / _update_spot_change),
  //   而 300s 的前端缓存会把 usePolling 的 30s 轮询整个吃掉(实刷频率退化成 300s),
  //   表现就是"竞价只该刷新的实时涨幅反而不刷新"。
  //   注: cache 置 0 不增加免费用户配额消耗(第 2 次本就被 quota_guard 挡成 429 →
  //       前端自动退避), 会员侧只是多打热路径(实测 66~84ms/次)。
  return request('/api/kpl/bid-qiangcang', { query: date ? { date } : {}, cache: date ? 300 : 0 })
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
