// 筛选纯函数单测(node:test 零依赖)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { passLockedFilter, defaultFilterSettings, buildFilterParams } from './filters.js'

const F = { ...defaultFilterSettings }
// 2026-08-25: 新默认 limitUp=false → "不勾选=剔除昨涨停"; 构造勾选版方便对照测试
const F_KEEP_ZT = { ...F, limitUp: true }   // 勾上"只看昨涨停" → 只保留昨涨停票

test('passLockedFilter: 默认不勾选时剔除昨日涨停/连板(正语义 limitUp=false→剔除)', () => {
  assert.equal(passLockedFilter({ code: '1', concept: '昨日涨停', bidChange: 1, circulationMV: 50, bidAmt: 5000 }, null, F), false)
  assert.equal(passLockedFilter({ code: '2', concept: 'AI+昨日连板', bidChange: 1, circulationMV: 50, bidAmt: 5000 }, null, F), false)
  // 非昨涨停票 → 通过
  assert.equal(passLockedFilter({ code: '1n', concept: '', bidChange: 3, circulationMV: 50, bidAmt: 5000 }, null, F), true)
})

test('passLockedFilter: 勾上"只看昨涨停"后仅保留昨涨停/连板票(正语义 limitUp=true→保留)', () => {
  // 注意: passLockedFilter 只处理"昨日涨停概念"过滤(其它条件仍需满足),
  // 所以非昨涨停票会通过(不被 limitUp 条件排除), 这点与后端 apply_filters 的"严格只看"语义不同.
  // 前端用此函数做锁定名单的二次过滤, 所以仅保证"昨涨停票不被误剔除",
  // 更严格的"只看昨涨停"名单由后端 API 直接返回.
  assert.equal(passLockedFilter({ code: '3', concept: '昨日涨停', bidChange: 3, circulationMV: 50, bidAmt: 5000 }, null, F_KEEP_ZT), true)
  assert.equal(passLockedFilter({ code: '4', concept: 'AI+昨日连板', bidChange: 3, circulationMV: 50, bidAmt: 5000 }, null, F_KEEP_ZT), true)
  // 非昨涨停票也不被剔除(前端不做严格只看, 避免误删正常票)
  assert.equal(passLockedFilter({ code: '4n', concept: '', bidChange: 3, circulationMV: 50, bidAmt: 5000 }, null, F_KEEP_ZT), true)
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

// 2026-09-08 竞涨下限 bidLt: 竞价大幅低开(资金出逃形态)剔除; null/undefined/'' = 不限(默认)
test('passLockedFilter: bidLt 为空(null/undefined)不设下限, 负竞涨保留(老行为)', () => {
  assert.equal(passLockedFilter({ code: 'b1', concept: '', bidChange: -5.46, circulationMV: 50, bidAmt: 5000 }, null, F), true)
  assert.equal(passLockedFilter({ code: 'b2', concept: '', bidChange: -5.46, circulationMV: 50, bidAmt: 5000 }, null, { ...F, bidLt: undefined }), true)
  assert.equal(passLockedFilter({ code: 'b3', concept: '', bidChange: -5.46, circulationMV: 50, bidAmt: 5000 }, null, { ...F, bidLt: '' }), true)
})

test('passLockedFilter: bidLt=-2 时竞价 -5.46 剔除, -1 保留', () => {
  const FL = { ...F, bidLt: -2 }
  assert.equal(passLockedFilter({ code: 'c1', concept: '', bidChange: -5.46, circulationMV: 50, bidAmt: 5000 }, null, FL), false)
  assert.equal(passLockedFilter({ code: 'c2', concept: '', bidChange: -1.0, circulationMV: 50, bidAmt: 5000 }, null, FL), true)
})

test('buildFilterParams: bidLt null 不传给后端(后端默认不限), 有值才传', () => {
  assert.equal('bidLt' in buildFilterParams({ ...F, bidLt: null }), false)
  assert.equal('bidLt' in buildFilterParams({ ...F }), false)   // 默认 null
  assert.equal(buildFilterParams({ ...F, bidLt: -2 }).bidLt, -2)
})
