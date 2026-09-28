/**
 * 盘中实时(spot)前端渲染冒烟测试 —— v4.11.75 建立 / v4.11.77 改版
 * =====================================================================
 * 与 nav.spec.js 同骨架(SSR 真渲染 + 零异常断言), 专测 spot 前端接入与列定义。
 *
 * 为什么必须做"真渲染"而不是只看 diff / 构建通过:
 *   spot 这轮改动里, StockTable 消费 `strategy` prop 来**换列**, FilterPanel 按 isSpot
 *   **分支渲染**两套参数行。这类"模板里的条件分支/自由变量"vite build 与 eslint 都看不见
 *   —— 本项目已两次因此放过事故(v4.11.58 的 NAV_GROUPS、v4.11.62 的 MarketBoardPanel)。
 *   所以必须渲染出来、按真实 HTML 断言。
 *
 * 断言的重点是**差异**, 不是"都有" —— 只断言"有"会让"两套一起渲染"这种最可能的错法
 * (分支写错)安然通过。
 *
 * v4.11.77 列定义(主人指令: 去掉实体涨幅/量比/3日20%异动提示, 增加竞价涨幅/竞价金额):
 *   · spot 必须有: 竞涨 / 竞额 / 换手 列; 「剔涨停」; .spot-notice
 *   · spot 必须**没有**: 量比 / 实体 列; 名称格内红/黄「异动风险」徽章; 锁定按钮
 *   · 竞价必须有: 竞涨 / 实体 / 竞额; 必须没有: 量比 / 换手 / spot 专属控件
 *   ⚠️ 「竞涨/竞额」现在**两态都有**, 故不能再用它区分两态! 区分点改为
 *      「实体(仅竞价)」vs「换手(仅 spot)」+ 「量比(两态都无)」。
 *
 * 跑法: vite build --ssr _verify/spot.spec.js --outDir .spotssr --emptyOutDir && node .spotssr/spot.spec.js
 *   ⚠️ 不能输出到 /tmp(Node 向上找不到 node_modules ⇒ Cannot find package 'vue')
 *   ⚠️ 不能写顶层 await(esbuild 报 "Top-level await is not available")
 *   ⚠️ 收尾必须 process.exit()(轮询会留 setTimeout, 事件循环不空)
 *   ⚠️ 断言类名要用 countByClass, 不能用 html.includes('class="ss-row') ——
 *      Vue SSR 合并 :class 时**动态类排在静态类之前**(实测 'class="active factor-tab"')
 */
import { createSSRApp, h } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { renderToString } from 'vue/server-renderer'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import StockTable from '../src/components/StockTable.vue'
import FilterPanel from '../src/components/FilterPanel.vue'
import { defaultSpotFilterSettings, buildSpotFilterParams, passSpotFilter } from '../src/utils/filters.js'

// ★ v4.11.77 新增: StockTable.vue **源码全文**。
//   为什么需要它 —— 「去掉 3 日 20% 异动提示」这条改动, 用 SSR 渲染**测不出来**:
//   该徽章的数据源 useDevWarn 在 onMounted 里拉取, SSR 不跑 onMounted ⇒ warnMap 恒空
//   ⇒ devWarnLabel() 恒返回 '' ⇒ 无论 v-if 写成什么, HTML 里都不会有徽章。
//   (变异测试实测: 把 '!isSpot && devWarnLabel(...)' 改回 'devWarnLabel(...)',
//    渲染断言 PASS=33 FAIL=0 **全绿** —— 典型假绿。)
//   唯一能观测到的手段 = 直接核**模板里的 v-if 表达式**。故此处读源码做静态断言。
const __dirname = dirname(fileURLToPath(import.meta.url))
const TABLE_SRC = readFileSync(resolve(__dirname, '../src/components/StockTable.vue'), 'utf8')
// ★ v4.11.80 第三步: StockView.vue / stores/stocks.js 源码全文 —— 同理由。
//   tab1「AI选股」改走 spot 引擎后, "哪个分支读 spotStocks / 哪个分支送 strategy=spot"
//   这类**模板/条件分支**在 SSR 覆盖不到(tab 由 leftTab ref 驱动, SSR 里恒为初始值
//   'auction' ⇒ 渲染的是 tab1 那支, 但 store 的 strategy 也是初始 'auction' ⇒
//   即便模板写死 'auction' 也会全绿)。只能静态核表达式。
const VIEW_SRC = readFileSync(resolve(__dirname, '../src/views/StockView.vue'), 'utf8')
const STORE_SRC = readFileSync(resolve(__dirname, '../src/stores/stocks.js'), 'utf8')

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

