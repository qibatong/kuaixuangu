// storage.js 单测(node:test 零依赖 ESM 版)
import { test, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { safeJsonGet, safeJsonSet, safeRemove } from './storage.js'

// node 环境无 localStorage → mock
function makeStore() {
  const m = new Map()
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => { m.set(k, String(v)) },
    removeItem: (k) => { m.delete(k) },
  }
}

beforeEach(() => {
  globalThis.localStorage = makeStore()
})

test('safeJsonSet/safeJsonGet: 对象往返', () => {
  assert.equal(safeJsonSet('k', { a: 1, b: [2, 3] }), true)
  assert.deepEqual(safeJsonGet('k'), { a: 1, b: [2, 3] })
})

test('safeJsonGet: 不存在返回 fallback', () => {
  assert.equal(safeJsonGet('missing'), null)
  assert.deepEqual(safeJsonGet('missing', []), [])
})

test('safeJsonGet: 坏 JSON 返回 fallback 不抛', () => {
  localStorage.setItem('bad', '{invalid')
  assert.equal(safeJsonGet('bad', 'fb'), 'fb')
})

test('safeRemove: 删除后读不到, 幂等', () => {
  safeJsonSet('k', 1)
  assert.equal(safeRemove('k'), true)
  assert.equal(safeJsonGet('k'), null)
  assert.equal(safeRemove('k'), true)   // 再次删除不抛
})

test('safeJsonSet: 配额/禁用异常不抛, 返回 false', () => {
  localStorage.setItem = () => { throw new Error('QuotaExceeded') }
  assert.equal(safeJsonSet('k', 'x'), false)
})
