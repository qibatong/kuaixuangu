/**
 * 板块题材面板「实时刷新」冒烟测试 —— v4.11.79 建立
 * =====================================================================
 * 背景(主人报障): MarketView 层⑤「板块题材」面板数据不实时更新。
 * 排查结论 —— 后端正常(直连上游、无缓存), **前端缺轮询**, 缺两层:
 *   ① MarketBoardPanel 的**右栏成分股**只在 @click=select() 时拉一次, 之后**永不刷新**
 *      ⇒ 点开某板块后看到的永远是"那一刻的快照"(截图症状就是这个)。
 *   ② 父组件 switchSrc 写 `if (s==='em' && !conceptList.length) loadConcept()`
 *      ⇒ 「东财」tab 有缓存就不再重拉, 切回来看到的是很久以前的榜。
 *
 * 为什么必须做"真渲染 + 源码静态断言"双轨:
 *   轮询是 usePolling(60s) 里的 setTimeout —— **SSR 一次性同步渲染不跑定时器**,
 *   故"有没有轮询""轮询里有没有门禁"这些事实**渲染层完全观测不到**(v4.11.77 已踩过
 *   同类假绿的坑: 数据源在 onMounted 的条件渲染, 纯渲染断言测不到)。
 *   唯一可靠手段 = 读**源码**核关键表达式, 再用真渲染核"组件仍能正常渲染"。
 *
 * 断言的核心不是"有 usePolling"(那太弱, 门禁写错也过), 而是:
 *   · 轮询回调里**必须**有 isIntradayNow 门禁(否则凌晨挂着也在打上游)
 *   · 轮询回调里**必须**有 props.date 门禁(否则历史回看被实时数据冲掉)
 *   · 轮询回调里**必须**有 current 门禁(否则没选板块也空打)
 *   · 轮询必须走**静默**分支(否则每 60s 整表闪一下)
 *   · MarketView 必须把 :date 传给面板(否则面板的 date 门禁永远拿到 '')
 *   · switchSrc 不得再有 `!conceptList.value.length` 短路
 *
 * 跑法: npm run test:board
 */
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/* ⚠️ 渲染任何**自己 usePolling** 的组件都必须先补 document 垫片 ——
   composables/usePolling.js 在 **setup 顶层**(不是 onMounted 里)就执行
   `document.addEventListener('visibilitychange', ...)` ⇒ Node 下 ReferenceError。
   (MarketBoardPanel 本轮新加 usePolling, 故本 spec 必补; 与 nav.spec 同款。)
   ★ 注意必须在 import 组件**之前**执行 —— usePolling 在模块 setup 时即触 document。 */
if (typeof globalThis.window === 'undefined') {
  globalThis.window = { matchMedia: () => ({ matches: false }), addEventListener() {}, removeEventListener() {} }
}
if (typeof globalThis.document === 'undefined') {
  globalThis.document = {
    hidden: false,
    addEventListener() {}, removeEventListener() {},
    querySelector() { return null }, querySelectorAll() { return [] },
    documentElement: { setAttribute() {}, style: {} },
    body: { setAttribute() {}, classList: { add() {}, remove() {} } },
  }
}

const __dirname = dirname(fileURLToPath(import.meta.url))
const PANEL_SRC = readFileSync(resolve(__dirname, '../src/components/MarketBoardPanel.vue'), 'utf8')
const MARKET_SRC = readFileSync(resolve(__dirname, '../src/views/MarketView.vue'), 'utf8')

let PASS = 0, FAIL = 0
const fails = []
function ok(name, cond, extra) {
  if (cond) { PASS++; console.log(`  [PASS] ${name}`) }
  else { FAIL++; fails.push(name); console.log(`  [FAIL] ${name}${extra ? ' :: ' + extra : ''}`) }
}

/** 抠出 usePolling(...) 的**回调体**源码(用于核门禁)。
 *  取法: 从 'usePolling(() => {' 起到配对的 '}, 60000' 止。 */
