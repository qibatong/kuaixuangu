// uiBus (全局交互总线) 单元测试
// 运行: node --test src/composables/uiBus.test.js
// 注意: vue 作为生产依赖, 其 ESM 入口在 package.json "type":"module" 下可直接 import
import { test } from 'node:test'
import assert from 'node:assert/strict'

// 用 dynamic import(因为路径相对 + Node 要能在 cwd=frontend 下解析 node_modules/vue)
const { uiBus, openStockChart, closeStockChart } = await import('./uiBus.js')

test('uiBus 初始状态: chartModal 不可见, 序列为 0', () => {
  assert.equal(uiBus.chartModal.visible, false)
  assert.equal(uiBus.chartModal.code, '')
  assert.equal(uiBus.chartModal.name, '')
  assert.equal(uiBus._seq, 0)
})

test('openStockChart: 非法 code 直接返回, 不修改状态', () => {
  const snap = JSON.stringify({ ...uiBus.chartModal, _seq: uiBus._seq })
  openStockChart('')
  openStockChart(null)
  openStockChart(undefined)
  openStockChart(0)
  assert.equal(JSON.stringify({ ...uiBus.chartModal, _seq: uiBus._seq }), snap,
    '空值/falsy code 不应改状态')
})

test('openStockChart: code 数字转 String, name 空串降级为字符串, visible=true', () => {
  // 先关一次再开, 避免测试顺序依赖
  closeStockChart()
  openStockChart(600001, '测试股份')
  assert.equal(uiBus.chartModal.visible, true)
  assert.equal(uiBus.chartModal.code, '600001')
  assert.equal(uiBus.chartModal.name, '测试股份')
  assert.equal(uiBus._seq, 1)

  // name=null → 空串
  openStockChart('000002', null)
  assert.equal(uiBus.chartModal.code, '000002')
  assert.equal(uiBus.chartModal.name, '')
  assert.equal(uiBus._seq, 2)  // _seq 每次开都递增, 保证 watch 被触发(连续点同一只也刷新弹框)
})

test('closeStockChart: visible 置为 false', () => {
  openStockChart('300003', '丙')
  assert.equal(uiBus.chartModal.visible, true)
  closeStockChart()
  assert.equal(uiBus.chartModal.visible, false)
  // close 不递减 seq
  const seqBefore = uiBus._seq
  closeStockChart()
  assert.equal(uiBus._seq, seqBefore)
})

test('连续点开同一只股票: _seq 递增保证 watch 可触发(前端弹框重新请求)', () => {
  closeStockChart()
  const s0 = uiBus._seq
  openStockChart('600001')
  const s1 = uiBus._seq
  openStockChart('600001')
  const s2 = uiBus._seq
  assert.equal(s1 - s0, 1)
  assert.equal(s2 - s1, 1)
})
