// boards.js 单测（node --test，无需浏览器）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { normBoardName, mergeLimitCount, sortBoardsByLimit } from './boards.js'

test('normBoardName: 去括号/后缀/空白', () => {
  assert.equal(normBoardName('机器人概念'), '机器人')
  assert.equal(normBoardName('半导体（芯片）'), '半导体')
  assert.equal(normBoardName('光伏设备 板块'), '光伏设备')
  assert.equal(normBoardName('新能源Ⅱ'), '新能源')
  assert.equal(normBoardName(''), '')
  assert.equal(normBoardName(null), '')
})

test('mergeLimitCount: 精确 + 包含匹配都命中', () => {
  const boards = [
    { name: '机器人概念', change: 3.1, mainNet: 2e8 },
    { name: '半导体设备', change: 2.2, mainNet: 1e8 },
    { name: '无人问津板块', change: 0.1, mainNet: 1e7 },
  ]
  const counts = [
    { name: '机器人', count: 12 },
    { name: '半导体', count: 5 },
  ]
  const out = mergeLimitCount(boards, counts)
  assert.equal(out[0].limitCount, 12)   // 「机器人概念」→「机器人」精确(归一化后)
  assert.equal(out[1].limitCount, 5)    // 「半导体设备」→「半导体」包含匹配
  assert.equal(out[2].limitCount, null) // 匹配不上 → null（不是 0）
})

test('mergeLimitCount: 长名优先，不被短名抢走', () => {
  const boards = [{ name: '半导体设备', change: 1, mainNet: 1 }]
  const counts = [
    { name: '半导体', count: 100 },
    { name: '半导体设备', count: 7 },
  ]
  const out = mergeLimitCount(boards, counts)
  assert.equal(out[0].limitCount, 7)    // 精确匹配优先
})

test('mergeLimitCount: 不改入参、空输入安全', () => {
  const boards = [{ name: 'A', change: 1, mainNet: 1 }]
  const out = mergeLimitCount(boards, [])
  assert.equal(out[0].limitCount, null)
  assert.equal('limitCount' in boards[0], false, '不得污染入参')
  assert.deepEqual(mergeLimitCount(null, null), [])
})

test('sortBoardsByLimit: 涨停数降序、null 垫底', () => {
  const list = [
    { name: 'a', limitCount: null, mainNet: 9e8 },
    { name: 'b', limitCount: 3, mainNet: 1e8 },
    { name: 'c', limitCount: 8, mainNet: 1e7 },
  ]
  assert.deepEqual(sortBoardsByLimit(list).map((x) => x.name), ['c', 'b', 'a'])
})

test('sortBoardsByLimit: 同涨停数按主力净额降序', () => {
  const list = [
    { name: 'a', limitCount: 5, mainNet: 1e8 },
    { name: 'b', limitCount: 5, mainNet: 9e8 },
  ]
  assert.deepEqual(sortBoardsByLimit(list).map((x) => x.name), ['b', 'a'])
})
