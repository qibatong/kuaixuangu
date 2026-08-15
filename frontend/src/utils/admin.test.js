// admin.js 纯函数单测(node:test 零依赖 ESM 版)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { expireState } from './admin.js'

test('expireState: 无 expire_at → forever', () => {
  assert.equal(expireState({}), 'forever')
  assert.equal(expireState({ expire_at: 0 }), 'forever')
  assert.equal(expireState(null), 'forever')
})

test('expireState: 已过期 → expired', () => {
  assert.equal(expireState({ expire_at: 1 }), 'expired')  // 1970 年早已过期
})

test('expireState: 未过期 → active', () => {
  const future = Math.floor(Date.now() / 1000) + 86400 * 30
  assert.equal(expireState({ expire_at: future }), 'active')
})
