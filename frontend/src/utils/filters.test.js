// 筛选纯函数单测(node:test 零依赖)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { passLockedFilter, defaultFilterSettings, buildFilterParams,
         defaultSpotFilterSettings, buildSpotFilterParams, passSpotFilter } from './filters.js'

const F = { ...defaultFilterSettings }
// 2026-09-11: 夹具必须带 probability —— 9/10 起 scoreFloor=80 是硬门槛(不看信心),
// 无 probability 的票一律被剔除, 旧夹具会假红(HEAD 基线 3 例红即此因)。

// 2026-08-25: 新默认 limitUp=false → "不勾选=剔除昨涨停"; 构造勾选版方便对照测试
const F_KEEP_ZT = { ...F, limitUp: true }   // 勾上"只看昨涨停" → 只保留昨涨停票

test('passLockedFilter: 默认不勾选时剔除昨日涨停/连板(正语义 limitUp=false→剔除)', () => {
  assert.equal(passLockedFilter({ code: '1', concept: '昨日涨停', bidChange: 1, circulationMV: 50, bidAmt: 5000, probability: 90 }, null, F), false)
  assert.equal(passLockedFilter({ code: '2', concept: 'AI+昨日连板', bidChange: 1, circulationMV: 50, bidAmt: 5000, probability: 90 }, null, F), false)
  // 非昨涨停票 → 通过
  assert.equal(passLockedFilter({ code: '1n', concept: '', bidChange: 3, circulationMV: 50, bidAmt: 5000, probability: 90 }, null, F), true)
})

test('passLockedFilter: 勾上"只看昨涨停"后仅保留昨涨停/连板票(正语义 limitUp=true→保留)', () => {
  // 注意: passLockedFilter 只处理"昨日涨停概念"过滤(其它条件仍需满足),
  // 所以非昨涨停票会通过(不被 limitUp 条件排除), 这点与后端 apply_filters 的"严格只看"语义不同.
  // 前端用此函数做锁定名单的二次过滤, 所以仅保证"昨涨停票不被误剔除",
  // 更严格的"只看昨涨停"名单由后端 API 直接返回.
  assert.equal(passLockedFilter({ code: '3', concept: '昨日涨停', bidChange: 3, circulationMV: 50, bidAmt: 5000, probability: 90 }, null, F_KEEP_ZT), true)
  assert.equal(passLockedFilter({ code: '4', concept: 'AI+昨日连板', bidChange: 3, circulationMV: 50, bidAmt: 5000, probability: 90 }, null, F_KEEP_ZT), true)
  // 非昨涨停票也不被剔除(前端不做严格只看, 避免误删正常票)
  assert.equal(passLockedFilter({ code: '4n', concept: '', bidChange: 3, circulationMV: 50, bidAmt: 5000, probability: 90 }, null, F_KEEP_ZT), true)
})

test('passLockedFilter: 竞价涨幅过高剔除', () => {
  assert.equal(passLockedFilter({ code: '3', concept: '', bidChange: 8.1, circulationMV: 50, bidAmt: 5000, probability: 90 }, null, F), false)
  assert.equal(passLockedFilter({ code: '4', concept: '', bidChange: 3, circulationMV: 50, bidAmt: 5000, probability: 90 }, null, F), true)
})

test('passLockedFilter: 市值上下限过滤', () => {
  assert.equal(passLockedFilter({ code: '5', concept: '', bidChange: 3, circulationMV: 10, bidAmt: 5000, probability: 90 }, null, F), false)  // 过小
  assert.equal(passLockedFilter({ code: '6', concept: '', bidChange: 3, circulationMV: 2000, bidAmt: 5000, probability: 90 }, null, F), false) // 过大
})

// 2026-09-14 主人需求: 面板区间显示为 `xx ≤ 流通 ≤ yy`(非严格/含边界)。
// 判据是 `mv < floor -> 剔除` / `mv > ceil -> 剔除`, 所以 mv **恰等于**两端时必须保留。
// 此用例把"显示符号"与"真实行为"绑死 —— 若将来有人把判据改成严格不等,
// 面板的 ≤ 就变成假话, 这条会立刻红。
test('passLockedFilter: 流通区间含边界(mv 恰等于下限/上限均保留)', () => {
  const Fb = { ...F, floatMvFloor: 30, floatMvGt: 200 }
  const row = (mv) => ({ code: 'b', concept: '', bidChange: 3, circulationMV: mv, bidAmt: 5000, probability: 90 })
  assert.equal(passLockedFilter(row(30), null, Fb), true, 'mv == 下限 → 保留(下限是「≥」)')
  assert.equal(passLockedFilter(row(200), null, Fb), true, 'mv == 上限 → 保留(上限是「≤」)')
  assert.equal(passLockedFilter(row(29.99), null, Fb), false, '略低于下限 → 剔除')
  assert.equal(passLockedFilter(row(200.01), null, Fb), false, '略高于上限 → 剔除')
  // 区间正中必过, 佐证开口方向没反
  assert.equal(passLockedFilter(row(100), null, Fb), true)
})

