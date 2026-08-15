// 筛选纯函数单测(node:test 零依赖)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { passLockedFilter, defaultFilterSettings, buildFilterParams } from './filters.js'

const F = { ...defaultFilterSettings }

test('passLockedFilter: 昨日涨停/连板概念剔除', () => {
  assert.equal(passLockedFilter({ code: '1', concept: '昨日涨停', bidChange: 1, circulationMV: 50, bidAmt: 5000 }, null, F), false)
  assert.equal(passLockedFilter({ code: '2', concept: 'AI+昨日连板', bidChange: 1, circulationMV: 50, bidAmt: 5000 }, null, F), false)
})

test('passLockedFilter: 竞价涨幅过高剔除', () => {
  assert.equal(passLockedFilter({ code: '3', concept: '', bidChange: 8.1, circulationMV: 50, bidAmt: 5000 }, null, F), false)
  assert.equal(passLockedFilter({ code: '4', concept: '', bidChange: 3, circulationMV: 50, bidAmt: 5000 }, null, F), true)
})

test('passLockedFilter: 市值上下限过滤', () => {
  assert.equal(passLockedFilter({ code: '5', concept: '', bidChange: 3, circulationMV: 10, bidAmt: 5000 }, null, F), false)  // 过小
  assert.equal(passLockedFilter({ code: '6', concept: '', bidChange: 3, circulationMV: 2000, bidAmt: 5000 }, null, F), false) // 过大
})

test('passLockedFilter: 股价过滤(rt 实时价优先)', () => {
  assert.equal(passLockedFilter({ code: '7', concept: '', bidChange: 3, circulationMV: 50, bidAmt: 5000, price: 350 }, null, F), false)
  // 有 rt 时用 rt.price
  assert.equal(passLockedFilter({ code: '8', concept: '', bidChange: 3, circulationMV: 50, bidAmt: 5000, price: 100 }, { price: 400 }, F), false)
})

test('passLockedFilter: 竞价金额过滤', () => {
  assert.equal(passLockedFilter({ code: '9', concept: '', bidChange: 3, circulationMV: 50, bidAmt: 1000 }, null, F), false)
})

test('buildFilterParams: 设置转 API 参数', () => {
  const p = buildFilterParams({ ...F, markets: ['hs', 'kcb'] })
  assert.equal(p.stSuspend, '1')
  assert.equal(p.limitUp, '1')
  assert.equal(p.markets, 'hs,kcb')
  assert.equal(p.bidGt, 7)
  assert.equal(p.floatMvFloor, 30)
})
