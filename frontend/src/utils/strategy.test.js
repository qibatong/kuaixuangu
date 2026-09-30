// tab(leftTab) → 引擎(strategy) 映射单测(node:test 零依赖)
// 运行: node --test src/utils/strategy.test.js   (或 npm test 跑 src/utils/*.test.js)
//
// 🔴 本文件是 2026-09-30「竞价选股不出数据」事故的**防复发哨兵**。
//   事故形态：旧映射写成白名单式 `aipick*/yijiner/zhpick → auction`, **其余 → spot**,
//   而白名单**漏掉了 `'auction'` 自己** ⇒ 点「竞价选股」拿到 strategy='spot'
//   ⇒ 面板隐藏「竞涨/竞额」、取数走 spot 分支(生产实测仅 1~3 只)。
//   最阴的地方：tab2 的值 `'spot'` 与旧兜底值**同名** ⇒ 它歪打正着、完全正常，
//   于是故障只出现在 tab1，看起来像"tab1 特有的怪问题"。
//
//   ⚠️ 谁把 strategyForTab 改回"白名单式"，或把兜底改回 'spot'，本文件必红。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { strategyForTab, TAB_AUCTION, TAB_SPOT } from './strategy.js'


test('★ tab「竞价选股」必须走**竞价引擎**(本事故的核心断言)', () => {
  assert.equal(strategyForTab('auction'), TAB_AUCTION,
    '竞价选股 => auction 引擎；给 spot 会让面板隐藏竞涨/竞额并走实时取数')
})


test('tab「实时动态选股」走 spot 引擎', () => {
  assert.equal(strategyForTab('spot'), TAB_SPOT)
})


test('不读 store.strategy 的 tab 一律归 auction(不留脏 spot 值给面板)', () => {
  for (const m of ['aipick', 'aipick_lgb', 'yijiner', 'zhpick']) {
    assert.equal(strategyForTab(m), TAB_AUCTION, `${m} 应归 auction`)
  }
})


test('未知/空 tab 兜底 auction(中性值, 不制造"以为在盘中模式"的错配)', () => {
  for (const m of ['', null, undefined, 'nope', 'AUCTION', 'Spot']) {
    assert.equal(strategyForTab(m), TAB_AUCTION, `${String(m)} 应兜底 auction`)
  }
})


test("穷举全部真实 tab: **只有 'spot' 一个**走 spot 引擎", () => {
  // 与 StockView.vue 的 6 个 mode-tab 一一对应(见该文件 tab 栏)
  const ALL_TABS = ['auction', 'spot', 'aipick', 'aipick_lgb', 'yijiner', 'zhpick']
  const spotTabs = ALL_TABS.filter((t) => strategyForTab(t) === TAB_SPOT)
  assert.deepEqual(spotTabs, ['spot'],
    'spot 引擎只应由「实时动态选股」使用；多出任何一项都意味着又把某个 tab 错配了')
})


test('回归哨兵: 旧"白名单式"写法(其余 → spot)能被本文件抓住', () => {
  // 复刻旧实现，证明本组断言确实能分辨新旧两种映射
  const STRATEGY_NEUTRAL_TABS = ['aipick', 'aipick_lgb', 'yijiner', 'zhpick']
  const legacy = (m) => (STRATEGY_NEUTRAL_TABS.includes(m) ? 'auction' : 'spot')
  assert.equal(legacy('auction'), 'spot', '旧实现确实把 auction 映射成 spot')
  assert.notEqual(legacy('auction'), strategyForTab('auction'),
    '新旧实现在 auction 上必须不同 —— 否则说明修复没生效')
  // 但两者在 'spot' 上相同(这正是旧 bug 只影响 tab1 的原因)
  assert.equal(legacy('spot'), strategyForTab('spot'))
})