test('passLockedFilter: 股价过滤(rt 实时价优先)', () => {
  assert.equal(passLockedFilter({ code: '7', concept: '', bidChange: 3, circulationMV: 50, bidAmt: 5000, price: 350, probability: 90 }, null, F), false)
  // 有 rt 时用 rt.price
  assert.equal(passLockedFilter({ code: '8', concept: '', bidChange: 3, circulationMV: 50, bidAmt: 5000, price: 100 }, { price: 400 }, F), false)
})

test('passLockedFilter: 竞价金额过滤', () => {
  assert.equal(passLockedFilter({ code: '9', concept: '', bidChange: 3, circulationMV: 50, bidAmt: 1000 }, null, F), false)
})

// ---- 2026-09-11 P0-3/口径修复 ----
test('passLockedFilter: floatMvGt/priceGt 为 0 = 不限(不得把所有票剔除)', () => {
  const F0 = { ...F, floatMvGt: 0, priceGt: 0 }
  // 旧实现 floatMvGt=0 时 `circulationMV > 0` 恒真 → 全剔(用户填 0 表示不限)
  assert.equal(passLockedFilter({ code: 'a', concept: '', bidChange: 3, circulationMV: 2000, bidAmt: 5000, price: 999, probability: 90 }, null, F0), true)
  // 上限给值时仍生效
  assert.equal(passLockedFilter({ code: 'b', concept: '', bidChange: 3, circulationMV: 2000, bidAmt: 5000, probability: 90 }, null, F), false)
  assert.equal(passLockedFilter({ code: 'c', concept: '', bidChange: 3, circulationMV: 50, bidAmt: 5000, price: 350, probability: 90 }, null, F), false)
})

test('passLockedFilter: 字段未知(null) 不抛错, 且与后端同口径(市值缺失→剔除)', () => {
  const base = { code: 'n', concept: '', bidChange: 3, circulationMV: 50, bidAmt: 5000, probability: 90 }
  assert.equal(passLockedFilter({ ...base, circulationMV: null }, null, F), false)  // 同后端 mv_floor
  assert.equal(passLockedFilter({ ...base, bidChange: null }, null, F), true)        // 涨幅缺失→放宽
  assert.equal(passLockedFilter({ ...base, probability: null, scoreFloor: 0 }, null, { ...F, scoreFloor: 0 }), true)
  assert.equal(passLockedFilter({ ...base, bidAmt: null }, null, F), false)          // 竞额缺失→同后端剔除
})

test('buildFilterParams: 设置转 API 参数(默认 false → 字符串"0")', () => {
  const p = buildFilterParams({ ...F, markets: ['hs', 'kcb'] })
  assert.equal(p.stSuspend, '0')
  assert.equal(p.limitUp, '0')
  assert.equal(p.markets, 'hs,kcb')
  assert.equal(p.bidGt, 7)
  assert.equal(p.floatMvFloor, 30)
})
test('buildFilterParams: 勾选后转"1"', () => {
  const p = buildFilterParams({ ...F, stSuspend: true, limitUp: true })
  assert.equal(p.stSuspend, '1')
  assert.equal(p.limitUp, '1')
})


/* ==========================================================================
   v4.11.75 盘中实时(spot)筛选 —— 2026-09-28 重建 spot 时新增。
   核心目的: 把这批"能存能回显、但前端**压根不传**、后端**零消费**"的参数
   钉死在契约上(此前是静默失效的最后一段断路)。
   ========================================================================== */

const FS = { ...defaultFilterSettings, ...defaultSpotFilterSettings }

// ★ 最重要的一条: 6 个盘中参数必须**真的出现在** query 里。
//   变异(把任一 key 删掉) → 本用例必红 ⇒ 防"又被静默丢掉"。
test('buildSpotFilterParams: 6 个盘中参数必须全部传出(防再次静默失效)', () => {
  const p = buildSpotFilterParams(FS)
  const KEYS = ['chgFloor', 'chgGt', 'volRatioFloor', 'turnoverFloor', 'turnoverGt', 'spotExcludeZT']
  for (const k of KEYS) {
    assert.ok(Object.prototype.hasOwnProperty.call(p, k), `缺参数 ${k} —— 会被后端读成默认值, 用户设置静默失效`)
  }
})

