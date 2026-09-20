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
  // ★ 2026-09-20 口径改**自由流通市值**(主人拍板「所有流通市值改自由流通市值」):
  //   绑定字段名 circulationMV 不变, 但其值由后端统一为 mv(= free_mv 优先, 缺则 float_mv),
  //   与后端 picker.filter 的 floatMvFloor/Gt 门槛**同源同口径**。
  if (it.circulationMV < f.floatMvFloor) return false      // 自由流通市值过小
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
  // 2026-09-12 P3: 竞价涨幅**下限**(后端 2026-09-09 已支持, 默认 0 = 竞价翻绿即剔)。
  // 前端此前不传该参数, 后端吃默认 0; 本地筛选要与后端同一套门槛, 故这里显式化。
  bidLt: 0,
  probLt: 50,
  confLt: 50,
  floatMvFloor: 30,
  floatMvGt: 1000,
  priceGt: 300,
  bidAmtFloor: 3000,
  scoreFloor: 50        // 2026-09-20 主人拍板: 评分低于 50 分不显示(管理员可在后台改默认)
}

// 构建后端筛选参数(把筛选设置转成 API query)
export function buildFilterParams(f) {
  return {
    stSuspend: f.stSuspend ? '1' : '0',
    limitUp: f.limitUp ? '1' : '0',
    markets: f.markets.join(','),
    bidGt: f.bidGt,
    bidLt: f.bidLt ?? 0,
    probLt: f.probLt,
    confLt: f.confLt,
    floatMvFloor: f.floatMvFloor,
    floatMvGt: f.floatMvGt,
    priceGt: f.priceGt,
    bidAmtFloor: f.bidAmtFloor,
    scoreFloor: f.scoreFloor
  }
}


/* ==========================================================================
   P3 本地筛选(2026-09-12): 用后端一次性下发的全市场预计算快照, 在浏览器里完成
   与后端 picker/filter.py **逐条同口径**的过滤 —— 改筛选条件秒出, 不再打后端。

   为什么必须逐条复刻:
     后端链路 = coarse_filter(全市场 → 候选) + apply_filters(精筛)。若本地少一步,
     "本地秒筛名单"与"后端名单"就会出现差异(同一套条件出两批票), 用户锁定/历史/
     推送全线错位。唯一防线是对拍测试(tests/pickFromSnapshot.test.js 与后端
     tests/test_picker_snapshot.py 用同一份夹具)。
   ========================================================================== */

export const COARSE_MAX = 120
// 与后端 filter.COARSE_MAX 同值: 粗筛后按**竞价额降序**取前 120 只送去评分。
// 本地若不做这个截断, 放宽条件时本地名单会比后端多出一批"后端根本没来得及评分"的票。

// 市场归属(与后端 filter.in_markets 同口径): hs=沪主板60x+深主板00x | cyb=300/301
// | kcb=688/689; 北交所一律排除。markets 为空 → 不限制。
export function inMarkets(code, markets) {
  if (!markets || !markets.length) return true
  const c = String(code || '')
  if (c.startsWith('300') || c.startsWith('301')) return markets.includes('cyb')
  if (c.startsWith('688') || c.startsWith('689')) return markets.includes('kcb')
  if (/^(600|601|603|605|000|001|002|003)/.test(c)) return markets.includes('hs')
  return false
}

function _num(v) {
  if (v === null || v === undefined || v === '' || isNaN(v)) return null
  return Number(v)
}

// 粗筛(评分前): 只用定格数据即可判定的门槛 —— 与后端 coarse_filter 一一对应
function _coarseOk(it, f, mk) {
  if (!inMarkets(it.code, mk)) return false
  if (!f.limitUp && it.isZt) return false              // 昨涨停/连板(勾选=包含)
  if (!f.stSuspend && (it.isSt || /ST/.test(it.name || ''))) return false
  const bc = _num(it.bidChange)
  if (bc === null) return false                        // 无竞价数据 → 本就不该在竞价名单里
  if (bc < (f.bidLt ?? 0)) return false                // 低开/大跌剔除
  if (bc > f.bidGt) return false
  const mv = _num(it.floatMv)
  if (mv === null || mv < f.floatMvFloor) return false
  if (f.floatMvGt > 0 && mv > f.floatMvGt) return false
  const amt = _num(it.bidAmt)
  if (amt === null || amt < f.bidAmtFloor) return false
  return true
}

