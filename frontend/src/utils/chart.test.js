// 图表工具函数单测(StockChartModal 抽离出来的格式化 helpers)
// 运行: node --test src/utils/chart.test.js
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { fmtNum, fmtVol, fmtVolShort, fmtPct } from './chart.js'

// ---------- fmtNum ----------
test('fmtNum: 2位小数, 空/null/空串 转 "-"', () => {
  assert.equal(fmtNum(3), '3.00')
  assert.equal(fmtNum(3.14159), '3.14')
  assert.equal(fmtNum(-1.995), '-2.00')  // Math.round 行为
  assert.equal(fmtNum(0), '0.00')
  assert.equal(fmtNum('12.5'), '12.50')   // 字符串数值兼容
  assert.equal(fmtNum(null), '-')
  assert.equal(fmtNum(undefined), '-')
  assert.equal(fmtNum(''), '-')
})

// ---------- fmtVol (长格式 2位小数) ----------
test('fmtVol: 四档(万亿/亿/万/整数)', () => {
  assert.equal(fmtVol(null), '-')
  assert.equal(fmtVol(''), '-')
  assert.equal(fmtVol(0), '0')
  // 万档: 1e4 ≤ |n| < 1e8
  assert.equal(fmtVol(10000), '1.00万')
  assert.equal(fmtVol(123456), '12.35万')
  assert.equal(fmtVol(-29999), '-3.00万')
  // 亿档: 1e8 ≤ |n| < 1e12
  assert.equal(fmtVol(1e8), '1.00亿')
  assert.equal(fmtVol(123456789), '1.23亿')
  assert.equal(fmtVol(999_999_999), '10.00亿')
  // 万亿档: ≥ 1e12
  assert.equal(fmtVol(1e12), '1.00万亿')
  assert.equal(fmtVol(1_500_000_000_000), '1.50万亿')
  // 小数值
  assert.equal(fmtVol(99.9), '100')
  assert.equal(fmtVol(9999), '9999')
  // NaN / Infinity 兼容
  assert.equal(fmtVol(NaN), '-')
  assert.equal(fmtVol(Infinity), '-')
})

// ---------- fmtVolShort (轴标签, 1位小数) ----------
test('fmtVolShort: 1位小数轴标签', () => {
  assert.equal(fmtVolShort(12345), '1.2万')
  assert.equal(fmtVolShort(98765432), '9876.5万')   // < 1e8 仍用万
  assert.equal(fmtVolShort(123_000_000), '1.2亿')
  assert.equal(fmtVolShort(0), '0')
  assert.equal(fmtVolShort(-5_000_000_000), '-50.0亿')
})

// ---------- fmtPct ----------
test('fmtPct: 正加+号, 2位小数, 空值 → "-"', () => {
  assert.equal(fmtPct(0), '0.00%')
  assert.equal(fmtPct(3.14), '+3.14%')
  assert.equal(fmtPct(-10.001), '-10.00%')
  assert.equal(fmtPct(null), '-')
  assert.equal(fmtPct(undefined), '-')
  assert.equal(fmtPct(''), '-')
  assert.equal(fmtPct('abc'), '-')   // NaN
  assert.equal(fmtPct(NaN), '-')
})
