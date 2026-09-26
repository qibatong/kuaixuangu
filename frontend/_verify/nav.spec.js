/**
 * 渲染冒烟测试（SSR 版，无需浏览器）
 *   v4.11.60 建（导航三件套）；v4.11.62 扩到**盘中盯盘台六层组件**；同版再补 G8（盯盘台**本体**）。
 *
 * 为什么需要它：
 *   v4.11.58 的 `GroupNav.vue` 里有一行 `void NAV_GROUPS`，而该标识符**没有被 import**。
 *   `<script setup>` 的顶层语句会被编译进 setup()，于是运行时抛
 *   `ReferenceError: NAV_GROUPS is not defined` ⇒ **二级导航 pill 行整块不渲染**
 *   （桌面只剩 5 个一级入口；手机端点「复盘」只落到 /ladder，组内页面全部不可达）。
 *   这个缺陷：构建成功、无 warning、静态一致性自检全绿、发布校验 8 项全过 —— **只有真渲染才暴露**。
 *   同类事故 v4.11.62 又发生一次（`MarketBoardPanel` 模板用了 `list`、script 只定义了 `rows`），
 *   **同样是本测试当场抓住的**。实践证明：模板里的自由变量错拼，vite build 与 eslint 都看不见。
 *
 * 本测试做的事：
 *   用 vue/server-renderer 在 Node 里把组件**真的渲染成 HTML**，断言关键内容存在、
 *   且渲染过程中零异常零 Vue 警告（errorHandler/warnHandler 全量捕获）。
 *   覆盖：① 导航三件套（NavBar / GroupNav / AppTabBar）+ 15 条路由遍历；
 *        ② G1~G6 盯盘台六层（FlashTicker / YestZtPanel / MoneyTopStrip / TodayPicksPanel /
 *           MarketBoardPanel / YidongFlow）的正常态与**空态/降级态**——
 *           重点盯「null 不许渲染成 0」这类静默造假；
 *        ③ G8 盯盘台**本体** MarketView（六层编排 + 既有板块能力保留）——
 *           ⚠️ G1~G6 覆盖不到它：MarketView 自己模板里还有几十个自由变量
 *           （flashList/yestCount/pickRows/boardRows/hotBoard/updatedAt…），错拼同样是静默的。
 *
 * 跑法（见 frontend/package.json 的 `test:nav`；`npm run verify` = lint + 本测试）：
 *   1) vite build --ssr _verify/nav.spec.js --outDir .navssr --emptyOutDir
 *      ⚠️ 产物不能输出到 /tmp：Node 向上找不到 node_modules ⇒ Cannot find package 'vue'
 *   2) node .navssr/nav.spec.js
 *   ⚠️ 不能写顶层 await：`vite build --ssr` 沿用浏览器 target，esbuild 直接报
 *      "Top-level await is not available" 构建失败。
 *   ⚠️ G8 渲染 MarketView 会经 usePolling 留下 setTimeout ⇒ 收尾必须 process.exit()，
 *      否则 Node 事件循环永不空、测试进程挂住。
 */

import { createSSRApp, h } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { renderToString } from 'vue/server-renderer'
import GroupNav from '../src/components/GroupNav.vue'
import NavBar from '../src/components/NavBar.vue'
import AppTabBar from '../src/components/AppTabBar.vue'
import { NAV_GROUPS } from '../src/composables/useNavGroups'
// v4.11.62 盘中盯盘台六层（纯展示组件）
import FlashTicker from '../src/components/FlashTicker.vue'
import YestZtPanel from '../src/components/YestZtPanel.vue'
import MoneyTopStrip from '../src/components/MoneyTopStrip.vue'
import TodayPicksPanel from '../src/components/TodayPicksPanel.vue'
import MarketBoardPanel from '../src/components/MarketBoardPanel.vue'
import YidongFlow from '../src/components/YidongFlow.vue'
import MarketView from '../src/views/MarketView.vue'
import { summarizePicks } from '../src/utils/picks'
import { mergeLimitCount, sortBoardsByLimit } from '../src/utils/boards'

