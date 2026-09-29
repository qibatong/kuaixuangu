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
  // 🔴 2026-09-29 主人拍板「北交所纳入」: 默认加 bj(与后端
  //   filter_defaults.SYSTEM_MARKETS / scorer.validate_filters 白名单同步,
  //   前后端默认值有 parity 测试盯着 —— 只改一侧必红)。
  markets: ['hs', 'cyb', 'kcb', 'bj'],
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
   盘中实时(spot)筛选参数 —— 2026-09-28 v4.11.75 重建 spot 能力

   背景(务必理解，否则会误判这批参数是"死参数"):
     这 6 个参数(chgFloor/chgGt/volRatioFloor/turnoverFloor/turnoverGt/spotExcludeZT)
     自 2026-09-09 spot 下线(提交 4c56083)后就**没有任何消费点** —— 后端零消费、
     前端 buildFilterParams **压根不传**。它们在 users.py 偏好白名单与 filters 契约里
     被有意保留(注释: "删除收益 < 契约变更风险")，所以能存能回显，但选了不生效。

     2026-09-28 后端重建 spot 三层后，这批参数在后端 `apply_spot_filters` 里真被消费了；
     本文件负责**把前端设置真正传出去**(这是"静默失效"的最后一段断路)。

   ⚠️ 与竞价参数的分工(不要混用):
     · bidGt / bidLt / bidAmtFloor = **竞价**语义(定格竞价涨幅/竞价额) → spot 不适用
       (spot 是盘中实时，没有"竞价额"这个概念; 后端 apply_spot_filters 也明确不消费它)
     · chgFloor / chgGt = **实时涨幅**区间(r.real_change, 非 bid_change)
     · turnoverFloor / turnoverGt = **换手率**区间(%)
     · volRatioFloor = 量比下限
     · spotExcludeZT = 剔除已封涨停的票(涨停池判定，非"昨日涨停")
   ========================================================================== */

// 盘中实时默认参数。取值依据 = 后端实测(2026-09-28 测试机 5561 只 → 133 只) +
//   六因子评分表的满分区间(涨幅 1.5~6% / 量比≥2 / 换手 2~20%)。
// 注意: 这里**不含** markets/stSuspend/limitUp/floatMv*/priceGt/probLt/confLt/scoreFloor，
//   它们由 defaultFilterSettings 共用(spot 与竞价同口径，见后端 apply_spot_filters 复用共用门槛)。
export const defaultSpotFilterSettings = {
  // 实时涨幅区间(%) —— 后端判据: real_chg < chgFloor 剔除; chgGt>0 且 real_chg > chgGt 剔除。
  // chgGt=0 表示**不限**(与 floatMvGt/priceGt 的 0=不限 约定一致)。
  // 默认给 0/0 = 不限: 让用户先看到全貌，再自己收紧(后端实测全放宽 225 只 vs 默认 133 只)。
  chgFloor: 0,
  chgGt: 0,
  // 量比下限: 0 = 不限。2 = "显著放量"(六因子评分表的满分档起点)。
  volRatioFloor: 0,
  // 换手率区间(%): 0/0 = 不限。后端 0 表示不限(下限时 turnover<0 不可能命中)。
  turnoverFloor: 0,
  turnoverGt: 0,
  // 剔除已封涨停(默认 false = 不剔除; 勾选后请求涨停池判定)
  spotExcludeZT: false
}