// ★ 反向: 竞价专属参数**不得**出现在 spot query 里。
//   后端 apply_spot_filters 不消费 bidGt/bidLt/bidAmtFloor(盘中无"竞价额"语义),
//   送了无害但会误导读日志的人以为 spot 受竞价额约束。
test('buildSpotFilterParams: 不得携带竞价专属参数(后端不消费, 会误导)', () => {
  const p = buildSpotFilterParams({ ...FS, bidGt: 7, bidLt: 0, bidAmtFloor: 3000 })
  assert.equal('bidGt' in p, false, 'bidGt 是竞价涨幅, spot 用 chgGt(实时涨幅)')
  assert.equal('bidLt' in p, false)
  assert.equal('bidAmtFloor' in p, false, 'spot 无"竞价额"语义, 后端明确不消费')
})

test('buildSpotFilterParams: 共用门槛照常透传(与竞价同口径)', () => {
  const p = buildSpotFilterParams({ ...FS, markets: ['hs', 'cyb'], floatMvFloor: 30, priceGt: 300 })
  assert.equal(p.markets, 'hs,cyb')
  assert.equal(p.floatMvFloor, 30)
  assert.equal(p.priceGt, 300)
  assert.equal(p.spotExcludeZT, '0', 'false → "0"')
  assert.equal(buildSpotFilterParams({ ...FS, spotExcludeZT: true }).spotExcludeZT, '1')
})

// ---- passSpotFilter 判据(与后端 apply_spot_filters 同口径) ----
const SP = (over = {}) => ({
  code: '600000', realChange: 3, volRatio: 3, turnover: 5,
  circulationMV: 50, price: 20, probability: 90, ...over
})

test('passSpotFilter: 实时涨幅区间(chgFloor/chgGt), 且用 realChange 不是 bidChange', () => {
  const f = { ...FS, chgFloor: 2, chgGt: 6 }
  assert.equal(passSpotFilter(SP({ realChange: 3 }), f), true)
  assert.equal(passSpotFilter(SP({ realChange: 1.9 }), f), false, '低于 chgFloor')
  assert.equal(passSpotFilter(SP({ realChange: 6.1 }), f), false, '高于 chgGt')
  assert.equal(passSpotFilter(SP({ realChange: 6 }), f), true, '含边界')
  // ★ 口径守点: bidChange 与 realChange 分属两个语义, 不得混用。
  //   构造 bidChange 合规但 realChange 越界的行 —— 若实现错用 bidChange 会误判为 true。
  assert.equal(passSpotFilter(SP({ realChange: 99, bidChange: 3 }), f), false,
    'realChange=99 必须剔除; 若这里通过说明用错了字段(串到 bidChange)')
})

test('passSpotFilter: chgGt=0 = 不限(不是"涨幅必须为 0")', () => {
  const f = { ...FS, chgFloor: 0, chgGt: 0 }
  assert.equal(passSpotFilter(SP({ realChange: 19.9 }), f), true)
})

test('passSpotFilter: 实时涨幅缺失 → 剔除(与后端 no_real_change 同口径)', () => {
  const f = { ...FS, chgFloor: 0, chgGt: 0 }
  assert.equal(passSpotFilter(SP({ realChange: null }), f), false)
  assert.equal(passSpotFilter(SP({ realChange: undefined }), f), false)
})

test('passSpotFilter: 量比下限(0=不限), 缺失且门槛>0 → 剔除', () => {
  assert.equal(passSpotFilter(SP({ volRatio: 5 }), { ...FS, volRatioFloor: 0 }), true, '0=不限, 不看量比')
  assert.equal(passSpotFilter(SP({ volRatio: 5 }), { ...FS, volRatioFloor: 6 }), false)
  assert.equal(passSpotFilter(SP({ volRatio: 6 }), { ...FS, volRatioFloor: 6 }), true, '含边界')
  assert.equal(passSpotFilter(SP({ volRatio: null }), { ...FS, volRatioFloor: 2 }), false, '缺失+有门槛→剔')
})