/* ---------- SSR 环境兜底：useTheme/NavBar 只在 onMounted 碰 DOM，但 store 初始化会读 localStorage ---------- */
if (typeof globalThis.localStorage === 'undefined') {
  const _s = new Map()
  globalThis.localStorage = {
    getItem: (k) => (_s.has(k) ? _s.get(k) : null),
    setItem: (k, v) => _s.set(k, String(v)),
    removeItem: (k) => _s.delete(k),
    clear: () => _s.clear(),
  }
}
if (typeof globalThis.window === 'undefined') {
  globalThis.window = { matchMedia: () => ({ matches: false }), addEventListener() {}, removeEventListener() {} }
}
/* ⚠️ v4.11.62：渲染 MarketView 本体必须补 document 垫片 ——
   composables/usePolling.js 在 **setup 顶层**（不是 onMounted 里）就执行
   `document.addEventListener('visibilitychange', ...)` ⇒ Node 下直接 ReferenceError。
   这正是「usePolling 必须在 setup 顶层注册」这条纪律的另一面：它一定会在 SSR 触到 document。 */
if (typeof globalThis.document === 'undefined') {
  globalThis.document = {
    hidden: false,
    addEventListener() {}, removeEventListener() {},
    querySelector() { return null }, querySelectorAll() { return [] },
    documentElement: { setAttribute() {}, style: {} },
    body: { setAttribute() {}, classList: { add() {}, remove() {} } },
  }
}

/* ---------- 测试骨架 ---------- */
const Stub = { name: 'Stub', render: () => h('div', 'stub') }
const R = (path, name, group) => ({ path, name, component: Stub, meta: group ? { group } : {} })

// 路由表必须与 src/router/index.js 的真实 path/name/meta.group 一致（不一致就会漏测到分组错位）
const ROUTES = [
  R('/news', 'news', 'news'),
  R('/', 'stock', 'auction'), R('/auction', 'auction', 'auction'),
  R('/aipick', 'aipick', 'auction'), R('/aipick-lgb', 'aipick-lgb', 'auction'),
  R('/market', 'market', 'intraday'),
  R('/ladder', 'ladder', 'review'), R('/history', 'history', 'review'), R('/temper', 'temper', 'review'),
  R('/bigv', 'bigv', 'review'), R('/yidong', 'yidong', 'review'), R('/lhb', 'lhb', 'review'),
  R('/pool', 'pool', 'pool'),
  R('/member', 'member', 'me'), R('/admin', 'admin', 'me'),
  R('/login', 'login', null),
]

let PASS = 0, FAIL = 0
const fails = []
function ok(name, cond, extra) {
  if (cond) { PASS++; console.log(`  [PASS] ${name}`) }
  else { FAIL++; fails.push(name); console.log(`  [FAIL] ${name}${extra ? ' :: ' + extra : ''}`) }
}

/** 渲染指定路由下的导航三件套，返回 { html, errors } */
async function renderAt(path) {
  const router = createRouter({ history: createMemoryHistory(), routes: ROUTES })
  const errors = []
  const app = createSSRApp({
    render: () => h('div', [h(NavBar), h(GroupNav), h(AppTabBar)]),
  })
  app.config.errorHandler = (err) => { errors.push(String(err && err.message ? err.message : err)) }
  app.config.warnHandler = (msg) => { errors.push('VUE_WARN: ' + msg) }
  app.use(createPinia())
  app.use(router)
  await router.push(path)
  await router.isReady()
  const html = await renderToString(app)
  return { html, errors }
}

