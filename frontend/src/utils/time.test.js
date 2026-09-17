// time.js 纯函数单测(node:test 零依赖 ESM 版)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { pad2, fmtDate, fmtTsDate, todayBj, fmtTsTime, fmtBjDay } from './time.js'

test('pad2: 补零', () => {
  assert.equal(pad2(5), '05')
  assert.equal(pad2(12), '12')
  assert.equal(pad2(0), '00')
})

test('fmtDate: 日期格式化 YYYY-MM-DD', () => {
  assert.equal(fmtDate(new Date(2026, 7, 15)), '2026-08-15')
  assert.equal(fmtDate(new Date(2026, 0, 5)), '2026-01-05')
})

test('fmtTsDate: 时间戳转日期', () => {
  // 2026-08-15T00:00:00Z = 1784160000? 用精确值: 2026-08-15 00:00 UTC
  const ts = Date.UTC(2026, 7, 15) / 1000
  assert.equal(fmtTsDate(ts), '2026-08-15')
})

test('todayBj: 返回 YYYY-MM-DD 格式', () => {
  assert.match(todayBj(), /^\d{4}-\d{2}-\d{2}$/)
})

test('fmtTsTime: 秒级时间戳(北京时间) → YYYY-MM-DD HH:MM', () => {
  // UTC 01:30 = 北京时间 09:30
  const ts = Date.UTC(2026, 7, 15, 1, 30) / 1000
  assert.equal(fmtTsTime(ts), '2026-08-15 09:30')
  assert.equal(fmtTsTime(0), '-')
  assert.equal(fmtTsTime('2026-08-15'), '2026-08-15')  // 非时间戳原样返回
})

test('fmtBjDay: 秒级时间戳(北京时间) → YYYY-MM-DD', () => {
  const ts = Date.UTC(2026, 7, 15, 1, 30) / 1000
  assert.equal(fmtBjDay(ts), '2026-08-15')
  assert.equal(fmtBjDay(0), '-')
})