test('passSpotFilter: 换手率区间(turnoverFloor/turnoverGt), 0=不限', () => {
  const f = { ...FS, turnoverFloor: 2, turnoverGt: 20 }
  assert.equal(passSpotFilter(SP({ turnover: 5 }), f), true)
  assert.equal(passSpotFilter(SP({ turnover: 2 }), f), true, '含边界')
  assert.equal(passSpotFilter(SP({ turnover: 20 }), f), true, '含边界')
  assert.equal(passSpotFilter(SP({ turnover: 1.9 }), f), false)
  assert.equal(passSpotFilter(SP({ turnover: 20.1 }), f), false)
  // 两端 0 = 不限
  assert.equal(passSpotFilter(SP({ turnover: 99 }), { ...FS, turnoverFloor: 0, turnoverGt: 0 }), true)
})

// 🔴 2026-09-28 修正过的契约 —— 这条用例曾**把 bug 固化成"期望"**:
//    原实现读 `it._spotZT`, 旧用例也断言 `SP({ _spotZT: true })`, 于是双双通过;
//    但后端 _spot_payload **从不下发 _spotZT**(实测: 测试机 142 只样本, 该键不存在),
//    ⇒ 分支恒不成立 ⇒ 本地预筛的"剔涨停"与后端名单不一致, 且无人发现。
//    真实同源信号 = 后端 `limit_boards`(= 涨停池 lb), 由 _spot_payload 原样下发。
//    本用例改为锚定 limitBoards, 并显式断言"_spotZT 不再被读取"防回退。
test('passSpotFilter: spotExcludeZT 以 limitBoards 判定(与后端 _spot_zt 同源)', () => {
  // 不勾选 → 不剔, 哪怕它确实是涨停
  assert.equal(passSpotFilter(SP({ limitBoards: 1 }), { ...FS, spotExcludeZT: false }), true)
  // 勾选 + 涨停(lb>0) → 剔除
  assert.equal(passSpotFilter(SP({ limitBoards: 1 }), { ...FS, spotExcludeZT: true }), false)
  assert.equal(passSpotFilter(SP({ limitBoards: 3 }), { ...FS, spotExcludeZT: true }), false, '连板同样剔')
  // 勾选 + 未涨停(lb=0/缺失/None) → 保留(不得因缺字段就把票全剔)
  assert.equal(passSpotFilter(SP({ limitBoards: 0 }), { ...FS, spotExcludeZT: true }), true)
  assert.equal(passSpotFilter(SP({}), { ...FS, spotExcludeZT: true }), true, '缺 limitBoards → 视为未涨停')
  assert.equal(passSpotFilter(SP({ limitBoards: null }), { ...FS, spotExcludeZT: true }), true)
})

test('passSpotFilter: 不得再依赖后端未下发的 _spotZT 键(防回退到旧实现)', () => {
  // 故意只给 _spotZT(旧实现会剔) 而不给 limitBoards —— 现实现应**不受其影响**
  assert.equal(passSpotFilter(SP({ _spotZT: true }), { ...FS, spotExcludeZT: true }), true,
    '_spotZT 不是真实契约(payload 无此键), 不能据此剔除')
})

test('passSpotFilter: 共用门槛(市值/价格/评分/市场)照常生效', () => {
  assert.equal(passSpotFilter(SP({ circulationMV: 10 }), { ...FS, floatMvFloor: 30 }), false)
  assert.equal(passSpotFilter(SP({ circulationMV: 2000 }), { ...FS, floatMvGt: 1000 }), false)
  assert.equal(passSpotFilter(SP({ price: 350 }), { ...FS, priceGt: 300 }), false)
  assert.equal(passSpotFilter(SP({ probability: 40 }), { ...FS, scoreFloor: 50 }), false)
  assert.equal(passSpotFilter(SP({ code: '300001' }), { ...FS, markets: ['hs'] }), false)
  assert.equal(passSpotFilter(SP({ code: '300001' }), { ...FS, markets: ['cyb'] }), true)
})

// 默认参数"不误杀": 六项全默认(不限)时, 一只平平无奇的票必须通过。
// 防止将来有人把默认值写成"看起来合理但实际很严"的数(如 chgGt 默认 9.5)。
test('defaultSpotFilterSettings: 默认值不得误杀(六项默认应全为不限)', () => {
  const d = defaultSpotFilterSettings
  assert.equal(d.chgFloor, 0)
  assert.equal(d.chgGt, 0)
  assert.equal(d.volRatioFloor, 0)
  assert.equal(d.turnoverFloor, 0)
  assert.equal(d.turnoverGt, 0)
  assert.equal(d.spotExcludeZT, false)
  assert.equal(passSpotFilter(SP(), { ...FS }), true, '默认条件下普通票必须通过')
})
