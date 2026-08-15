// 筛选纯函数(与 store 解耦, 便于单测复用)
// 锁定名单按"当前筛选条件"过滤: 条件不允许的票直接移除(不显示)
// 盘中与竞价逻辑统一——同一套筛选条件(竞价涨幅/昨日涨停/市值/价格/竞价金额)
export function passLockedFilter(it, rt, f) {
  if (f.limitUp) {
    const concept = it.concept || ''
    if (concept.includes('昨日涨停') || concept.includes('昨日连板')) return false
  }
  if (it.bidChange > f.bidGt) return false                 // 竞价涨幅过高剔除
  if (it.circulationMV < f.floatMvFloor) return false      // 市值过小
  if (it.circulationMV > f.floatMvGt) return false         // 市值过大
  const price = rt ? rt.price : it.price
  if (price && price > f.priceGt) return false             // 股价过高
  if (it.bidAmt < f.bidAmtFloor) return false              // 竞价金额过低
  return true
}

// 默认筛选参数(竞价)
export const defaultFilterSettings = {
  stSuspend: true,
  markets: ['hs', 'cyb', 'kcb'],
  limitUp: true,
  bidGt: 7,
  probLt: 65,
  confLt: 65,
  floatMvFloor: 30,
  floatMvGt: 1000,
  priceGt: 300,
  bidAmtFloor: 3000
}

// 构建后端筛选参数(把筛选设置转成 API query)
export function buildFilterParams(f) {
  return {
    stSuspend: f.stSuspend ? '1' : '0',
    limitUp: f.limitUp ? '1' : '0',
    markets: f.markets.join(','),
    bidGt: f.bidGt,
    probLt: f.probLt,
    confLt: f.confLt,
    floatMvFloor: f.floatMvFloor,
    floatMvGt: f.floatMvGt,
    priceGt: f.priceGt,
    bidAmtFloor: f.bidAmtFloor
  }
}
