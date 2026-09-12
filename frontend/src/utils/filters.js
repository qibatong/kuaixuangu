// 筛选纯函数(与 store 解耦, 便于单测复用)
// 锁定名单按"当前筛选条件"过滤: 条件不允许的票直接移除(不显示)
// 盘中与竞价逻辑统一——同一套筛选条件(竞价涨幅/昨日涨停/市值/价格/竞价金额)
// 2026-08-25 语义反转(正逻辑): limitUp/stSuspend = true → "只看这类票", false → "剔除这类票".
//   因此此处判断改为 NOT: 未勾选"昨涨停"时, 把昨日涨停/连板票丢掉.
export function passLockedFilter(it, rt, f) {
  if (!f.limitUp) {
    const concept = it.concept || ''
    if (concept.includes('昨日涨停') || concept.includes('昨日连板')) return false
  }
  if (it.bidChange > f.bidGt) return false                 // 竞价涨幅过高剔除
  if (it.circulationMV < f.floatMvFloor) return false      // 市值过小
  // 2026-09-11 修: 与后端 picker.filter 同语义 —— floatMvGt/priceGt 为 0 表示**不限**,
  //   旧实现无条件比较, 用户把"流通≤"填 0(不限) 会把所有票剔除(0 = 不限 ≠ 上限 0)。
  if (f.floatMvGt > 0 && it.circulationMV > f.floatMvGt) return false
  const price = rt ? rt.price : it.price
  if (f.priceGt > 0 && price && price > f.priceGt) return false   // 股价过高(0=不限)
  if (it.bidAmt < f.bidAmtFloor) return false              // 竞价金额过低
  // 2026-09-10 主人拍板(全站默认 80): 评分低于门槛的票不显示 — 前端与后端同口径,
  // 锁定名单在前端二次过滤时也必须一致, 否则"锁定后还能看到低分票"。
  if (f.scoreFloor > 0 && (it.probability || 0) < f.scoreFloor) return false
  return true
}

// 默认筛选参数(竞价)
// 2026-08-25: 语义反转后, stSuspend/limitUp 默认 true = 默认"只看这类票",
//   等价于旧默认(剔除ST/剔除昨涨停) → 保持默认行为一致但 UI 直觉正确.
export const defaultFilterSettings = {
  stSuspend: false,
  markets: ['hs', 'cyb', 'kcb'],
  limitUp: false,
  bidGt: 7,
  probLt: 65,
  confLt: 65,
  floatMvFloor: 30,
  floatMvGt: 1000,
  priceGt: 300,
  bidAmtFloor: 3000,
  scoreFloor: 80        // 2026-09-10 主人拍板: 评分低于 80 分不显示(管理员可在后台改默认)
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
    bidAmtFloor: f.bidAmtFloor,
    scoreFloor: f.scoreFloor
  }
}
