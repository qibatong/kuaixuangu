// batches.js 单测（node --test，无需浏览器）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { pickReportBatch } from './batches.js'

const TODAY = '2026-09-28'

test('pickReportBatch: 今日有 ready 的 lock → 用 lock', () => {
  const r = pickReportBatch([
    { id: 3, batch_date: TODAY, action: 'lock', auto_applied: 0, freeze_ready: true, ts: 300 },
    { id: 2, batch_date: TODAY, action: 'auto', auto_applied: 1, freeze_ready: true, ts: 200 },
  ], TODAY)
  assert.equal(r.batch.id, 3)
  assert.equal(r.isToday, true)
  assert.equal(r.pending, false)
})

test('pickReportBatch: 今日只有 auto → 用 auto', () => {
  const r = pickReportBatch([
    { id: 2, batch_date: TODAY, action: 'auto', auto_applied: 1, freeze_ready: true, ts: 200 },
  ], TODAY)
  assert.equal(r.batch.id, 2)
  assert.equal(r.isToday, true)
})

test('🔴 今日批次未 freeze_ready → pending，绝不退回昨日冒充今日', () => {
  const r = pickReportBatch([
    { id: 9, batch_date: TODAY, action: 'lock', auto_applied: 0, freeze_ready: false, ts: 900 },
    { id: 5, batch_date: '2026-09-25', action: 'auto', auto_applied: 1, freeze_ready: true, ts: 500 },
  ], TODAY)
  assert.equal(r.batch, null)
  assert.equal(r.pending, true)
  assert.equal(r.isToday, true)
})

test('pickReportBatch: 无今日批次 → 退回最近交易日并标 isToday=false', () => {
  const r = pickReportBatch([
    { id: 5, batch_date: '2026-09-25', action: 'auto', auto_applied: 1, freeze_ready: true, ts: 500 },
    { id: 4, batch_date: '2026-09-24', action: 'lock', auto_applied: 0, freeze_ready: true, ts: 400 },
  ], TODAY)
  assert.equal(r.batch.id, 5)          // 日期更近的优先
  assert.equal(r.isToday, false)
  assert.equal(r.pending, false)
})

test('pickReportBatch: 最近交易日同日多批 → lock 优先于 auto', () => {
  const r = pickReportBatch([
    { id: 8, batch_date: '2026-09-25', action: 'auto', auto_applied: 1, freeze_ready: true, ts: 800 },
    { id: 7, batch_date: '2026-09-25', action: 'lock', auto_applied: 0, freeze_ready: true, ts: 700 },
  ], TODAY)
  assert.equal(r.batch.id, 7)
})

test('pickReportBatch: 空/脏输入安全', () => {
  assert.equal(pickReportBatch([], TODAY).batch, null)
  assert.equal(pickReportBatch(null, TODAY).batch, null)
  const r = pickReportBatch([{ batch_date: TODAY }, null], TODAY)
  assert.equal(r.batch, null, '没有 id 的行不算批次')
})
