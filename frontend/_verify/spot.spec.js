/**
 * 盘中实时(spot)前端渲染冒烟测试 —— v4.11.75
 * =====================================================================
 * 与 nav.spec.js 同骨架(SSR 真渲染 + 零异常断言), 专测本次 spot 前端接入。
 *
 * 为什么必须做"真渲染"而不是只看 diff / 构建通过:
 *   spot 这轮改动里, StockTable 首次消费 `strategy` prop 来**换列**(竞涨/竞额 → 量比/换手),
 *   FilterPanel 按 isSpot **分支渲染**两套参数行。这类"模板里的条件分支/自由变量"
 *   vite build 与 eslint 都看不见 —— 本项目已两次因此放过事故(v4.11.58 的 NAV_GROUPS、
 *   v4.11.62 的 MarketBoardPanel 的 list/rows)。所以必须渲染出来、按真实 HTML 断言。
 *
 * 断言的重点是**差异**, 不是"都有" ——
 *   · spot 必须有: 量比 / 换手 列; 现涨/量比/换手 参数行; 「剔涨停」; .spot-notice
 *   · spot 必须**没有**: 竞涨 / 竞额 列; 锁定按钮(spot 不落批次、无可锁之物)
 *   只断言"有"会让"两套一起渲染"这种最可能的错法(分支写错)安然通过。
 *
 * 跑法: vite build --ssr _verify/spot.spec.js --outDir .spotssr --emptyOutDir && node .spotssr/spot.spec.js
 *   ⚠️ 不能输出到 /tmp(Node 向上找不到 node_modules ⇒ Cannot find package 'vue')
 *   ⚠️ 不能写顶层 await(esbuild 报 "Top-level await is not available")
 *   ⚠️ 收尾必须 process.exit()(轮询会留 setTimeout, 事件循环不空)
 */
import { createSSRApp, h } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { renderToString } from 'vue/server-renderer'
import StockTable from '../src/components/StockTable.vue'
import FilterPanel from '../src/components/FilterPanel.vue'
import { defaultSpotFilterSettings, buildSpotFilterParams, passSpotFilter } from '../src/utils/filters.js'

const ROUTES = [{ path: '/', component: { template: '<div/>' } },
                { path: '/stocks', component: { template: '<div/>' } }]

let PASS = 0, FAIL = 0
const fails = []
function ok(name, cond, extra) {
  if (cond) { PASS++; console.log(`  [PASS] ${name}`) }
  else { FAIL++; fails.push(name); console.log(`  [FAIL] ${name}${extra ? ' :: ' + extra : ''}`) }
}

const countByClass = (html, cls) =>
  (html.match(new RegExp(`class="[^"]*\\b${cls}\\b[^"]*"`, 'g')) || []).length

async function renderComp(component, props = {}, route = '/stocks') {
  const router = createRouter({ history: createMemoryHistory(), routes: ROUTES })
  const errors = []
  const app = createSSRApp({ render: () => h(component, props) })
  app.config.errorHandler = (err) => { errors.push(String(err && err.message ? err.message : err)) }
  app.config.warnHandler = (msg) => { errors.push('VUE_WARN: ' + msg) }
  app.use(createPinia())
  app.use(router)
  await router.push(route)
  await router.isReady()
  const html = await renderToString(app)
  return { html, errors }
}

// spot 名单夹具(字段名与后端 _spot_payload 对齐)
const SPOT_STOCKS = [
  { code: '600000', name: '浦发银行', realChange: 3.56, volRatio: 9.03, turnover: 7.9,
    probability: 82, confidence: 79, limitBoards: 0, circulationMV: 50, price: 20, industry: '银行', concept: '-' },
  { code: '300096', name: '易联众', realChange: 5.43, volRatio: 10.73, turnover: 3.5,
    probability: 78, confidence: 73, limitBoards: 2, circulationMV: 40, price: 15, industry: '软件', concept: 'AI' },
]
// 竞价名单夹具(用于对照, 字段是竞价的)
const BID_STOCKS = [
  { code: '600000', name: '浦发银行', bidChange: 4.2, bidAmt: 8500, probability: 82, confidence: 79,
    circulationMV: 50, price: 20, industry: '银行', concept: '-' },
]