/** 渲染单个组件（盯盘台六层都是纯展示组件，只吃 props，SSR 下不会触发任何网络请求） */
async function renderComp(component, props = {}, route = '/market') {
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

/* ---------- 断言 ----------
   ⚠️ 必须包在 async main() 里，不能写顶层 await —— `vite build --ssr` 沿用 vite.config.js 的
   浏览器 target(es2020/chrome87)，esbuild 会以 "Top-level await is not available" 直接构建失败。 */
async function main() {
console.log('\n=== 导航渲染冒烟测试 (SSR) ===\n')

// A. 主人点名的三页必须在「复盘」组内 → 用 /ladder 渲染，pill 行须列出它们
const a = await renderAt('/ladder')
console.log('— A. /ladder（复盘组，主人点名的三页）')
ok('渲染无异常/无 Vue 警告', a.errors.length === 0, a.errors.join(' | '))
ok('二级 pill 行已渲染 (.group-nav)', a.html.includes('group-nav'))
for (const label of ['涨停梯队', '历史回看', '股性', '大V资讯', '异动监管', '龙虎榜']) {
  ok(`复盘组含「${label}」`, a.html.includes(label))
}
for (const href of ['/history', '/temper', '/bigv', '/yidong', '/lhb']) {
  ok(`复盘组含链接 ${href}`, a.html.includes(`href="${href}"`))
}
ok('组内 6 项全在（pill 数=6）', (a.html.match(/group-nav-item/g) || []).length === 6,
   '实际 ' + (a.html.match(/group-nav-item/g) || []).length)

// B. 「竞价」组**不渲染 pill 行**（v4.11.61 主人实测反馈后定稿：与 / 页内联 tab 重复）
const b = await renderAt('/')
console.log('\n— B. /（竞价组，无二级 pill 行）')
ok('渲染无异常/无 Vue 警告', b.errors.length === 0, b.errors.join(' | '))
ok('竞价组不渲染 .group-nav（hidePills）', !b.html.includes('group-nav-item'),
   '实际 pill 数 ' + (b.html.match(/group-nav-item/g) || []).length)
ok('竞价组标签仍在（一级 nav / tabbar 里有「竞价」）', b.html.includes('竞价'))
// 组内 4 条路由仍必须可达（只是不再由 pill 行承载，而是 / 页内联 tab）
for (const href of ['/', '/auction', '/aipick', '/aipick-lgb']) {
  ok(`竞价组 items 仍声明 ${href}`, NAV_GROUPS.find((g) => g.key === 'auction').items.some((i) => i.path === href))
}

// B2. 盘前资讯**已升为一级分组**（主人：和竞价、盘中放一行）
const b2 = await renderAt('/news')
console.log('\n— B2. /news（盘前资讯，一级分组）')
ok('渲染无异常/无 Vue 警告', b2.errors.length === 0, b2.errors.join(' | '))
ok('盘前资讯是一级分组（NAV_GROUPS 里有 key=news）', !!NAV_GROUPS.find((g) => g.key === 'news'))
ok('一级分组里出现「盘前资讯」', b2.html.includes('盘前资讯'))
ok('NavBar 有 /news 一级入口', b2.html.includes('href="/news"'))
ok('盘前资讯不在「竞价」组内（已迁出）',
   !NAV_GROUPS.find((g) => g.key === 'auction').items.some((i) => i.path === '/news'))
ok('单页组不渲染 pill 行', !b2.html.includes('group-nav-item'))

// C. 单页组不渲染 pill 行（设计约定）
const c = await renderAt('/market')
console.log('\n— C. /market（盘中组只有 1 页 ⇒ 不渲染 pill 行）')
ok('渲染无异常/无 Vue 警告', c.errors.length === 0, c.errors.join(' | '))
ok('单页组不渲染 .group-nav', !c.html.includes('group-nav-item'))

// D. 一级入口：NavBar 6 个 + 底部 tabbar 6 个，且都指向各组 entry
console.log('\n— D. 一级入口（NavBar / AppTabBar）')
for (const g of NAV_GROUPS) {
  ok(`NavBar 有一级入口「${g.label}」`, b.html.includes(g.label))
  ok(`AppTabBar 有 tab「${g.label}」→ ${g.entry}`, b.html.includes(`href="${g.entry}"`))
}
ok('一级分组恰好 6 个', NAV_GROUPS.length === 6, '实际 ' + NAV_GROUPS.length)
ok('底部 tabbar 恰好 6 个 tab', (b.html.match(/tabbar-item/g) || []).length === 6,
   '实际 ' + (b.html.match(/tabbar-item/g) || []).length)
ok('底部 tab 顺序 = 竞价 / 盘前资讯 / 盘中 / 复盘 / 自选 / 我的',
   NAV_GROUPS.map((g) => g.label).join('/') === '竞价/盘前资讯/盘中/复盘/自选/我的',
   '实际 ' + NAV_GROUPS.map((g) => g.label).join('/'))

// E. 自由变量/未定义标识符的典型渲染痕迹
console.log('\n— E. 未定义标识符痕迹扫描')
for (const [name, res] of [['/ladder', a], ['/', b], ['/market', c]]) {
  ok(`${name} 渲染结果不含 "undefined"`, !res.html.includes('undefined'))
}

// F. 全部 15 条路由都不能崩（防"某个分组渲染就炸"）
console.log('\n— F. 全路由遍历（任一分组渲染都不许崩）')
for (const r of ROUTES) {
  const res = await renderAt(r.path)
  ok(`${r.path} 渲染无异常`, res.errors.length === 0, res.errors.join(' | '))
}

// ==================== G. 盘中盯盘台六层（v4.11.62 · 工单批次二） ====================
console.log('\n— G. 盯盘台六层组件（渲染契约 + 空/降级态）')

// G1 ① 快讯走马灯
const flashItems = [
  { id: 'kpl:1', time_label: '09:31', date: '09-27', source: '开盘啦', title: '央行开展逆回购操作', summary: '摘要内容', url: 'https://x.test/1', kind: 'kpl' },
  { id: 'meoz:2', time_label: '09:30', date: '09-27', source: '第一财经', title: '两市小幅高开', summary: '', url: '', kind: 'meoz' },
]
const g1 = await renderComp(FlashTicker, { items: flashItems })
ok('FlashTicker 渲染无异常/无警告', g1.errors.length === 0, g1.errors.join(' | '))
ok('FlashTicker 条数 = 样本×2（走马灯无缝循环必须渲染两份）',
  (g1.html.match(/ft-item/g) || []).length === 4, '实际 ' + (g1.html.match(/ft-item/g) || []).length)
ok('FlashTicker 含时间轴与标题', g1.html.includes('09:31') && g1.html.includes('央行开展逆回购操作'))
const g1e = await renderComp(FlashTicker, { items: [], loading: true })
ok('FlashTicker 空+加载中 → 明确文案（不崩）', g1e.errors.length === 0 && g1e.html.includes('加载快讯中'))
const g1d = await renderComp(FlashTicker, { items: [], loading: false, degraded: ['meoz'] })
ok('FlashTicker 降级时点名缺失源（禁止用空列表假装「今天没资讯」）', g1d.html.includes('meoz'))

// G2 ② 昨日涨停今日表现
const g2 = await renderComp(YestZtPanel, {
  count: 88, avgOpen: 3.12, avgNow: 4.56, maxLadder: 5, brokenRate: 12.3, date: '2026-09-26',
})
ok('YestZtPanel 渲染无异常/无警告', g2.errors.length === 0, g2.errors.join(' | '))
ok('YestZtPanel 四指标标题齐备',
  ['平均高开', '现溢价', '连板高度', '炸板率'].every((k) => g2.html.includes(k)))
ok('YestZtPanel 数值格式正确（+3.12% / +4.56% / 5 板 / 12.3%）',
  g2.html.includes('+3.12%') && g2.html.includes('+4.56%') && g2.html.includes('5 板') && g2.html.includes('12.3%'))
ok('YestZtPanel 样本数透出', g2.html.includes('88'))
ok('YestZtPanel 情绪=修复期（现溢价≥2 且不低于平均高开）', g2.html.includes('修复期'))
const g2b = await renderComp(YestZtPanel, { count: 0, avgOpen: null, avgNow: null, maxLadder: null, brokenRate: null })
ok('🔴 无样本时逐项显示 --（绝不把 null 渲染成 0.00%）',
  (g2b.html.match(/--/g) || []).length >= 4 && !g2b.html.includes('0.00%'),
  '-- 数 ' + (g2b.html.match(/--/g) || []).length)
ok('无样本时情绪=待统计（不硬判修复/退潮）', g2b.html.includes('待统计'))
const g2c = await renderComp(YestZtPanel, { count: 10, avgOpen: 2, avgNow: -1.2 })
ok('现溢价为负 → 退潮期 · 管住手', g2c.html.includes('退潮期'))

// G3 ③ 最强资金 TOP
const moneyBoards = [
  { name: '机器人', boardCode: '801001', change: 3.2, mainNet: 5e8 },
  { name: '半导体', boardCode: '801002', change: 1.1, mainNet: 9e8 },
  { name: '无净额板块', boardCode: '801003', change: 0.5, mainNet: null },
]
const g3 = await renderComp(MoneyTopStrip, { boards: moneyBoards })
ok('MoneyTopStrip 渲染无异常/无警告', g3.errors.length === 0, g3.errors.join(' | '))
ok('🔴 mainNet=null 的板块不参与 TOP（不用 0 顶替）', !g3.html.includes('无净额板块'))
ok('按主力净额降序（半导体 9 亿在 机器人 5 亿之前）',
  g3.html.indexOf('半导体') < g3.html.indexOf('机器人'))
ok('净额格式 +9.00亿', g3.html.includes('+9.00亿'))
ok('卡片数 = 有效板块数 2', (g3.html.match(/mt-card/g) || []).length === 2,
  '实际 ' + (g3.html.match(/mt-card/g) || []).length)
const g3e = await renderComp(MoneyTopStrip, { boards: [], failed: true })
ok('MoneyTopStrip 空+失败 → 明说数据源暂缺', g3e.html.includes('数据源暂缺'))

// G4 ④ 今日票战报
const pickStocks = [
  { code: '600001', name: '翻绿票', price: 11.0, change: -2.5 },
  { code: '600002', name: '主板封板', price: 22.0, change: 10.0 },
  { code: '300003', name: '创业板封板', price: 33.0, change: 20.0 },
]
const g4 = await renderComp(TodayPicksPanel, {
  stocks: pickStocks, summary: summarizePicks(pickStocks), date: '2026-09-28', isToday: true,
})
ok('TodayPicksPanel 渲染无异常/无警告', g4.errors.length === 0, g4.errors.join(' | '))
ok('聚合：今日选了 3 只', g4.html.includes('3 只'))
ok('逐票状态标签齐备（封板×2 / 翻绿×1）',
  (g4.html.match(/st-limit/g) || []).length === 2 && (g4.html.match(/st-down/g) || []).length === 1,
  'limit=' + (g4.html.match(/st-limit/g) || []).length + ' down=' + (g4.html.match(/st-down/g) || []).length)
ok('按状态排序：翻绿票排在封板票之后', g4.html.indexOf('600001') > g4.html.indexOf('600002'))
ok('🔴 无 peakChange 时炸板数显示占位符（不编数字）', g4.html.includes('—'))
ok('summarizePicks: 数据源不给最高价时 brokenKnown=false',
  summarizePicks([{ code: '600001', change: 1 }]).brokenKnown === false)
const g4b = await renderComp(TodayPicksPanel, {
  stocks: pickStocks, summary: summarizePicks(pickStocks), date: '2026-09-25', isToday: false,
})
ok('非今日名单必须显式标注「非今日」', g4b.html.includes('非今日'))
const g4e = await renderComp(TodayPicksPanel, { stocks: [], summary: summarizePicks([]), emptyMsg: '今日名单尚未生成' })
ok('空名单给出等待文案', g4e.html.includes('今日名单尚未生成'))
const g4f = await renderComp(TodayPicksPanel, { stocks: [], summary: summarizePicks([]), failed: true })
ok('名单读取失败有明确提示', g4f.html.includes('名单读取失败'))

// G5 ⑤ 题材榜
const boardRows = mergeLimitCount([
  { name: '机器人概念', boardCode: '801001', change: 3.2, mainNet: 5e8 },
  { name: '半导体设备', boardCode: '801002', change: 1.1, mainNet: 9e8 },
  { name: '匹配不上的板块', boardCode: '801003', change: 2.2, mainNet: 3e8 },
], [{ name: '机器人', count: 8 }, { name: '半导体', count: 12 }])
const g5 = await renderComp(MarketBoardPanel, { boards: boardRows, src: 'kpl' })
ok('MarketBoardPanel 渲染无异常/无警告', g5.errors.length === 0, g5.errors.join(' | '))
ok('🔴 涨停数来自另一上游、名称归一化后匹配成功（机器人=8 / 半导体=12）',
  boardRows[0].limitCount === 8 && boardRows[1].limitCount === 12 &&
  boardRows[2].limitCount === null)
ok('默认按涨停数降序（半导体 12 在 机器人 8 之前）',
  g5.html.indexOf('半导体设备') < g5.html.indexOf('机器人概念'))
ok('匹配不上的题材显示 —（不显示 0）', g5.html.includes('—'))
ok('数据源切换按钮齐备（开盘啦榜 / 东财概念榜）',
  g5.html.includes('开盘啦榜') && g5.html.includes('东财概念榜'))
ok('列头齐备（题材 / 涨停数 / 涨幅% / 主力净额(亿)）',
  g5.html.includes('题材') && g5.html.includes('涨停数') && g5.html.includes('涨幅%') && g5.html.includes('主力净额(亿)'))
ok('sortBoardsByLimit: null 恒排最后', (() => {
  const s = sortBoardsByLimit([{ name: 'a', limitCount: null }, { name: 'b', limitCount: 1 }])
  return s[0].name === 'b'
})())
const g5e = await renderComp(MarketBoardPanel, { boards: [], src: 'kpl' })
ok('空题材榜有明确文案', g5e.html.includes('暂无题材榜数据'))

// G6 ⑥ 实时异动流
const ydItems = [
  { code: '600001', name: '异动甲', type: '严重异动', trigger: '10日累计偏离 100%', triggered: true, change: 9.9, deviation: 105.2, days: 10, target: 100 },
]
const g6 = await renderComp(YidongFlow, { items: ydItems, day: '2026-09-26', time: '14:30' })
ok('YidongFlow 渲染无异常/无警告', g6.errors.length === 0, g6.errors.join(' | '))
ok('含名称/类型/触发描述/偏离值', g6.html.includes('异动甲') && g6.html.includes('严重异动') &&
  g6.html.includes('10日累计偏离 100%') && g6.html.includes('偏离 +105.20%'))
const g6e = await renderComp(YidongFlow, { items: [] })
ok('无异动 → 明确文案', g6e.html.includes('无触发异动'))
const g6f = await renderComp(YidongFlow, { items: [], failed: true })
ok('异动源失败 → 明说数据源暂缺（不空着让人以为是没异动）', g6f.html.includes('数据源暂缺'))

// G7 盯盘台不得在组件里注册轮询（轮询统一在 MarketView 的 tick 里）
console.log('\n— G7. 六层渲染结果整体扫描')
for (const [name, res] of [['FlashTicker', g1], ['YestZtPanel', g2], ['MoneyTopStrip', g3],
  ['TodayPicksPanel', g4], ['MarketBoardPanel', g5], ['YidongFlow', g6]]) {
  ok(`${name} 渲染结果不含 "undefined"`, !res.html.includes('undefined'))
  ok(`${name} 渲染结果不含 "NaN"`, !res.html.includes('NaN'))
}

// G8 /market 盯盘台**本体**（不是子组件）——★ v4.11.62 最高风险文件
//   G1~G6 只证明六个子组件各自能渲染；但 MarketView 自己模板里也有几十个自由变量
//   （flashList/yestCount/pickRows/boardRows/hotBoard/updatedAt/...），
//   **错拼同样不会被 build 与 eslint 发现**，只有真渲染才暴露 ⇒ 必须单独渲染它。
//   props 为空、onMounted 不执行 ⇒ 六层拿到的都是空数据，正好顺带验证「空态不许造假」。
console.log('\n— G8. MarketView 盯盘台本体（六层编排 + 既有能力一件不丢）')
const g8 = await renderComp(MarketView, {}, '/market')
ok('MarketView 渲染无异常/无 Vue 警告', g8.errors.length === 0, g8.errors.join(' | '))
ok('含页头 盘中盯盘台', g8.html.includes('盘中盯盘台'))
ok('含六层栈容器 .mk-stack', g8.html.includes('mk-stack'))
ok('含刷新戳 .mk-updated', g8.html.includes('mk-updated'))
// 六层：空数据态下六层**仍须各自渲染出标题**（某层若自由变量错拼，这层会整块消失）
for (const t of ['快讯', '昨日涨停今日表现', '按主力净额排序', '今日票战报', '题材榜', '实时异动流']) {
  ok(`六层标题「${t}」`, g8.html.includes(t))
}
ok('⑥ 副标题 按累计偏离值排序', g8.html.includes('按累计偏离值排序'))
// 既有能力一件不丢（折叠区 + 三个 tab + 强度表工具条）
ok('保留 板块进阶数据 折叠区', g8.html.includes('板块进阶数据'))
for (const t of ['板块强度明细', '板块轮动历史', '人气热榜']) ok(`保留 tab「${t}」`, g8.html.includes(t))
ok('保留强度表工具条（日期回看）', g8.html.includes('实时板块强度排行'))
// ⚠️ SSR 初始态 boardLoading=true ⇒ 强度表走「加载占位」分支（不是空态文案）；
//    两者都是「不冒充有数据」的诚实态，这里断言的是实际该出现的那一个。
ok('初始态 → 加载占位（不冒充有数据）', g8.html.includes('加载板块强度'))
ok('MarketView 渲染结果不含 "undefined"', !g8.html.includes('undefined'))
ok('MarketView 渲染结果不含 "NaN"', !g8.html.includes('NaN'))

console.log(`\n=== 结果：PASS=${PASS}  FAIL=${FAIL} ===`)
if (FAIL) { console.log('失败项：\n  - ' + fails.join('\n  - ')); process.exit(1) }
console.log('渲染冒烟测试全绿。\n')
// ⚠️ 必须显式退出：G8 渲染 MarketView 时 usePolling 会留下 setTimeout(1s/60s) 并自我重排，
//    不 exit 的话 Node 事件循环永不空 ⇒ 测试进程挂住不返回（退出码也会被拖没）。
process.exit(0)
}

main().catch((e) => {
  console.error('\n[FATAL] 冒烟测试自身异常：', e && e.stack ? e.stack : e)
  process.exit(2)
})
