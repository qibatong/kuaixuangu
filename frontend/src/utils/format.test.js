// 纯函数单测(node:test 零依赖 ESM 版, 运行: node --test src/utils/format.test.js)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { yi, signed, amtText, fmtAvg, fmtT, wan, pct } from './format.js'

test('yi: 元转亿(2位)', () => {
  assert.equal(yi(1e8), '1.00')
  assert.equal(yi(5.55e9), '55.50')
  assert.equal(yi(0), '0.00')
})

test('signed: 正数加+号, 负数保留符号', () => {
  assert.equal(signed(3.21), '+3.21')
  assert.equal(signed(-1.5), '-1.50')
  assert.equal(signed(0), '0.00')
  // Number 转换容错(字符串输入)
  assert.equal(signed('10.01'), '+10.01')
})

test('amtText: 金额自适应 亿/万', () => {
  assert.equal(amtText(2e8), '2.00亿')
  assert.equal(amtText(5e4), '5万')
  assert.equal(amtText(0), '-')
  assert.equal(amtText(null), '-')
})

test('fmtAvg: 涨跌幅带符号百分比', () => {
  assert.equal(fmtAvg(5.5), '+5.50%')
  assert.equal(fmtAvg(-2.25), '-2.25%')
  assert.equal(fmtAvg(null), '-')
})

test('fmtT: 秒级时间戳转 HH:MM(北京时间)', () => {
  // 2026-08-15T09:30:00Z + 8h = 17:30
  const ts = 1786786200
  assert.equal(fmtT(ts), '17:30')
  assert.equal(fmtT(0), '-')
})

test('wan: 取整', () => {
  assert.equal(wan(123.7), '124')
  assert.equal(wan(0), '0')
  assert.equal(wan(null), '0')
})

test('pct: 百分比拼接', () => {
  assert.equal(pct(3.14159), '+3.14%')
  assert.equal(pct(-1), '-1.00%')
  assert.equal(pct(undefined), '-')
})