// spot 名单夹具(字段名与后端 stocks_spot._spot_payload 对齐)
// ★ v4.11.77: 补 bidChange/bidAmt —— 后端**本就下发**这两项(SpotPayload 第 83/84 行),
//   之前只是前端不展示; 本次加列后夹具必须带上, 否则测不出"值真渲染出来了"。
const SPOT_STOCKS = [
  { code: '600000', name: '浦发银行', realChange: 3.56, bidChange: 2.1, bidAmt: 8500,
    volRatio: 9.03, turnover: 7.9, entityChange: 0.9,
    probability: 82, confidence: 79, limitBoards: 0, circulationMV: 50, price: 20, industry: '银行', concept: '-' },
  { code: '300096', name: '易联众', realChange: 5.43, bidChange: 4.8, bidAmt: 26000,
    volRatio: 10.73, turnover: 3.5, entityChange: 2.2,
    probability: 78, confidence: 73, limitBoards: 2, circulationMV: 40, price: 15, industry: '软件', concept: 'AI' },
]
// 竞价名单夹具(用于对照, 字段是竞价的)
const BID_STOCKS = [
  { code: '600000', name: '浦发银行', bidChange: 4.2, bidAmt: 8500, entityChange: 1.3,
    probability: 82, confidence: 79,
    circulationMV: 50, price: 20, industry: '银行', concept: '-' },
]