// 精筛(评分后): 双低剔除 → 评分下限 → 市值 → 竞额 → 价格上限(用定格竞价价)
function _refineOk(it, f) {
  const p = _num(it.probability)
  const c = _num(it.confidence)
  // 后端: 概率与信心**同时**低于门槛才剔除(高信心可救低概率)
  if (p !== null && c !== null && p < f.probLt && c < f.confLt) return false
  // scoreFloor 是单阈值硬门槛, 不看信心; 0 = 关闭
  if (f.scoreFloor > 0 && p !== null && p < f.scoreFloor) return false
  const mv = _num(it.floatMv)
  if (mv === null || mv < f.floatMvFloor) return false
  if (f.floatMvGt > 0 && mv > f.floatMvGt) return false
  const amt = _num(it.bidAmt)
  if (amt === null || amt < f.bidAmtFloor) return false
  const price = _num(it.auctionPrice) ?? _frozenPrice(it)
  // 定格竞价价(全天恒定, 不用实时价) —— 与后端 price_gate="auction" 同口径
  if (f.priceGt > 0 && price !== null && price > f.priceGt) return false
  return true
}

// 定格竞价价缺失时的派生: 昨收×(1+竞价涨幅/100)。
// 与后端 QuoteRow.auction_price **同式** —— 物化表该列为空(昨收缺失)时两侧同为 None
// (门槛不生效), 不为空时两侧同值。夹具与真实接口都下发 auctionPrice, 这里只是兜底。
function _frozenPrice(it) {
  const pc = _num(it.prevClose)
  const bc = _num(it.bidChange)
  if (pc === null || bc === null || pc <= 0) return null
  return pc * (1 + bc / 100)
}

// 快照行 → 前端表格行(字段名对齐 /api/stocks 的 item 结构)
export function snapshotToRow(it) {
  return {
    code: it.code, name: it.name,
    probability: it.probability, confidence: it.confidence,
    bidChange: it.bidChange, bidAmt: it.bidAmt,
    circulationMV: it.floatMv,                        // 亿(与 batch_stocks 口径一致)
    prevClose: it.prevClose, auctionPrice: it.auctionPrice,
    warnType: it.warnType, industry: it.industry, concept: it.concept,
    rank: it.rank,
    price: null, realChange: null, entityChange: null,
    volRatio: null, turnover: null
  }
}

/**
 * 本地筛选: 快照列表 + 筛选条件 → 名单(已按 probability 降序, 与后端输出同序)。
 * 不修改入参; 快照不可用/为空时返回 []。
 */
export function pickFromSnapshot(snap, f) {
  if (!snap || !snap.length || !f) return []
  const mk = f.markets || []
  const cands = []
  for (const it of snap) {
    if (_coarseOk(it, f, mk)) cands.push(it)
  }
  // 竞额降序取前 COARSE_MAX(与后端粗筛的截断位置一致), 再精筛
  cands.sort((a, b) => (_num(b.bidAmt) || 0) - (_num(a.bidAmt) || 0))
  const kept = cands.slice(0, COARSE_MAX).filter((it) => _refineOk(it, f))
  const rows = kept.map(snapshotToRow)
  // 与后端 `res.items.sort(key=lambda x: (-probability, code))` 同序:
  // 评分降序; 同分按 code 字典序(纯数字代码, 不用 localeCompare —— 其本地化规则
  // 会把 '10' 排到 '9' 前面, 与 Python 的字典序不一致)
  rows.sort((a, b) => (b.probability || 0) - (a.probability || 0)
    || (a.code < b.code ? -1 : a.code > b.code ? 1 : 0))
  return rows
}
