// P3 本地筛选纯函数单测 + **与后端 picker.filter 的共用夹具对拍**(node:test, 零依赖)
// 运行: node --test src/utils/pickFromSnapshot.test.js
//
// 对拍夹具取 backend/tests/fixtures/picker_parity.json —— 后端
// tests/test_picker_snapshot.py 读同一份文件。任何一侧算法漂移, 两侧测试一起红。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { pickFromSnapshot, inMarkets, snapshotToRow, COARSE_MAX,
         defaultFilterSettings } from './filters.js'

const FIX = JSON.parse(readFileSync(
  new URL('../../../backend/tests/fixtures/picker_parity.json', import.meta.url), 'utf-8'))

const F = { ...defaultFilterSettings }

test('inMarkets: 主板/创业/科创/北交所 (与后端 filter.in_markets 同口径)', () => {
  const all = ['hs', 'cyb', 'kcb']
  assert.equal(inMarkets('600001', all), true)
  assert.equal(inMarkets('000002', all), true)
  assert.equal(inMarkets('002003', all), true)
  assert.equal(inMarkets('300013', all), true)
  assert.equal(inMarkets('301001', all), true)
  assert.equal(inMarkets('688014', all), true)
  assert.equal(inMarkets('689001', all), true)
  // 北交所一律排除(任何 markets 组合都不放行)
  assert.equal(inMarkets('900015', all), false)
  assert.equal(inMarkets('830001', all), false)
  // 市场范围限定
  assert.equal(inMarkets('300013', ['hs']), false)
  assert.equal(inMarkets('688014', ['hs', 'cyb']), false)
  assert.equal(inMarkets('600001', ['hs']), true)
  // markets 空 → 不限制
  assert.equal(inMarkets('600001', []), true)
})

// ---------------- 与后端对拍(同一份夹具, 逐 case 断言名单与顺序) ----------------
for (const c of FIX.cases) {
  test('对拍后端 picker.filter: ' + c.name, () => {
    const got = pickFromSnapshot(FIX.rows, c.filters)
    assert.deepEqual(got.map((r) => r.code), c.expect)
  })
}

test('COARSE_MAX 仍为 200(与后端 filter.COARSE_MAX / stocks._SNAP_CANDIDATE_MAX 三处同值)', () => {
  assert.equal(COARSE_MAX, 200)
})

test('粗筛排队键 = 定格竞价涨幅(bidChange)降序(2026-09-26 改键), 不再用 coarseRank/竞价额', () => {
  // 230 只票: 涨幅随 i **递减**, 而竞价额随 i **递增** —— 两把尺子方向相反,
  // 结果必须听涨幅的(旧「竞价额降序」会保住涨幅最小的那批 600229)。
  const rows = []
  for (let i = 0; i < 230; i++) {
    rows.push({
      code: String(600000 + i), name: '排序股',
      bidChange: 6.0 - i * 0.02,     // 递减: i 越大涨幅越低(i=0 最高 6.00%)
      bidAmt: 5000 + i,              // 递增: i 越大竞额越高
      floatMv: 55.0, prevClose: 10.0,
      probability: 90, confidence: 80, isSt: 0, isZt: 0
    })
  }
  const out = pickFromSnapshot(rows, F)
  assert.equal(out.length, COARSE_MAX)
  const got = new Set(out.map((r) => r.code))
  assert.ok(!got.has('600229'), '涨幅最低的必须被截断(旧竞价额键会保留它)')
  assert.ok(got.has('600000'), '涨幅最高的必须入选')
})

test('粗筛排队键已与 coarseRank 解耦: 下发该旧字段也不得影响排序(2026-09-26)', () => {
  // 后端已不再下发 coarseRank(见 precompute.read_snapshot_rows 注释)。万一老浏览器
  // 缓存/中间态里还带着它, 也必须**忽略** —— 否则前端按 coarseRank、后端按涨幅,
  // 两侧各一把尺子截断候选, 触顶日名单静默分叉。
  const rows = []
  for (let i = 0; i < 230; i++) {
    rows.push({
      code: String(600000 + i), name: '旧字段股',
      bidChange: 6.0 - i * 0.02,     // 递减
      bidAmt: 5000, floatMv: 55.0, prevClose: 10.0,
      coarseRank: 50 + i,            // 递增 —— 与涨幅方向**相反**(听它就等于保住 600229)
      probability: 90, confidence: 80, isSt: 0, isZt: 0
    })
  }
  const got = new Set(pickFromSnapshot(rows, F).map((r) => r.code))
  assert.ok(got.has('600000'), '必须听 bidChange(涨幅最高者入选)')
  assert.ok(!got.has('600229'), '不得再听 coarseRank(旧键会保留它)')
})