async function main() {
  console.log('\n— S1. StockTable 在 spot 下的列定义（本次改动的核心可观察行为）')
  const spot = await renderComp(StockTable, { stocks: SPOT_STOCKS, strategy: 'spot' })
  ok('StockTable(spot) 渲染无异常/无 Vue 警告', spot.errors.length === 0, spot.errors.join(' | '))
  // v4.11.77: spot 增加「竞涨 / 竞额」
  ok('spot 表头有「竞涨」', spot.html.includes('竞涨'))
  ok('spot 表头有「竞额」', spot.html.includes('竞额'))
  ok('spot 表头有「换手」', spot.html.includes('换手'))
  // v4.11.77: spot 去掉「实体」「量比」
  ok('🔴 spot 表头**没有**「实体」', !spot.html.includes('实体'), '实体是竞价语义(开→收), spot 必须去掉')
  ok('🔴 spot 表头**没有**「量比」', !spot.html.includes('量比'), '主人要求精简, 量比不再占列')
  ok('spot 渲染出数据行', countByClass(spot.html, 'col-turnover') >= 1
     || (spot.html.match(/<tbody/g) || []).length >= 1)
  // v4.11.77: spot 值必须真渲染竞涨/竞额(不只是表头)
  ok('🔴 spot 值渲染竞涨 2.10%', spot.html.includes('2.1'))
  ok('🔴 spot 值渲染竞额 8500', spot.html.includes('8500'))
  ok('spot 值渲染换手 7.90%', spot.html.includes('7.9'))
  ok('🔴 spot 不含 "undefined"', !spot.html.includes('undefined'))
  ok('🔴 spot 不含 "NaN"', !spot.html.includes('NaN'))

  console.log('\n— S1b. 「3 日 20% 异动提示」必须 spot 态不渲染（⚡ 只能静态核源码，见文件头说明）')
  // 渲染层测不到(SSR 不跑 onMounted ⇒ warnMap 恒空 ⇒ 徽章恒不出现),
  // 故这里直接断言模板里的 v-if 表达式**含 !isSpot**。
  // 🔴 反过来也要防「两态一起关掉」—— 竞价必须保留, 用"表达式里出现 !isSpot"这一个事实同时锁住两件事:
  //    出现 !isSpot ⇒ spot 不显示 + 竞价显示(因为 !isSpot 在竞价态为 true)。
  //    ⚠️ 但 `!false` 这种恒真写法也能骗过本条 —— 故同时断言该表达式**确实引用了 isSpot 变量**。
  const devWarnVif = (TABLE_SRC.match(/v-if="([^"]*devWarnLabel[^"]*)"/) || [])[1] || ''
  ok('🔴 异动徽章 v-if 表达式存在', !!devWarnVif, '未匹配到 v-if="...devWarnLabel..."')
  ok('🔴 异动徽章 v-if **引用 isSpot**(证明按策略分叉)', /\bisSpot\b/.test(devWarnVif),
     '实际: ' + devWarnVif)
  ok('🔴 异动徽章在 spot 态被关掉(!isSpot 前置)', /!\s*isSpot\b/.test(devWarnVif) || /isSpot\s*===?\s*false/.test(devWarnVif),
     '实际: ' + devWarnVif)
  ok('🔴 异动徽章仍然依赖 devWarnLabel(竞价侧功能未被误删)', devWarnVif.includes('devWarnLabel'))
  // 防"锚点漂移": devWarnLabel 在模板里**恰好 2 次**(v-if 条件 1 次 + 插值 1 次)。
  // 若第三处消费点出现(如别处又渲染该徽章), 本条会红 —— 提示需同步复核本段假设。
  const devWarnUses = (TABLE_SRC.match(/devWarnLabel\(/g) || []).length
  ok('🔴 devWarnLabel 在模板中恰好 2 处(v-if 条件 + 插值)', devWarnUses === 2,
     '实际出现 ' + devWarnUses + ' 次 —— 若新增消费点需同步复核本段断言')

  console.log('\n— S2. 对照: 同一组件在竞价下「竞涨 / 实体 / 竞额」（防止换成"两边都 spot"）')
  const bid = await renderComp(StockTable, { stocks: BID_STOCKS, strategy: 'auction', bidSealMap: {} })
  ok('StockTable(auction) 渲染无异常', bid.errors.length === 0, bid.errors.join(' | '))
  ok('竞价表头有「竞涨」', bid.html.includes('竞涨'))
  ok('竞价表头有「实体」(spot 已去掉)', bid.html.includes('实体'))
  ok('竞价表头有「竞额」', bid.html.includes('竞额'))
  ok('🔴 竞价表头**没有**「量比」', !bid.html.includes('量比'), '量比两态都已移除')
  ok('🔴 竞价表头**没有**「换手」', !bid.html.includes('换手'), '换手是 spot 专属(竞价无实时换手)')

  console.log('\n— S3. 非 spot 的一切取值都必须走竞价列（防"分支写反"）')
  // ⚠️ 本条最初只测「不传 prop」—— 变异测试证明它抓不到 `!== 'auction'` 这类写反:
  //    因为 prop 默认值就是 'auction', 不传时 `=== 'spot'` 与 `!== 'auction'` 结果相同 ⇒ 等价变异。
  //    真正有区分度的是**第三个取值**(typo / 新增策略)。故补测之。
  // ★ v4.11.77: 区分点由「竞涨」改为「实体 / 换手」—— 竞涨两态都有, 不再有区分度。
  const noProp = await renderComp(StockTable, { stocks: BID_STOCKS })
  ok('不传 strategy 时走竞价列(实体在场 / 换手不在)', noProp.html.includes('实体') && !noProp.html.includes('换手'),
     '默认值若不是 auction, 所有老调用点会静默换列')
  ok('不传 strategy 时不出现 spot 专属「换手」', !noProp.html.includes('换手'))

  const typo = await renderComp(StockTable, { stocks: BID_STOCKS, strategy: 'spott' })
  ok('🔴 strategy 取值非 spot(如 typo) 时仍走竞价列',
     typo.html.includes('实体') && !typo.html.includes('换手'),
     '必须是"严格等于 spot 才换列", 不能写成"不等于 auction 就换列"')
  const other = await renderComp(StockTable, { stocks: BID_STOCKS, strategy: 'aipick' })
  ok('🔴 strategy=其他策略(aipick) 时仍走竞价列',
     other.html.includes('实体') && !other.html.includes('换手'))

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

  console.log('\n— S7. tab1「AI选股」走 spot 引擎（v4.11.80 第三步 · ⚡ 静态核源码）')
  // 背景: 主人需求「AI竞价出来数据就锁定」—— tab1 保留锁定语义, 但引擎换成 spot。
  // 可观察契约三件: ① StockView 里 tab1 分支的 StockTable 按 strategy 分派数据源
  //   ② switchTab 把 tab1 映射到 'spot'  ③ 闸门/定格标注条只在 auction 策略下生效。
  // SSR 覆盖不到(初始 strategy 恒 'auction'), 故静态核。
  const hasSpotBranch = /stocks\.isSpotStrategy[\s\S]{0,400}?strategy="spot"/.test(VIEW_SRC)
  ok('🔴 tab1 分支按 isSpotStrategy 分派到 spotStocks + strategy="spot"', hasSpotBranch,
     '未在 StockView 模板中匹配到 isSpotStrategy→spotStocks/strategy="spot" 分派')
  ok('🔴 tab1 分支仍保留 auction 侧的 cachedStocks/strategy="auction" 分派',
     /strategy="auction"/.test(VIEW_SRC))
  // switchTab 映射: aipick ≠ spot, 其余(auction/spot tab) = spot
  const switchFn = (VIEW_SRC.match(/function switchTab[\s\S]*?\n\}/) || [''])[0]
  ok('🔴 switchTab 把 tab1(AI选股) 映射为 spot',
     /aipick[\s\S]{0,80}?\?\s*'auction'\s*:\s*'spot'/.test(switchFn),
     '实际: ' + switchFn.replace(/\s+/g, ' ').slice(0, 200))
  ok('🔴 switchTab 里 aipick 两 tab 显式归 auction(不留 spot 脏值)', /aipick_lgb/.test(switchFn))
  // 定格标注条必须仅在 auction 策略下出现(spot 无"上一交易日定格"概念)
  ok('🔴 freeze-notice 仅在 !isSpotStrategy 时出现',
     /!stocks\.isSpotStrategy[\s\S]{0,120}?freeze-notice/.test(VIEW_SRC)
     || /isSpotStrategy[\s\S]{0,200}?![\s\S]{0,40}?freeze-notice/.test(VIEW_SRC),
     '冻结标注条若在 spot 态露出, 会让用户误以为实时名单是"上一交易日定格"')

  console.log('\n— S8. store 侧策略契约（v4.11.80 第三步 · ⚡ 静态核源码）')
  ok('🔴 store 导出 isSpotStrategy getter', /isSpotStrategy\s*\(\s*\)\s*\{\s*return\s+this\.strategy\s*===\s*\'spot\'/.test(STORE_SRC))
  ok('🔴 buildActiveFilterParams 按策略分派参数', /buildActiveFilterParams\s*\(\s*\)[\s\S]{0,160}?isSpotStrategy[\s\S]{0,80}?buildSpotFilterParams/.test(STORE_SRC))
  // fetchAndCache 必须把 strategy 透传给 API(否则后端永远按 auction 算)
  ok('🔴 fetchAndCache 把 strategy 传给 fetchStocks',
     /fetchStocks\(\s*action\s*,\s*this\.buildActiveFilterParams\(\)\s*,\s*strategy/.test(STORE_SRC))
  ok('🔴 fetchAndCache 里 strategy 不再写死 auction', !/=\s*'auction'\s*\/\/.*strategy/.test(STORE_SRC) && /const strategy = this\.strategy/.test(STORE_SRC))
  // spot 结果必须落 spotStocks, 不回填 cachedStocks
  ok('🔴 fetchAndCache 的 spot 分支写 spotStocks 而非 cachedStocks',
     /if\s*\(\s*strategy\s*===\s*'spot'\s*\)\s*\{[\s\S]{0,300}?this\.spotStocks\s*=/.test(STORE_SRC))
  // freeze_ready 字段名订正(本轮修的既有 bug)
  ok('🔴 loadLockedBatchFromServer 读 freeze_ready(snake, 后端真实字段名)',
     /x\.freeze_ready\s*===\s*true/.test(STORE_SRC),
     'x.freezeReady(camel) 恒 undefined ⇒ 该函数恒返回 [], 前端一直回退本地快照')
  ok('🔴 freezeReady 判据保留 camel 兼容读法(后端若改口径不静默失效)',
     /x\.freezeReady\s*===\s*true/.test(STORE_SRC))
  // 批次策略区分(勘察风险3)
  ok('🔴 批次按 _strategy 区分, 旧批次兼容为 auction',
     /_strategy\s*\|\|\s*'auction'/.test(STORE_SRC) && /batchStrategy\s*\(x\)\s*===\s*wantStrategy/.test(STORE_SRC))

  console.log(`\n=== 结果：PASS=${PASS}  FAIL=${FAIL} ===`)
  if (FAIL) { console.log('失败项：\n  - ' + fails.join('\n  - ')); process.exit(1) }
  console.log('spot 渲染冒烟测试全绿。\n')
  process.exit(0)
}

main().catch((e) => {
  console.error('\n[FATAL] spot 冒烟测试自身异常：', e && e.stack ? e.stack : e)
  process.exit(2)
})