// 构建 spot 的 API query —— 在竞价参数基础上**换掉**竞价专属项、**补上**盘中专属项。
//
// 为什么不能用 buildFilterParams + 追加:
//   它会把 bidGt/bidLt/bidAmtFloor 一并送出，而后端 apply_spot_filters **不消费**这三个
//   —— 送了无害但会让读日志的人以为 spot 受竞价额约束(已知误导源)。此处显式剔除，
//   让"前端传的"与"后端消费的"一一对应，可被 grep 核对。
export function buildSpotFilterParams(f) {
  return {
    // ---- 共用门槛(与竞价同口径, 后端 apply_spot_filters 复用) ----
    stSuspend: f.stSuspend ? '1' : '0',
    limitUp: f.limitUp ? '1' : '0',
    markets: (f.markets || []).join(','),
    probLt: f.probLt,
    confLt: f.confLt,
    floatMvFloor: f.floatMvFloor,
    floatMvGt: f.floatMvGt,
    priceGt: f.priceGt,
    scoreFloor: f.scoreFloor,
    // ---- 盘中专属(6 个) ----
    chgFloor: f.chgFloor ?? 0,
    chgGt: f.chgGt ?? 0,
    volRatioFloor: f.volRatioFloor ?? 0,
    turnoverFloor: f.turnoverFloor ?? 0,
    turnoverGt: f.turnoverGt ?? 0,
    spotExcludeZT: f.spotExcludeZT ? '1' : '0'
  }
}