test('粗筛排队键并列: 同涨幅按 code 升序(与后端 score_rows 并列规则一致)', () => {
  const rows = []
  for (let i = 0; i < 230; i++) {
    rows.push({
      code: String(600000 + i), name: '并列股', bidChange: 3.0,
      bidAmt: 5000, floatMv: 55.0, prevClose: 10.0,
      probability: 90, confidence: 80, isSt: 0, isZt: 0
    })
  }
  const out = pickFromSnapshot(rows, F)
  const got = out.map((r) => r.code)
  assert.equal(got.length, COARSE_MAX)
  assert.equal(got[0], '600000')       // code 最小的先进
  assert.ok(!got.includes('600229'))   // code 最大的被截断
})

test('snapshotToRow: 单位与字段名对齐 /api/stocks 的 item(流通=亿)', () => {
  const r = snapshotToRow({ code: '600001', name: '甲', floatMv: 55.0, bidAmt: 8000,
                            probability: 88, confidence: 80, bidChange: 3.0,
                            prevClose: 10, auctionPrice: 10.3, warnType: 4 })
  assert.equal(r.circulationMV, 55.0)     // 亿(与 batch_stocks 口径一致)
  assert.equal(r.bidAmt, 8000)            // 万元
  assert.equal(r.realChange, null)        // 实时字段留给 /api/quotes
})

test('空输入不炸: null/空快照/缺条件 → []', () => {
  assert.deepEqual(pickFromSnapshot(null, F), [])
  assert.deepEqual(pickFromSnapshot([], F), [])
  assert.deepEqual(pickFromSnapshot(FIX.rows, null), [])
})

test('缺失值语义: floatMv/bidAmt 为 null 一律剔除(同后端"无法证明达标→剔除")', () => {
  const base = { name: '缺值股', bidChange: 3.0, probability: 90, confidence: 80,
                 isSt: 0, isZt: 0 }
  assert.equal(pickFromSnapshot([{ ...base, code: '600001', floatMv: null, bidAmt: 8000 }], F).length, 0)
  assert.equal(pickFromSnapshot([{ ...base, code: '600002', floatMv: 55, bidAmt: null }], F).length, 0)
  // bidChange 缺失 = 没有竞价数据 → 不属于竞价名单(同后端 no_bid_change)
  assert.equal(pickFromSnapshot([{ ...base, code: '600003', floatMv: 55, bidAmt: 8000, bidChange: null }], F).length, 0)
})

// 2026-09-14: 面板流通区间显示为 `xx ≤ 流通 ≤ yy`(含边界), 精筛路径判据与
// passLockedFilter 是**两段重复代码**, 必须一起锁 — 否则将来只改一处会静默漂移。
test('流通区间含边界(精筛路径): mv 恰等于下限/上限均保留', () => {
  const base = { name: '边界股', bidChange: 3.0, bidAmt: 8000, probability: 90,
                 confidence: 80, isSt: 0, isZt: 0 }
  const Fb = { ...F, floatMvFloor: 30, floatMvGt: 200 }
  assert.equal(pickFromSnapshot([{ ...base, code: '600001', floatMv: 30 }], Fb).length, 1, 'mv == 下限 → 保留(≥)')
  assert.equal(pickFromSnapshot([{ ...base, code: '600002', floatMv: 200 }], Fb).length, 1, 'mv == 上限 → 保留(≤)')
  assert.equal(pickFromSnapshot([{ ...base, code: '600003', floatMv: 29.9 }], Fb).length, 0, '略低于下限 → 剔除')
  assert.equal(pickFromSnapshot([{ ...base, code: '600004', floatMv: 200.1 }], Fb).length, 0, '略高于上限 → 剔除')
})
