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

test('粗筛截断: 竞额降序前 120 只之外不出现(与后端 COARSE_MAX 同)', () => {
  const rows = []
  for (let i = 0; i < 130; i++) {
    rows.push({
      code: String(600000 + i), name: '截断股', bidChange: 3.0,
      bidAmt: 5000 + i,          // 递增: i 越大竞额越高
      floatMv: 55.0, prevClose: 10.0,
      probability: 90, confidence: 80, isSt: 0, isZt: 0
    })
  }
  const out = pickFromSnapshot(rows, F)
  assert.equal(out.length, COARSE_MAX)
  const got = new Set(out.map((r) => r.code))
  for (let i = 0; i < 10; i++) {
    assert.ok(!got.has(String(600000 + i)), '竞额最低的 10 只应被截断丢弃')
  }
  assert.ok(got.has('600129'), '竞额最高的必须入选')
})

test('snapshotToRow: 单位与字段名对齐 /api/stocks 的 item(流通=亿)', () => {
  const r = snapshotToRow({ code: '600001', name: '甲', floatMv: 55.0, bidAmt: 8000,
                            probability: 88, confidence: 80, bidChange: 3.0,
                            prevClose: 10, auctionPrice: 10.3, warnType: 4 })
  assert.equal(r.circulationMV, 55.0)     // 亿(与 batch_stocks 口径一致)
  assert.equal(r.bidAmt, 8000)            // 万元
  assert.equal(r.realChange, null)        // 实时字段留给 /api/quotes
  assert.equal(r.qiangchou, 0)
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