// 盘中实时筛选纯函数 —— 与后端 picker/filter.py::apply_spot_filters **逐条同口径**。
// 用途: 拿到后端 spot 名单后，改条件时的本地即时预筛(可选优化；当前 spot 走真网络请求，
//   因为盘中"现涨/量比/换手"每次都在变，本地快照会立刻过期 —— 与竞价快照性质不同)。
// ⚠️ 若将来接本地预筛，必须与后端对拍；此处先提供判据以固定口径、并给单测锚点。
export function passSpotFilter(it, f) {
  // 1) 实时涨幅区间 —— 缺失(无实时行情)→ 剔除(与后端 no_real_change 同口径)
  const rc = _num(it.realChange)
  if (rc === null) return false
  if (rc < (f.chgFloor ?? 0)) return false
  if ((f.chgGt ?? 0) > 0 && rc > f.chgGt) return false
  // 2) 量比下限(缺失 → 剔除, 与后端 vol_ratio 同口径)
  const vr = _num(it.volRatio)
  const vrFloor = f.volRatioFloor ?? 0
  if (vrFloor > 0 && (vr === null || vr < vrFloor)) return false
  // 3) 换手区间(缺失 → 剔除, 与后端 turnover_floor 同口径)
  const to = _num(it.turnover)
  const toFloor = f.turnoverFloor ?? 0
  if (toFloor > 0 && (to === null || to < toFloor)) return false
  const toGt = f.turnoverGt ?? 0
  if (toGt > 0 && (to === null || to > toGt)) return false
  // 4) 剔涨停 —— 判据必须与后端 apply_spot_filters 的 `_spot_zt` 同源。
  //    后端: _spot_zt = (涨停池该股 lb > 0), 而 lb 被写进评分结果的 limit_boards
  //    (score_spot.py:183 `limit_boards=int((zt_info or {}).get("lb") or 0)`),
  //    _spot_payload 再把 limit_boards 原样下发。
  //    🔴 2026-09-28 修正: 原实现读 `it._spotZT`, 但后端**从未下发**该字段
  //      (payload 无此键) ⇒ 本分支恒不成立 ⇒ 本地预筛会与后端名单不一致。
  //      改用 limitBoards 派生, 与后端同源。
  if (f.spotExcludeZT && (_num(it.limitBoards) ?? 0) > 0) return false
  // 5) 共用门槛: 市值(自由流通) / 价格 / 评分
  const mv = _num(it.circulationMV)
  if (mv !== null && mv < f.floatMvFloor) return false
  if (f.floatMvGt > 0 && mv !== null && mv > f.floatMvGt) return false
  const price = _num(it.price)
  if (f.priceGt > 0 && price !== null && price > f.priceGt) return false
  if (f.scoreFloor > 0 && (_num(it.probability) ?? 0) < f.scoreFloor) return false
  // 6) 市场归属
  if (!inMarkets(it.code, f.markets || [])) return false
  return true
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

export const COARSE_MAX = 200
// 与后端 filter.COARSE_MAX / api/stocks._SNAP_CANDIDATE_MAX **三处必须同值**:
// 粗筛后按**定格竞价涨幅降序**取前 200 只送去评分。
// 本地若不做这个截断, 放宽条件时本地名单会比后端多出一批"后端根本没来得及评分"的票。
// ★ 2026-09-23 主人指令: 120 → 200(后端两处同改)。依据: 松参数下实测平均 143.6 只/日、
//    20 日里 13 日触顶 ⇒ 120 是真实瓶颈。三处任一单独改, 触顶日就会出现
//   「本地秒筛名单 ≠ 后端名单」的漂移。
// ⚠️ 排队键沿革: 「竞价额降序」→ 2026-09-23「coarseRank(定格三因子粗排分)降序」
//    → **2026-09-26「定格竞价涨幅(bidChange)降序」**(主人指令: 直接用"当日涨幅榜")。
//    与后端 picker.filter.coarse_filter / api/stocks._snapshot_candidate_codes
//    **必须同一把尺子**(含缺值位次与并列规则), 否则触顶日本地与后端名单分叉。
//    🔴 2026-09-26 起**不再有 `coarseRank` 这个下发字段** —— 后端已删
//       (见 precompute.read_snapshot_rows 的注释): 涨幅本就是 payload 里的 bidChange,
//       前后端各按同一字段排序即可, 不必再维护一个"必须与后端逐位对齐的派生标量"。

// 市场归属(与后端 scorer._in_markets / picker.filter.in_markets **同口径**):
// hs=沪主板60x+深主板00x | cyb=300/301 | kcb=688/689 | bj=北交所 4/8/920。
// 🔴 2026-09-29 主人拍板「北交所纳入」⇒ 新增 bj 分支(此前一律 false);
//    900xxx(沪B)/200xxx(深B) 不被 4/8/920 命中 ⇒ 仍排除。markets 为空 → 不限制。
export function inMarkets(code, markets) {
  if (!markets || !markets.length) return true
  const c = String(code || '')
  if (c.startsWith('300') || c.startsWith('301')) return markets.includes('cyb')
  if (c.startsWith('688') || c.startsWith('689')) return markets.includes('kcb')
  if (/^(4|8|920)/.test(c)) return markets.includes('bj')
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
  // 粗筛排队键(2026-09-26 改): **定格竞价涨幅(bidChange)降序** —— 直接用"当日涨幅榜"
  // 这把市场公认的尺子。口径依据: 定格时点(9:25 撮合之后) C=O ⇒ 当日涨幅 ≡ 开盘涨幅
  // ≡ 竞价涨幅, 三者同值; 而竞价涨幅的权威来源就是快照的 bidChange。
  // 改键前(2026-09-23~09-26)用的是后端随行下发的「定格三因子粗排分」coarseRank ——
  // 当时因该键要用评分分档表与权重、不宜下发前端, 才由后端算好一个标量; 换成涨幅后
  // 这个标量已取消(见文件顶部 COARSE_MAX 注释), 前后端各按同名字段排序。
  // 能走到这里的票**必定有 bidChange**(_coarseOk 已剔除缺失者); 缺值分支仅作防御,
  // 位次与后端一致(COARSE_RANK_MISSING = 排最后), 不冒充"平开"。
  // 同涨幅按 code 升序(与后端 score_rows 的并列规则一致, 保证结果与输入顺序无关)。
  const _chg = (it) => {
    const v = _num(it.bidChange)
    return v === null ? -Infinity : v
  }
  cands.sort((a, b) => {
    const ca = _chg(a)
    const cb = _chg(b)
    if (ca !== cb) return cb > ca ? 1 : -1
    return a.code < b.code ? -1 : a.code > b.code ? 1 : 0
  })
  const kept = cands.slice(0, COARSE_MAX).filter((it) => _refineOk(it, f))
  const rows = kept.map(snapshotToRow)
  // 与后端 `res.items.sort(key=lambda x: (-probability, code))` 同序:
  // 评分降序; 同分按 code 字典序(纯数字代码, 不用 localeCompare —— 其本地化规则
  // 会把 '10' 排到 '9' 前面, 与 Python 的字典序不一致)
  rows.sort((a, b) => (b.probability || 0) - (a.probability || 0)
    || (a.code < b.code ? -1 : a.code > b.code ? 1 : 0))
  return rows
}
