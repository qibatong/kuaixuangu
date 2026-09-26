// picks.js 单测（node --test，无需浏览器）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { limitPctOf, pickState, summarizePicks, sortByState, avgOf, LIMIT_SLACK } from './picks.js'

test('limitPctOf: 按板块给涨停幅度', () => {
  assert.equal(limitPctOf('600000', '浦发银行'), 10)
  assert.equal(limitPctOf('300750', '宁德时代'), 20)
  assert.equal(limitPctOf('301001', '某创业'), 20)
  assert.equal(limitPctOf('688981', '中芯国际'), 20)
  assert.equal(limitPctOf('830799', '某北交'), 30)
  assert.equal(limitPctOf('430047', '某北交'), 30)
  assert.equal(limitPctOf('600001', 'ST某某'), 5)
  assert.equal(limitPctOf('300001', 'ST创业'), 20)   // 创业板 ST 仍 20%
})

test('pickState: 封板/炸板/冲高/翻绿/微涨/未知', () => {
  assert.equal(pickState({ code: '600000', change: 10 }).key, 'limit')
  assert.equal(pickState({ code: '600000', change: 9.85 }).key, 'limit')   // 容差内仍算封板
  assert.equal(pickState({ code: '300750', change: 19.9 }).key, 'limit')
  // 有 peakChange 且触及过涨停、现在没封 → 炸板
  assert.equal(pickState({ code: '600000', change: 6.2, peakChange: 10 }).key, 'broken')
  // 无 peakChange → 只能判冲高，绝不臆断炸板
  assert.equal(pickState({ code: '600000', change: 6.2 }).key, 'high')
  assert.equal(pickState({ code: '600000', change: -3 }).key, 'down')
  assert.equal(pickState({ code: '600000', change: 1 }).key, 'flat')
  assert.equal(pickState({ code: '600000', change: null }).key, 'unknown')
  assert.equal(pickState(null).key, 'unknown')
})

test('pickState: 容差边界', () => {
  const just = 10 - LIMIT_SLACK
  assert.equal(pickState({ code: '600000', change: just }).key, 'limit')
  assert.equal(pickState({ code: '600000', change: just - 0.01 }).key, 'high')
})

test('summarizePicks: 剔除无涨幅样本且不给炸板编数', () => {
  const r = summarizePicks([
    { code: '600000', change: 10 },        // 封板
    { code: '300750', change: 20 },        // 封板(20cm)
    { code: '600001', change: -2 },        // 翻绿
    { code: '600002', change: 5 },         // 冲高
    { code: '600003', change: null },      // 无涨幅 → 不进样本
  ])
  assert.equal(r.count, 4)
  assert.equal(r.limitCount, 2)
  assert.equal(r.brokenCount, 0)
  assert.equal(r.brokenKnown, false)       // 数据源不给最高价 → 上层显示「—」
  assert.equal(r.upCount, 3)
  assert.equal(r.downCount, 1)
  assert.equal(r.avgChange, (10 + 20 - 2 + 5) / 4)
})

test('summarizePicks: 有最高价时炸板可判', () => {
  const r = summarizePicks([
    { code: '600000', change: 4.2, peakChange: 10.0 },
    { code: '600001', change: 1.1, peakChange: 3.0 },
  ])
  assert.equal(r.brokenKnown, true)
  assert.equal(r.brokenCount, 1)
  assert.equal(r.limitCount, 0)
})

test('summarizePicks: 空列表不崩、不产生 NaN 均值', () => {
  const r = summarizePicks([])
  assert.equal(r.count, 0)
  assert.equal(r.avgChange, null)
})

test('sortByState: 封板/炸板在前，翻绿在后', () => {
  const list = [
    { code: '600001', change: -3 },
    { code: '600002', change: 1 },
    { code: '600003', change: 10 },
    { code: '600004', change: 5, peakChange: 10 },
  ]
  assert.deepEqual(sortByState(list).map((x) => x.code), ['600003', '600004', '600002', '600001'])
})

test('avgOf: 剔除缺失值；全缺失返回 null（绝不兜成 0）', () => {
  assert.equal(avgOf([1, 2, 3]), 2)
  assert.equal(avgOf([1, null, 3, undefined, '', 'x']), 2)
  assert.equal(avgOf([]), null)
  assert.equal(avgOf(null), null)
  assert.equal(avgOf([null, undefined]), null)
  // 关键：0 是合法样本，不能被当成缺失
  assert.equal(avgOf([0, 0]), 0)
})