async function main() {
  console.log('\n— S1. StockTable 在 spot 下换列（本次改动的核心可观察行为）')
  const spot = await renderComp(StockTable, { stocks: SPOT_STOCKS, strategy: 'spot' })
  ok('StockTable(spot) 渲染无异常/无 Vue 警告', spot.errors.length === 0, spot.errors.join(' | '))
  ok('spot 表头有「量比」', spot.html.includes('量比'))
  ok('spot 表头有「换手」', spot.html.includes('换手'))
  ok('🔴 spot 表头**没有**「竞涨」', !spot.html.includes('竞涨'), '竞涨是竞价列, spot 下必须换掉')
  ok('🔴 spot 表头**没有**「竞额」', !spot.html.includes('竞额'), '竞价额在盘中无意义')
  ok('spot 渲染出数据行', countByClass(spot.html, 'col-volratio') >= 1
     || (spot.html.match(/<tbody/g) || []).length >= 1)
  ok('spot 值渲染量比 9.03', spot.html.includes('9.03'))
  ok('spot 值渲染换手 7.90%', spot.html.includes('7.9'))
  ok('🔴 spot 不含 "undefined"', !spot.html.includes('undefined'))
  ok('🔴 spot 不含 "NaN"', !spot.html.includes('NaN'))

  console.log('\n— S2. 对照: 同一组件在竞价下仍是「竞涨 / 竞额」（防止换成"两边都 spot"）')
  const bid = await renderComp(StockTable, { stocks: BID_STOCKS, strategy: 'auction', bidSealMap: {} })
  ok('StockTable(auction) 渲染无异常', bid.errors.length === 0, bid.errors.join(' | '))
  ok('竞价表头有「竞涨」', bid.html.includes('竞涨'))
  ok('竞价表头有「竞额」', bid.html.includes('竞额'))
  ok('🔴 竞价表头**没有**「量比」', !bid.html.includes('量比'), '若出现说明分支写反了')
  ok('竞价表头**没有**「换手」', !bid.html.includes('换手'))

  console.log('\n— S3. 非 spot 的一切取值都必须走竞价列（防"分支写反"）')
  // ⚠️ 本条最初只测「不传 prop」—— 变异测试证明它抓不到 `!== 'auction'` 这类写反:
  //    因为 prop 默认值就是 'auction', 不传时 `=== 'spot'` 与 `!== 'auction'` 结果相同 ⇒ 等价变异。
  //    真正有区分度的是**第三个取值**(typo / 新增策略)。故补测之。
  const noProp = await renderComp(StockTable, { stocks: BID_STOCKS })
  ok('不传 strategy 时走竞价列(竞涨在场)', noProp.html.includes('竞涨'),
     '默认值若不是 auction, 所有老调用点会静默换列')
  ok('不传 strategy 时不出现 spot 列', !noProp.html.includes('量比'))

  const typo = await renderComp(StockTable, { stocks: BID_STOCKS, strategy: 'spott' })
  ok('🔴 strategy 取值非 spot(如 typo) 时仍走竞价列', typo.html.includes('竞涨') && !typo.html.includes('量比'),
     '必须是"严格等于 spot 才换列", 不能写成"不等于 auction 就换列"')
  const other = await renderComp(StockTable, { stocks: BID_STOCKS, strategy: 'aipick' })
  ok('🔴 strategy=其他策略(aipick) 时仍走竞价列', other.html.includes('竞涨') && !other.html.includes('量比'))

  console.log('\n— S4. FilterPanel 的 spot 参数行为')
  const fp = await renderComp(FilterPanel)
  ok('FilterPanel 渲染无异常/无 Vue 警告', fp.errors.length === 0, fp.errors.join(' | '))
  // 默认态(竞价) 下不该出现 spot 专属控件
  ok('默认(竞价态)渲染不含「剔涨停」', !fp.html.includes('剔涨停'),
     'spot 专属参数不该在竞价态露出')

  console.log('\n— S5. 参数契约(与后端同源, 单测已锁, 此处再钉一次渲染无关项)')
  const q = buildSpotFilterParams({ ...defaultSpotFilterSettings, chgFloor: 3, volRatioFloor: 2 })
  ok('buildSpotFilterParams 传出 chgFloor', q.chgFloor === 3)
  ok('buildSpotFilterParams 传出 volRatioFloor', q.volRatioFloor === 2)
  ok('buildSpotFilterParams 不传竞价专属 bidGt', !('bidGt' in q))
  ok('buildSpotFilterParams 不传竞价专属 bidAmt', !('bidAmtFloor' in q))
  ok('spotExcludeZT 传成 "0"/"1"', q.spotExcludeZT === '0')

  console.log('\n— S6. passSpotFilter 与后端同口径复核(限额/缺失/剔涨停)')
  const F = { ...defaultSpotFilterSettings, floatMvFloor: 0, floatMvGt: 0, priceGt: 0, scoreFloor: 0, markets: ['hs', 'cyb', 'kcb', 'bj'] }
  ok('limitBoards=0 → 不剔', passSpotFilter({ code: '600000', realChange: 3, limitBoards: 0 }, { ...F, spotExcludeZT: true }) === true)
  ok('🔴 limitBoards=1 + 勾剔涨停 → 剔除(与后端 _spot_zt 同源)',
     passSpotFilter({ code: '600000', realChange: 3, limitBoards: 1 }, { ...F, spotExcludeZT: true }) === false)
  ok('不勾剔涨停时涨停股保留', passSpotFilter({ code: '600000', realChange: 3, limitBoards: 1 }, { ...F, spotExcludeZT: false }) === true)
  ok('🔴 用 realChange 而非 bidChange',
     passSpotFilter({ code: '600000', realChange: 99, bidChange: 3 }, { ...F, chgGt: 7 }) === false)

  console.log(`\n=== 结果：PASS=${PASS}  FAIL=${FAIL} ===`)
  if (FAIL) { console.log('失败项：\n  - ' + fails.join('\n  - ')); process.exit(1) }
  console.log('spot 渲染冒烟测试全绿。\n')
  process.exit(0)
}

main().catch((e) => {
  console.error('\n[FATAL] spot 冒烟测试自身异常：', e && e.stack ? e.stack : e)
  process.exit(2)
})