function pickPollingBody(src) {
  const m = src.match(/usePolling\(\(\)\s*=>\s*\{([\s\S]*?)\}\s*,\s*\d+/)
  return m ? m[1] : ''
}

const POLL_BODY = pickPollingBody(PANEL_SRC)

// ⚠️ 不能写顶层 await(esbuild 报 "Top-level await is not available"), 故包一层 async IIFE。
//    ★ 组件也在这里**动态 import** —— 必须在上面 document 垫片执行**之后**才加载,
//      否则 usePolling 在模块初始化时触 document 直接 ReferenceError。
async function main() {
const { default: MarketBoardPanel } = await import('../src/components/MarketBoardPanel.vue')

console.log('\n— B1. 组件可正常渲染(SSR 真渲染, 零异常)')
{
  const app = createSSRApp(MarketBoardPanel, {
    boards: [{ boardCode: '801001', name: '芯片', strength: 2967, limitCount: 5, mainNet: -1e9 }],
    src: 'kpl',
  })
  const html = await renderToString(app)
  ok('MarketBoardPanel 渲染无异常', typeof html === 'string' && html.length > 0)
  ok('渲染出板块名', html.includes('芯片'))
  ok('渲染不含 "undefined"', !html.includes('undefined'))
  ok('渲染不含 "NaN"', !html.includes('NaN'))
}

console.log('\n— B2. 轮询注册存在且位于 setup 顶层(非 onMounted 内)')
{
  ok('🔴 存在 usePolling 调用', /usePolling\(/.test(PANEL_SRC))
  ok('🔴 轮询回调体可抠出(说明写法符合预期)', POLL_BODY.length > 0)

  // v4.11.59/62 铁律: usePolling 必须在 setup 顶层注册.
  // 若被塞进 onMounted 回调, Vue 调 mounted 时 currentInstance 为 null ⇒
  // onBeforeUnmount 静默注册失败 ⇒ 定时器永不清理. 这里核"不在 onMounted 里".
  const onMountedBlock = (PANEL_SRC.match(/onMounted\(\(\)\s*=>\s*\{[\s\S]*?\n\}\)/g) || []).join('\n')
  ok('🔴 usePolling **不在** onMounted 回调内(否则定时器永不清理)',
     !/usePolling\(/.test(onMountedBlock))
}

console.log('\n— B3. 轮询回调的三道门禁(缺一即错)')
{
  ok('🔴 门禁① 已选板块(current)', /current\.value/.test(POLL_BODY))
  ok('🔴 门禁② 非历史回看(props.date)', /props\.date/.test(POLL_BODY))
  ok('🔴 门禁③ 盘中(isIntradayNow)', /isIntradayNow/.test(POLL_BODY))

  // 反向: 三道门禁必须都是"提前 return"式的短路, 不能只是读了一下变量
  const returns = (POLL_BODY.match(/return\b/g) || []).length
  ok('🔴 门禁以提前 return 实现(≥3 处)', returns >= 3, `实际 ${returns}`)
}

console.log('\n— B4. 轮询走静默刷新(否则每 60s 整表闪空)')
{
  ok('🔴 loadStocks 有 silent 形参', /function\s+loadStocks\s*\(\s*b\s*,\s*silent/.test(PANEL_SRC))
  ok('🔴 静默时不显示 loading', /if\s*\(\s*!\s*silent\s*\)\s*\{[\s\S]{0,80}stocksLoading\.value\s*=\s*true/.test(PANEL_SRC))
  ok('🔴 静默失败不清空旧数据', /if\s*\(\s*!\s*silent\s*\)\s*stocks\.value\s*=\s*\[\]/.test(PANEL_SRC))
  // 轮询调用处必须传 silent=true
  ok('🔴 轮询里以 silent=true 调用 loadStocks',
     /loadStocks\(\s*current\.value\s*,\s*true\s*\)/.test(POLL_BODY))
}

console.log('\n— B5. 切换数据源时重拉当前板块成分股')
{
  ok('🔴 有 watch(props.src) 重拉', /watch\(\(\)\s*=>\s*props\.src/.test(PANEL_SRC))
  ok('🔴 src 变化时按当前板块重拉', /props\.src[\s\S]{0,200}loadStocks\(current\.value\)/.test(PANEL_SRC))
}

console.log('\n— B6. 组件声明 date prop(门禁② 的数据来源)')
{
  ok('🔴 props 里声明 date', /date:\s*\{\s*type:\s*String/.test(PANEL_SRC))
}

console.log('\n— B7. MarketView 侧接线')
{
  ok('🔴 MarketView 把 :date 传给 MarketBoardPanel',
     /<MarketBoardPanel[\s\S]{0,300}:date="datePicker"/.test(MARKET_SRC))

  // 修缺陷②: switchSrc 不得再有 "有缓存就不拉" 短路。
  // ⚠️ 必须**先剥掉注释**再判 —— 修复说明里会原样引用旧代码
  //    (`原为 if (s==='em' && !conceptList.value.length) ...`), 不剥会把注释当代码,
  //    导致"改对了却报红"。本仓已有这类教训(守卫查文案, 而陈旧副本恰含该文案)。
  const MARKET_CODE = MARKET_SRC
    .replace(/\/\*[\s\S]*?\*\//g, '')      // 块注释
    .replace(/^\s*\/\/.*$/gm, '')          // 行注释(整行)
  ok('🔴 switchSrc 不再以 conceptList.length 短路(原缺陷②)',
     !/function switchSrc[\s\S]{0,400}!?\s*conceptList\.value\.length/.test(MARKET_CODE))
  ok('🔴 切入 em 时总是 loadConcept()',
     /function switchSrc[\s\S]{0,600}if\s*\(s\s*===\s*'em'\)\s*loadConcept\(\)/.test(MARKET_CODE))
}

console.log(`\n=== 结果：PASS=${PASS}  FAIL=${FAIL} ===`)
if (fails.length) { console.log('失败项：'); for (const f of fails) console.log('  - ' + f) }
process.exit(FAIL ? 1 : 0)

}

main()
