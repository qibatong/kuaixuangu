/**
 * 渲染冒烟测试（SSR 版，无需浏览器）
 *   v4.11.60 建（导航三件套）；v4.11.62 扩到**盘中盯盘台六层组件**；同版再补 G8（盯盘台**本体**）。
 *   v4.11.63 补 G9（全局股票搜索面板/入口 —— 《快选移动端追加清单》§三）。
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
 *        ③ G8 盯盘台**本体** MarketView（六层编排 + 板块能力：就地留着的对现状断言 /
 *           搬走的去新家核对 / 下线的反向钉死）——
 *           ★ 2026-10-05：本节原写「既有板块能力一件不丢」，但实测「板块进阶数据」折叠区
 *           （强度明细 / 轮动历史 / 人气热榜 + 日期回看工具条）已在 09-27~09-28 的板块区重构中
 *           移出 /market，且当时没留版本表条目 ⇒ 断言改为如实反映现状 + 去向核对，详见 G8 段内注释。
 *           ⚠️ G1~G6 覆盖不到它：MarketView 自己模板里还有几十个自由变量
 *           （flashList/yestCount/pickRows/boardRows/hotBoard/updatedAt…），错拼同样是静默的。
 *        ④ G9 全局股票搜索（《移动端清单》§三）—— 面板是**纯展示组件**，
 *           所以能用夹具把 loading/empty/err/ok 四个态各自渲染出来。
 *           ★ 重点断言「空结果」与「服务失败」文案**必须不同**：两者都长成空列表
 *             就是本项目最忌讳的"静默"（yday_amount 冻结 9 日 / 名单退回昨日 同型）。
 *        ⑤ G10 DataStamp 数据更新时刻三态（v4.11.63）。
 *        ⑥ G11 异动/停牌风险（v4.11.64 · 工单批次三）：DevWarnList 四态 +
 *           DevRiskDetail（算得出 / **算不出** / 未计算）+ `/yidong` 本体三 tab。
 *           ★ 最要紧的一条：后端用 `ok:false + reason` 表达「算不出」（指数源不可用、
 *             个股长期停牌…），前端**绝不能**渲染成一堆 0 —— 那就是把"不知道"画成"安全"，
 *             与「yday_amount 冻结 9 个交易日」是同一类缺陷。
 *        ⑦ G11 另押一条纪律：已删 tab（热门股偏离值 / 多次异动）不许留残留 ——
 *           模板里的死代码不会报错，只会静默留在页面上。
 *        ⑧ G12 「我的」账户区块（v4.11.65 · 顶部 NavBar 的账户下拉整块迁入 MemberView）。
 *           两种判据缺一不可：**迁入的必须在**（账户卡 / 个人信息 / 改密 / 退出 / 字号 / 字体）、
 *           **迁走的必须不在**（顶部导航不许再有 .user-name-btn 与账户下拉项）——
 *           UI 搬迁最容易只做一半：新家建好了，旧家没搬走。
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
import QuickGrid from '../src/components/QuickGrid.vue'
import { NAV_GROUPS, TABBAR_TABS } from '../src/composables/useNavGroups'
// ★ 2026-10-05：板块区重构后，两项能力**搬去了别的页面**，断言必须"去新家核对" ⇒ 读源码。
//   与 spot.spec.js 同一套写法（dirname(fileURLToPath(import.meta.url)) + readFileSync）。
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
// 2026-10-04 站内消息
import MessagesView from '../src/views/MessagesView.vue'
// v4.11.62 盘中盯盘台六层（纯展示组件）
import FlashTicker from '../src/components/FlashTicker.vue'
import YestZtPanel from '../src/components/YestZtPanel.vue'
import MoneyTopStrip from '../src/components/MoneyTopStrip.vue'
import TodayPicksPanel from '../src/components/TodayPicksPanel.vue'
import MarketBoardPanel from '../src/components/MarketBoardPanel.vue'
// 🔴 2026-09-28 主人拍板：YidongFlow（实时异动流）整块移除、组件已删除 ⇒ 不再 import。
import MarketView from '../src/views/MarketView.vue'
// v4.11.63 全局股票搜索（《快选移动端追加清单》§三）
import StockSearch from '../src/components/StockSearch.vue'
import StockSearchPanel from '../src/components/StockSearchPanel.vue'
// v4.11.63 数据更新时刻（《快选移动端追加清单》§二·4）
import DataStamp from '../src/components/DataStamp.vue'
// v4.11.64 异动 / 停牌风险（《快选异动停牌风险功能工单》批次三）
import DevWarnList from '../src/components/DevWarnList.vue'
import DevRiskDetail from '../src/components/DevRiskDetail.vue'
import YidongView from '../src/views/YidongView.vue'
// v4.11.65 「我的」账户区块（由顶部 NavBar 迁入）
import MemberView from '../src/views/MemberView.vue'
import { summarizePicks } from '../src/utils/picks'
import { mergeLimitCount, sortBoardsByLimit } from '../src/utils/boards'
// ★ v4.11.71：取**源码文本**做静态断言（`?raw` 由 vite 在构建期内联，SSR 下可用）。
//   为什么需要它：有些接线（如「组件拿到 props」）在 SSR 空数据下渲染结果一样，
//   只有读源码才能钉死「props 真传了 / 死代码真删了」。
import YidongViewSrc from '../src/views/YidongView.vue?raw'
import MarketViewSrc from '../src/views/MarketView.vue?raw'


/* ---------- SSR 环境兜底：useTheme/NavBar 只在 onMounted 碰 DOM，但 store 初始化会读 localStorage ---------- */
/* ⚠️ 2026-10-05：判据从「有没有 localStorage」改为「方法齐不齐」。
   Node 22+ 起 globalThis.localStorage **存在但只是个空壳**（实测 Object.keys(prototype) 为空、
   getItem/setItem/removeItem 都不是函数）⇒ 原判据 `typeof === 'undefined'` 恒假、垫片从不安装，
   于是 store 的 readSession() 每次都抛异常进 catch、永远读成"未登录"（本 spec 因此**长期只能测匿名态**）。 */
function hasLocalStorage() {
  const ls = globalThis.localStorage
  return !!ls && typeof ls.getItem === 'function' && typeof ls.setItem === 'function' && typeof ls.removeItem === 'function'
}
if (!hasLocalStorage()) {
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
  R('/market', 'market', 'intraday'), R('/theme', 'theme', 'intraday'),
  // ⚠️ 桩路由必须与 src/router/index.js 的 meta **逐项一致**（本表只传 group，
  //    但 /chaozhi 还带 noGroupNav ⇒ 这里显式写出，否则测不到豁免）
  { path: '/chaozhi', name: 'chaozhi', component: Stub, meta: { group: 'intraday', noGroupNav: true } },
  R('/ladder', 'ladder', 'review'), R('/history', 'history', 'review'), R('/temper', 'temper', 'review'),
  R('/bigv', 'bigv', 'review'), R('/yidong', 'yidong', 'review'), R('/lhb', 'lhb', 'review'),
  R('/pool', 'pool', 'pool'),
  R('/member', 'member', 'me'), R('/admin', 'admin', 'me'),
  // 2026-10-04 消息中心：不渲染分组 pill 行（它是铃铛进的独立页，不属于任何组的二级页）
  { path: '/messages', name: 'messages', component: Stub, meta: { group: 'me', noGroupNav: true } },
  R('/login', 'login', null),
]

let PASS = 0, FAIL = 0
const fails = []
function ok(name, cond, extra) {
  if (cond) { PASS++; console.log(`  [PASS] ${name}`) }
  else { FAIL++; fails.push(name); console.log(`  [FAIL] ${name}${extra ? ' :: ' + extra : ''}`) }
}

/**
 * 数「class 属性里含某类名的元素」个数。
 * ⚠️ 不能用 `html.includes('class="ss-row')` 这种写法计数 —— Vue SSR 合并 `:class` 时
 *    把**动态类排在静态类之前**：静态 `class="ss-row"` + 动态 `is-active` 会渲染成
 *    `class="is-active ss-row"` ⇒ 按前缀计数必然漏掉高亮那一行（本测试第一版就是这样
 *    把 3 行数成 2 行）。与 grep 计数同一条纪律：先看清真实输出形状再写判据。
 */
function countByClass(html, cls) {
  return (html.match(new RegExp(`class="[^"]*\\b${cls}\\b[^"]*"`, 'g')) || []).length
}

/**
 * 剥掉 JS / CSS / HTML 注释，只留**可执行代码**。
 * 为什么需要：判「死代码是否清干净」时，实现里常刻意留下「为什么删」的说明注释，
 *   注释里带着被删标识符的名字 ⇒ 直接 substring 会把**解释**误判成**残留**。
 *   本项目已多次踩「grep 命中了自己的注释」（v4.11.62 的探针假失败、v4.11.61 的注入无效）。
 * ⚠️ 朴素实现（会误伤字符串里的 `//`，如 `'https://…'`）—— 用于本测试已足够：
 *   我们只关心几个**标识符**是否残留，误伤只会让判据更宽松，不会造成假红。
 *   若将来要用它做更严的判据，须换成真正的分词器。
 */
function stripComments(src) {
  return String(src)
    .replace(/\/\*[\s\S]*?\*\//g, ' ')      // /* … */ 与 <!-- … --> 的块注释（含 vue 模板注释）
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/(^|\s)\/\/[^\n]*/g, '$1')     // 行首/空白后的 // 行注释
}

/** 写入 / 清除测试用登录态。
 *  ⚠️ Node 22+ 自带的 globalThis.localStorage 只实现了部分方法（实测没有 removeItem），
 *     所以**不能**直接调 removeItem；这里对每个方法都做存在性判断，并退化用
 *     `setItem(key, 'null')` 表达"无会话"（store 的 readSession 会 JSON.parse('null') → null，等价）。 */
function seedSession(on) {
  const SESSION = JSON.stringify({
    username: '_spec', token: '_spec_token', is_admin: 0, expire_at: 0, expired: 0, member_level: 1,
  })
  try {
    const ls = globalThis.localStorage
    if (!ls) return
    if (on) { ls.setItem && ls.setItem('kuaixuan_session_v1', SESSION); return }
    if (ls.removeItem) ls.removeItem('kuaixuan_session_v1')
    else if (ls.setItem) ls.setItem('kuaixuan_session_v1', 'null')
  } catch (e) { /* 垫片缺失时忽略：仅影响登录态渲染，不影响其余断言 */ }
}

/** 渲染指定路由下的导航三件套，返回 { html, errors }
 *  2026-10-05 (S1)：新增 opts.auth —— NavBar 的一级分组 tab 现在对**匿名访客不渲染**
 *  （v-if="user.isLoggedIn"：落地页上这些 tab 全部指向需登录页面，点了只会被守卫弹到 /login）。
 *  因此"一级入口齐备"这类断言必须在**已登录**上下文里渲染；匿名上下文另立断言（见 D 段末尾）。 */
async function renderAt(path, opts = {}) {
  seedSession(opts.auth === true)
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
for (const label of ['连板天梯', '历史回看', '股性', '大V复盘', '异动监管', '龙虎榜']) {
  ok(`复盘组含「${label}」`, a.html.includes(label))
}
for (const href of ['/history', '/temper', '/bigv', '/yidong', '/lhb']) {
  ok(`复盘组含链接 ${href}`, a.html.includes(`href="${href}"`))
}
ok('组内 6 项全在（pill 数=6）', (a.html.match(/group-nav-item/g) || []).length === 6,
   '实际 ' + (a.html.match(/group-nav-item/g) || []).length)

// B. 「竞价」组**不渲染 pill 行**（v4.11.61 主人实测反馈后定稿：与 / 页内联 tab 重复）
//    ⚠️ 这里起用 auth:true —— 一级 tab 只对已登录渲染（见 renderAt 注释），
//    本段及 D 段断言的是"登录后的导航结构"。
const b = await renderAt('/', { auth: true })
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
const b2 = await renderAt('/news', { auth: true })
console.log('\n— B2. /news（盘前资讯，一级分组）')
ok('渲染无异常/无 Vue 警告', b2.errors.length === 0, b2.errors.join(' | '))
ok('盘前资讯是一级分组（NAV_GROUPS 里有 key=news）', !!NAV_GROUPS.find((g) => g.key === 'news'))
ok('一级分组里出现「盘前资讯」', b2.html.includes('盘前资讯'))
ok('NavBar 有 /news 一级入口', b2.html.includes('href="/news"'))
ok('盘前资讯不在「竞价」组内（已迁出）',
   !NAV_GROUPS.find((g) => g.key === 'auction').items.some((i) => i.path === '/news'))
ok('单页组不渲染 pill 行', !b2.html.includes('group-nav-item'))

// C. 2026-10-01 主人新增「题材库」⇒ 盘中组由 1 页变 2 页 ⇒ 该组**应当**渲染 pill 行
//    （原断言"单页组不渲染 pill 行"在此组不再成立；单页组约定仍由 pool/me 等组覆盖）
const c = await renderAt('/market')
console.log('\n— C. /market（盘中组 2 页：板块 / 题材库 ⇒ 渲染 pill 行）')
ok('渲染无异常/无 Vue 警告', c.errors.length === 0, c.errors.join(' | '))
ok('盘中组渲染 pill 行且含「题材库」',
   c.html.includes('group-nav-item') && c.html.includes('题材库'))
ok('盘中组 pill 行含 /theme 链接', c.html.includes('href="/theme"'))

// C2. 2026-10-01 主人: 「超智页面上面怎么有盘中/板块/题材库，这个不应该显示在这里」
//     ⇒ /chaozhi 用 `meta.noGroupNav` **路由级**豁免分组 pill 行（不影响盘中组的其它页面）
const c2 = await renderAt('/chaozhi')
console.log('\n— C2. /chaozhi（独立聚合页 ⇒ 不渲染分组 pill 行）')
ok('渲染无异常/无 Vue 警告', c2.errors.length === 0, c2.errors.join(' | '))
ok('不含分组 pill 行', !c2.html.includes('group-nav-item'))
ok('不含「板块 / 题材库」pill 文案', !c2.html.includes('>题材库<') && !c2.html.includes('>板块<'))

// D. 一级入口：**桌面 7 组**（2026-10-03 主人指令：「超智」升为一级分组且放「竞价」左边，
//    原话「放在竞价这里，竞价向右调整一下」—— 曾误解为竞价页左栏，已回退）；手机底部仍 5 格
//    = 首页/竞价/盘中/复盘/我的（超智不进底部栏，入口仍走首页宫格，与盘前资讯同待遇）
//    2026-10-01 主人拍板（原话「4格 首页、竞价、盘中、我的」「底部不保留搜索，上面已经搜索了」）
//    ⇒ 底部栏不再逐组渲染，改读 TABBAR_TABS；失去底部入口的页由首页宫格 QuickGrid 承载。
console.log('\n— D. 一级入口（NavBar 7 组 / AppTabBar 5 格）')
for (const g of NAV_GROUPS) {
  ok(`NavBar 有一级入口「${g.label}」`, b.html.includes(g.label))
}
// ★ 2026-10-05 (S1) 新增：登录态 = 渲染 N 个一级 tab；匿名态 = 一个都不渲染。
//   目的：把"落地页不给死链"这条产品约定锁进闸门，防止后人把 v-if 去掉又退化成
//   「匿名访客点一级 tab 就被弹到登录页」。
const countNavItems = (html) => (html.match(/class="nav-item/g) || []).length
ok('登录态渲染出全部一级 tab（' + NAV_GROUPS.length + ' 个）',
  countNavItems(b.html) === NAV_GROUPS.length, '实际 ' + countNavItems(b.html))
const bAnon = await renderAt('/')
ok('🔴 匿名访客不渲染一级 tab（避免"点了就被弹到登录页"的死链）',
  countNavItems(bAnon.html) === 0, '实际 ' + countNavItems(bAnon.html))
ok('匿名访客仍保留登录 / 注册入口（.user-tools）', bAnon.html.includes('user-tools'))
ok('超智是一级分组且排在竞价**前面**（竞价向右让位）',
   NAV_GROUPS.length > 1 && NAV_GROUPS[0].key === 'chaozhi' && NAV_GROUPS[1].key === 'auction',
   '实际前两组: ' + NAV_GROUPS.slice(0, 2).map((g) => g.key).join(','))
ok('超智组落点是 /chaozhi 且单页组（不渲染 pill 行）',
   NAV_GROUPS.find((g) => g.key === 'chaozhi').entry === '/chaozhi')
ok('桌面一级分组是 7 个（2026-10-03 起超智入列）', NAV_GROUPS.length === 7, '实际 ' + NAV_GROUPS.length)
// 2026-10-04 主人指令「app端右下角我的去掉」⇒ 5 格 → **4 格**（首页/竞价/盘中/复盘）。
ok('底部 tabbar 恰好 4 个 tab', (b.html.match(/tabbar-item/g) || []).length === 4,
   '实际 ' + (b.html.match(/tabbar-item/g) || []).length)
ok('底部 4 格 = 首页/竞价/盘中/复盘（「我的」2026-10-04 起已移除）',
   TABBAR_TABS.map((t) => t.label).join('/') === '首页/竞价/盘中/复盘',
   '实际 ' + TABBAR_TABS.map((t) => t.label).join('/'))
// 🔴 补偿断言：底部没了「我的」，手机端**必须**还有账户入口，否则 /member 无处可进
//   （该页有登录守卫）⇒ 顶部左上角用户中心（首字母头像，仅 ≤768px 渲染）。
const nb = await renderComp(NavBar, {}, '/')
ok('顶部左上角有用户中心入口（底部「我的」移除后的补偿）', nb.html.includes('nav-user-btn'),
   'NavBar 里找不到 nav-user-btn ⇒ 手机端账户入口丢失')
ok('用户中心已登录显示首字母头像 / 未登录显示 fa-user',
   nb.html.includes('nav-avatar-initial') || nb.html.includes('fa-user'),
   '两个分支都没渲染')
// 2026-10-04 系统消息：消息中心页必须渲染得出来, 且**失败态与空态文案不同**（静态闸门）
const ms = await renderComp(MessagesView, {}, '/messages')
ok('消息中心页渲染无异常/无警告', ms.errors.length === 0, ms.errors.join(' | '))
ok('消息中心有页头「系统消息」', ms.html.includes('系统消息'))
ok('消息中心空态/失败态文案不同（不都渲染成空列表）',
   ms.html.includes('暂无消息') || ms.html.includes('读取失败'),
   '两个状态文案都没渲染')
for (const t of TABBAR_TABS) {
  // ⚠️ 带 query 的落点（竞价 → /?wb=1&t=auction）在 SSR HTML 里 & 会转义成 &amp; ⇒ 归一化后比较
  const _href = `href="${t.path}"`
  ok(`AppTabBar 有 tab「${t.label}」→ ${t.path}`,
     b.html.includes(_href) || b.html.includes(_href.replace(/&/g, '&amp;')))
}
ok('底部不再渲染搜索格（搜索改顶栏常驻）', !b.html.includes('ss-tab'),
   '仍出现 ss-tab ⇒ 第 7 格没删干净')
// 失去底部入口的页面 ⇒ 必须能在首页宫格里找到（否则手机端没入口）。
//   ⚠️ 宫格在 StockView 里，上面的 `b` 只是**导航外壳** ⇒ 这里**直接渲染 QuickGrid**
//      （顺带把它的 SSR 安全也测了：SSR 下 localStorage 是桩对象，早期实现会把它带崩）。
const qg = await renderComp(QuickGrid, {}, '/')
ok('首页宫格渲染无异常/无警告', qg.errors.length === 0, qg.errors.join(' | '))
ok('宫格默认恰好 10 格', (qg.html.match(/data-qg="/g) || []).length === 10,
   '实际 ' + (qg.html.match(/data-qg="/g) || []).length)
// 2026-10-01 主人指定的 10 格（上排 5 + 下排 5）—— 按**顺序**钉死，防以后被悄悄改动
const _want10 = ['pick', 'zhpick', 'yijiner', 'auc', 'ai', 'spot', 'ladder', 'calc', 'news', 'themelib']
const _got10 = [...qg.html.matchAll(/data-qg="([^"]+)"/g)].map((m) => m[1])
ok('宫格 10 格内容与顺序 = 主人指定（竞价选股/竞价精选/竞价优选/竞价异动/超智研判 + 动态选股/连板天梯/异动计算器/盘前资讯/题材库）',
   _got10.join('/') === _want10.join('/'), '实际 ' + _got10.join('/'))
// 2026-10-03 主人指令：手机端宫格点「竞价异动」**不再弹子版块面板**，改为直跳 /auction
//   （与电脑端一致，版块在页内 9 个 tab 切）⇒ 带子项的格子数由 1 变 **0**。
//   同例：2026-10-01 超智研判也是从"子项面板"改为"直跳聚合页"。
ok('宫格 10 格**全部直跳**（竞价异动 2026-10-03 起也不再弹子项面板，与电脑端一致）',
   (qg.html.match(/data-qg-children="[1-9]/g) || []).length === 0,
   '实际带子项的格数 ' + (qg.html.match(/data-qg-children="[1-9]/g) || []).length)
// 2026-10-04 主人指令：删「快捷入口」标题与「编辑」按钮（宫格变纯入口，编辑机制保留在组件内无触发路径）
ok('宫格**不带**标题/编辑入口（2026-10-04 主人指令撤下）',
   !qg.html.includes('快捷入口') && !qg.html.includes('qg-edit'))
// 2026-10-01 图标重设计：自绘多色 SVG；**同日主人指定** 5 格改用**文字图标**
//   （竞价选股「选」/竞价精选「精」/竞价优选「优」/超智研判「智」/题材库「题材」）
ok('宫格图标 = 5 个自绘 SVG + 5 个文字图标',
   (qg.html.match(/class="qg-svg"/g) || []).length === 5 &&
   (qg.html.match(/class="qg-txt/g) || []).length === 5,
   '实际 svg=' + (qg.html.match(/class="qg-svg"/g) || []).length +
   ' txt=' + (qg.html.match(/class="qg-txt/g) || []).length)
for (const t of ['选', '精', '优', '智', '题材']) {
  ok(`宫格文字图标含「${t}」`, qg.html.includes(`>${t}</span>`))
}
ok('宫格不再依赖 FA 字形做图标（无 qg-ic 内的 fa-）',
   !/class="qg-ic[^"]*"[^>]*>\s*<i class="fa /.test(qg.html))
ok('色系按 token 走（出现 qg-h-* 且无裸 hex）',
   /qg-h-(red|cyan|purple|orange|green|pink|teal|gold|blue|lime)/.test(qg.html) &&
   !/#[0-9a-fA-F]{6}/.test(qg.html.replace(/var\(--qg-hi\)/g, '')))

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

// ★ 2026-10-05：下面两条断言（G5 数据源按钮 / 列头）与 G8 的「板块进阶数据」5 条**曾经长期红**，
//   逐条取证后确认**都不是产品坏了，而是产品有意重构、断言没跟上** —— 来源：
//     · v4.11.62→v4.11.71（2026-09-27~28）板块区重构：面板改为「左栏板块列表 + 右栏成分股」，
//       数据源按钮文案由「开盘啦榜 / 东财概念榜」缩成「开盘啦 / 东财」（MarketBoardPanel.vue:11/13），
//       原四列（题材/涨停数/涨幅%/主力净额(亿)）改为左栏「板块/板/强度/主力净」（:23-25）。
//     · 同一次重构把「板块进阶数据」折叠区（强度明细 / 轮动历史 / 人气热榜 三 tab + 日期回看工具条）
//       整体移出 /market，且当时**没留版本表条目**（v4.11.71 的 history 已把它记为"改动前既存失败"）。
//   处置原则：**不许为了把灯改绿而删断言** ——
//     ① 还在的能力 → 断言改成"当前真实文案/列头"；
//     ② 搬走的能力 → 去**新家**核对（读源码），证明是"搬走了"而不是"搬丢了"；
//     ③ 确实下线的能力 → 反向钉死"不许再出现"，并注明是哪个决定删的。
const __dirname = dirname(fileURLToPath(import.meta.url))
const SRC_MARKET_BOARD = readFileSync(resolve(__dirname, '../src/components/MarketBoardPanel.vue'), 'utf8')
const SRC_SECTOR_ROTATION = readFileSync(resolve(__dirname, '../src/components/SectorRotationPanel.vue'), 'utf8')
const SRC_HOT_RANK_MULTI = readFileSync(resolve(__dirname, '../src/components/HotRankMulti.vue'), 'utf8')

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
ok('数据源切换按钮齐备（开盘啦 / 东财）',
  g5.html.includes('开盘啦') && g5.html.includes('东财'))
ok('左栏列头齐备（板块 / 强度 / 主力净）',
  g5.html.includes('板块') && g5.html.includes('强度') && g5.html.includes('主力净'))
// 反向钉死：旧列头文案不许回来 —— 否则就是"重构做了一半、新旧两套并存"
ok('🔴 旧题材榜列头「涨停数」已不出现（现为行内「N板」）', !g5.html.includes('涨停数'))
// 按钮文案也做同样约束：旧的长文案不许复活（EmConceptPanel 里的「东财概念榜」属另一个组件）
ok('🔴 旧按钮文案「开盘啦榜」已不出现', !g5.html.includes('开盘啦榜'))
ok('sortBoardsByLimit: null 恒排最后', (() => {
  const s = sortBoardsByLimit([{ name: 'a', limitCount: null }, { name: 'b', limitCount: 1 }])
  return s[0].name === 'b'
})())
const g5e = await renderComp(MarketBoardPanel, { boards: [], src: 'kpl' })
ok('空板块榜有明确文案（不冒充有数据）', g5e.html.includes('暂无板块数据'))

// G6 ⑥ 实时异动流 —— 🔴 2026-09-28 主人拍板**整块移除**（组件 YidongFlow.vue 已删除 ⇒ 渲染不出来），
//   故原「四态渲染 + 文案」断言整块删除；「不许再加回来」的反向守卫见 G13。
const ydItems = []

// G7 盯盘台不得在组件里注册轮询（轮询统一在 MarketView 的 tick 里）
console.log('\n— G7. 六层渲染结果整体扫描')
for (const [name, res] of [['FlashTicker', g1], ['YestZtPanel', g2], ['MoneyTopStrip', g3],
  ['TodayPicksPanel', g4], ['MarketBoardPanel', g5]]) {
  ok(`${name} 渲染结果不含 "undefined"`, !res.html.includes('undefined'))
  ok(`${name} 渲染结果不含 "NaN"`, !res.html.includes('NaN'))
}

// G8 /market 盯盘台**本体**（不是子组件）——★ v4.11.62 最高风险文件
//   G1~G6 只证明六个子组件各自能渲染；但 MarketView 自己模板里也有几十个自由变量
//   （flashList/yestCount/pickRows/boardRows/hotBoard/updatedAt/...），
//   **错拼同样不会被 build 与 eslint 发现**，只有真渲染才暴露 ⇒ 必须单独渲染它。
//   props 为空、onMounted 不执行 ⇒ 六层拿到的都是空数据，正好顺带验证「空态不许造假」。
console.log('\n— G8. MarketView 盯盘台本体（五层编排 + 既有能力一件不丢）')
const g8 = await renderComp(MarketView, {}, '/market')
ok('MarketView 渲染无异常/无 Vue 警告', g8.errors.length === 0, g8.errors.join(' | '))
ok('含页头 盘中盯盘台', g8.html.includes('盘中盯盘台'))
ok('含五层栈容器 .mk-stack', g8.html.includes('mk-stack'))
ok('含刷新戳 .mk-updated', g8.html.includes('mk-updated'))
// 五层：空数据态下各层**仍须各自渲染出标题**（某层若自由变量错拼，这层会整块消失）
for (const t of ['快讯', '昨日涨停今日表现', '按主力净额排序', '今日票战报', '题材榜']) {
  ok(`五层标题「${t}」`, g8.html.includes(t))
}
// ★ v4.11.71：原第 ⑥ 层「实时异动流」已从盯盘台**整层移除**（模板早搬到 /yidong）。
//   这里反向钉死：不得再出现该层，也不得出现它的副标题（防「顺手搬回来」）。
ok('🔴 盯盘台不再含第 ⑥ 层「实时异动流」（v4.11.71 整层移除）',
  !g8.html.includes('实时异动流') && !g8.html.includes('按累计偏离值排序'))
// 🔴 2026-10-05：本节原为「既有能力一件不丢」（折叠区 + 三 tab + 强度工具条），
//   实测这 5 条**全部过期**：「板块进阶数据」折叠区已在 v4.11.62→v4.11.71 的板块区重构中
//   **整体移出 /market**（MarketView.vue:94 留有墓碑注释），且当时没留版本表条目。
//   处置原则（不许为了把灯改绿而删断言）：
//     ① 搬走的 → **去新家核对**，证明是"搬走了"而不是"搬丢了"；
//     ② 确实下线的 → **反向钉死**不许再出现，并注明去向/结论。
//   去向：板块轮动历史 → /history 的 SectorRotationPanel（文案逐字未变）；
//        人气热榜 → /news 的 HotRankMulti（标题改成「热门个股」）；
//        板块强度明细（11 列 + 日期回看工具条）→ ⚠️ **无等价替代**，已记入 CHANGELOG「本轮遗留」。
ok('🔴 盯盘台不再渲染「板块进阶数据」折叠区（09-27~09-28 重构移出）',
  !g8.html.includes('板块进阶数据'))
ok('🔴 盯盘台不再渲染三个旧 tab（强度明细 / 轮动历史 / 人气热榜）',
  !['板块强度明细', '板块轮动历史', '人气热榜'].some((t) => g8.html.includes(t)))
ok('🔴 旧强度表工具条「实时板块强度排行」已不存在',
  !g8.html.includes('实时板块强度排行'))
// 去向守卫：能力搬到了别处 ⇒ 必须在新家找得到（这两条是本节唯一还能抓"搬丢了"的判据）
ok('板块轮动历史仍在（已搬到 /history 的 SectorRotationPanel）',
  SRC_SECTOR_ROTATION.includes('板块轮动历史'), '若这里也找不到 = 能力被搬丢了')
ok('人气热榜仍在（已搬到 /news 的 HotRankMulti，标题「热门个股」）',
  SRC_HOT_RANK_MULTI.includes('热门个股'), '若这里也找不到 = 能力被搬丢了')
// ⚠️ SSR 初始态 boardLoading=true ⇒ 层⑤走「加载占位」分支（不是空态文案）；
//    两者都是「不冒充有数据」的诚实态，这里断言的是实际该出现的那一个。
//    （文案随重构由「加载板块强度」变为「加载板块榜…」，见 MarketBoardPanel.vue:16）
ok('初始态 → 加载占位（不冒充有数据）', g8.html.includes('加载板块榜'))
ok('MarketView 渲染结果不含 "undefined"', !g8.html.includes('undefined'))
ok('MarketView 渲染结果不含 "NaN"', !g8.html.includes('NaN'))

// ==================== G9. 全局股票搜索（v4.11.63 · 《移动端清单》§三「🔍 跳股」） ====================
//   面板 StockSearchPanel 是**纯展示组件**（零状态零请求），所以四态能用夹具各渲一遍；
//   入口 StockSearch 是容器（防抖/请求/键盘/定位），SSR 只验证"关闭态能渲染、不崩"。
console.log('\n— G9. 股票搜索面板（五态互斥）与两处入口')

const ssRows = [
  { code: '605058', name: '澳弘电子', board: '沪主板', py: 'AHDZ' },
  { code: '002466', name: '天齐锂业', board: '深主板', py: 'TQLY' },
  { code: '300750', name: '宁德时代', board: '创业板', py: 'NDSD' },
]
const SS = (p) => renderComp(StockSearchPanel, { variant: 'nav', kw: '', rows: [], phase: 'idle', ...p })

// ① ok 态
const g9 = await SS({ kw: 'ah', rows: ssRows, phase: 'ok', activeIdx: 0 })
ok('Panel 渲染无异常/无 Vue 警告', g9.errors.length === 0, g9.errors.join(' | '))
ok('三条结果各一行', countByClass(g9.html, 'ss-row') === 3,
  '实际 ' + countByClass(g9.html, 'ss-row'))
ok('结果里代码/名称/板块/拼音首字母四要素齐备',
  g9.html.includes('605058') && g9.html.includes('澳弘电子') && g9.html.includes('沪主板') && g9.html.includes('AHDZ'))
ok('activeIdx=0 ⇒ 恰好一条高亮', (g9.html.match(/aria-selected="true"/g) || []).length === 1 &&
  (g9.html.match(/aria-selected="false"/g) || []).length === 2)
ok('底部透出条数', g9.html.includes('共 3 条'))
ok('listbox/option 语义齐备', g9.html.includes('role="listbox"') && g9.html.includes('role="option"'))

// ② 空结果 与 ③ 服务失败 —— ★ 两者必须一眼可分（本项目的头号缺陷类型就是"静默"）
const g9e = await SS({ kw: '不存在票', rows: [], phase: 'empty' })
const g9f = await SS({ kw: '600001', rows: [], phase: 'err', errMsg: '请求失败(500)' })
ok('空结果：明说未找到 + 回显关键词', g9e.html.includes('未找到') && g9e.html.includes('不存在票'))
ok('失败：明说服务不可用 + 带原因 + 给重试', g9f.html.includes('搜索服务暂不可用') &&
  g9f.html.includes('请求失败(500)') && g9f.html.includes('重试'))
ok('🔴 空结果与失败文案必须不同（不许都渲染成一个空列表）',
  !g9e.html.includes('暂不可用') && !g9f.html.includes('未找到'))
ok('失败态不含结果行', countByClass(g9f.html, 'ss-row') === 0)

// ④ loading：首屏无结果时显示"搜索中"
const g9l = await SS({ kw: '60', rows: [], phase: 'loading' })
ok('首屏搜索中态明确', g9l.html.includes('搜索中'))

// ④b loading 但已有旧结果 ⇒ 保留旧列表（否则每敲一个字列表就闪一下空）
const g9l2 = await SS({ kw: '605', rows: ssRows, phase: 'loading', activeIdx: 0 })
ok('已有结果时 loading 不盖掉旧列表', countByClass(g9l2.html, 'ss-row') === 3 &&
  !g9l2.html.includes('搜索中'))

// ⑤ idle：给三种用法示例（代码/名称/拼音首字母）
const g9i = await SS({ kw: '   ', rows: [], phase: 'idle' })
ok('未输入时给出三种用法示例', g9i.html.includes('拼音首字母') &&
  g9i.html.includes('605058') && g9i.html.includes('ahdz'))
ok('未输入时没有结果行', countByClass(g9i.html, 'ss-row') === 0)

// ⑥ 变体差异：nav 的输入框长在导航栏里（面板不重复渲染）；tabbar 的面板自带输入框
const g9t = await SS({ variant: 'tabbar', kw: '', rows: [], phase: 'idle' })
ok('tabbar 变体：面板自带输入框', g9t.html.includes('ss-panel-search') &&
  g9t.html.includes('type="search"') && g9t.html.includes('代码 / 名称 / 拼音首字母'))
ok('nav 变体：面板不自带输入框（避免出现两个输入框）', !g9.html.includes('ss-panel-search'))

// ⑦ 入口两处（容器组件，关闭态）
const g9c = await renderComp(StockSearch, { variant: 'nav' })
ok('桌面入口渲染无异常/无警告', g9c.errors.length === 0, g9c.errors.join(' | '))
ok('桌面入口是内联输入框', g9c.html.includes('ss-inline-input') &&
  g9c.html.includes('搜索代码 / 名称 / 拼音'))
ok('关闭态不产出结果面板', !g9c.html.includes('ss-panel'))
const g9d = await renderComp(StockSearch, { variant: 'tabbar' })
ok('底部入口渲染无异常/无警告', g9d.errors.length === 0, g9d.errors.join(' | '))
ok('底部入口是「搜索」按钮（不是路由项）', g9d.html.includes('ss-tab') && g9d.html.includes('搜索'))
// 2026-10-01: AppTabBar **不再挂搜索格**（无 ss-tab）；搜索改由顶栏常驻承担。
// 2026-10-04: 底部同时**去掉「我的」** ⇒ 格数 5 → **4**（此处同步，否则与上面的 4 格断言打架）。
ok('AppTabBar 已移除搜索格（4 格 + 无 ss-tab）',
  (b.html.match(/tabbar-item/g) || []).length === 4 && !b.html.includes('ss-tab'),
  'tabbar-item=' + (b.html.match(/tabbar-item/g) || []).length)
// NavBar 里挂上了桌面入口
ok('NavBar 内已挂搜索入口', b.html.includes('ss-inline-input'))

// ⑧ 整体扫描：面板 HTML 不得出现 undefined / NaN
console.log('\n— G9 附. 渲染结果扫描')
for (const [n, r] of [['ok', g9], ['empty', g9e], ['err', g9f], ['loading', g9l], ['idle', g9i]]) {
  ok(`搜索面板(${n}) 不含 "undefined"`, !r.html.includes('undefined'))
  ok(`搜索面板(${n}) 不含 "NaN"`, !r.html.includes('NaN'))
}

// ==================== G10. 数据更新时刻（v4.11.63 · 《移动端清单》§二·4） ====================
//   与页头那个每秒跳的时钟是两回事：本戳只在**成功取到数据**时前进。
//   所以这里重点验三态（未更新过 / 已更新 / 当前无自动刷新）都不许说假话。
console.log('\n— G10. DataStamp 数据更新时刻（三态）')

const g10 = await renderComp(DataStamp, { at: '14:32:05', ok: true, interval: 30 })
ok('DataStamp 渲染无异常/无警告', g10.errors.length === 0, g10.errors.join(' | '))
ok('已更新 → 「更新于 HH:MM:SS」', g10.html.includes('更新于 14:32:05'))
ok('在自动刷新 → 附带间隔说明', g10.html.includes('每 30s 自动刷新'))

const g10b = await renderComp(DataStamp, { at: '', ok: false, interval: 30 })
ok('尚未成功取到数据 → 明说等待，不编时间', g10b.html.includes('等待首次更新') && !g10b.html.includes('更新于'))
ok('🔴 未更新过时绝不渲染 00:00:00 冒充已更新', !g10b.html.includes('00:00:00'))

const g10c = await renderComp(DataStamp, { at: '20:05:00', ok: true, interval: 0 })
ok('无自动刷新时（收盘 / 历史回看）不出现「自动刷新」字样',
  g10c.html.includes('更新于 20:05:00') && !g10c.html.includes('自动刷新'))

for (const [n, r] of [['已更新', g10], ['未更新', g10b], ['无轮询', g10c]]) {
  ok(`DataStamp(${n}) 不含 "undefined"`, !r.html.includes('undefined'))
  ok(`DataStamp(${n}) 不含 "NaN"`, !r.html.includes('NaN'))
}

// ==================== G11. 异动 / 停牌风险（v4.11.64 · 工单批次三） ====================
//   两个纯展示组件 + `/yidong` 本体。重点盯三件事：
//     ① `ok:false`（后端刻意用它表达"算不出"）**不许**被渲染成一堆 0 —— 那就是"静默造假"；
//     ② 「读取失败」与「今天没有风险」文案必须不同（本项目头号缺陷类型）；
//     ③ 三 tab 改名后，「热门股偏离值 / 多次异动」两个已删 tab 不许残留。
console.log('\n— G11. 异动风险名单 DevWarnList（四态）')

// `lb` / `lbDate`（连板高度标签）：2026-09-28 起异动名单也下发与选股名单同一口径的标签。
//   这里刻意一只 lb=1（渲染「昨首板」）、一只 lb=0（**不渲染**，连 lv0 类名都不该出现）。
const devRows = [
  { code: '605058', name: '澳弘电子', board: '沪深主板', board_key: 'main_sh', price: 21.5,
    today_dev: 1.24, d3: 25.86, d3_status: '触发', d10: 99.99, d10_status: '临近',
    d30: 126.79, d30_status: '安全', next_trigger_pct: null, trigger_price: null,
    rule: '', reachable: 0, warn_level: 'red', warn_msg: '已触发10日偏离值异动线',
    lb: 1, lbDate: '2026-09-23' },
  { code: '300750', name: '宁德时代', board: '创业板', board_key: 'gem', price: 188.2,
    today_dev: 3.4, d3: 28.1, d3_status: '临近', d10: 61.2, d10_status: '安全',
    d30: 90.5, d30_status: '安全', next_trigger_pct: 1.42, trigger_price: 190.87,
    rule: '10日+100%', reachable: 1, warn_level: 'yellow', warn_msg: '明日涨 1.42% 即触发10日+100%',
    lb: 0, lbDate: '2026-09-23' },
]

const g11 = await renderComp(DevWarnList, { rows: devRows, date: '2026-09-24' }, '/yidong')
ok('DevWarnList 渲染无异常/无警告', g11.errors.length === 0, g11.errors.join(' | '))
ok('汇总条给出结果日期（非交易日看到的是上一交易日，必须显式）', g11.html.includes('2026-09-24'))
ok('红/黄计数分别统计', g11.html.includes('红级 1') && g11.html.includes('黄级 1'))
ok('两级各渲染一行', countByClass(g11.html, 'dev-lv') === 2,
  '实际 ' + countByClass(g11.html, 'dev-lv'))
ok('级别徽章按 level 上色（红/黄各一）',
  countByClass(g11.html, 'dev-lv-red') === 1 && countByClass(g11.html, 'dev-lv-yellow') === 1)
ok('带出代码与名称', g11.html.includes('605058') && g11.html.includes('澳弘电子') &&
  g11.html.includes('300750') && g11.html.includes('宁德时代'))
ok('🔴 连板高度标签：lb=1 渲染「昨首板」（与选股名单同一套 .lb-tag）',
  g11.html.includes('昨首板') && g11.html.includes('lb-tag-lv1'))
ok('🔴 连板标签：lb=0 完全不渲染（不得出现 lv0 类名，更不得出现「新启动」）',
  !g11.html.includes('lb-tag-lv0') && !g11.html.includes('新启动'))
ok('两条线的值/状态都渲染（10/30；3日列已于 2026-09-28 移除）',
  g11.html.includes('+99.99%') && g11.html.includes('+126.79%'))
ok('🔴 3日 不得再出现在表头或单元格（夹具自带的 d3 值也不许渲染）',
  !g11.html.includes('3日') && !g11.html.includes('+25.86%'))
ok('明日触发涨幅：可达者给出数值 + 规则', g11.html.includes('1.42%') && g11.html.includes('10日+100%'))
ok('明日触发涨幅为空时渲染「—」而不是 0', g11.html.includes('—'))
ok('🔴 渲染结果不含 "undefined"', !g11.html.includes('undefined'))
ok('🔴 渲染结果不含 "NaN"', !g11.html.includes('NaN'))

// ② 空：明说「没有风险」并解释名单生成时刻；③ 失败：明说读取失败 —— ★ 两者不许同文案
const g11e = await renderComp(DevWarnList, { rows: [], date: '2026-09-24' }, '/yidong')
ok('空名单：说明当前无触发/临近，并给出生成时刻解释',
  g11e.html.includes('当前没有触发或临近'))
const g11f = await renderComp(DevWarnList, { rows: [], failed: true, date: '' }, '/yidong')
ok('失败态：明说「读取失败」并点明不是「今天没有风险」',
  g11f.html.includes('读取失败') && g11f.html.includes('不是「今天没有风险」'))
ok('🔴 失败与空名单文案必须不同（不许都渲染成一个空列表）',
  !g11e.html.includes('读取失败') && !g11f.html.includes('当前没有触发或临近'))
const g11l = await renderComp(DevWarnList, { rows: [], loading: true }, '/yidong')
ok('加载态：给出「加载异动风险名单」', g11l.html.includes('加载异动风险名单'))

console.log('\n— G11. 异动计算器 DevRiskDetail（算得出 / 算不出 / 未计算）')

const devFixture = {
  ok: true, code: '605058', name: '澳弘电子', board: '沪深主板', board_key: 'main_sh',
  index: '000002', index_name: '上证A指', price: 21.5, date: '2026-09-24',
  limit_up_pct: 10.0, today_dev: 1.24,
  // 🔴 2026-09-28：夹具改为 **10/30 两条线**（3 日维度已移除，但刻意**保留 d3/detail[3]** ——
  //   后端数据链路不动、仍会返回，用它来验证「即使数据里有 3 日，计算器也不画出来了」）。
  //   d10 触发 + d30 临近 ⇒ 两张卡正好覆盖 dd-hit / dd-near 两种 ascii 类名。
  dev: {
    d3: { value: 25.86, thresh: 20, status: '触发' },
    d10: { value: 105.6, thresh: 100, status: '触发' },
    d30: { value: 196.4, thresh: 200, status: '临近' },
  },
  detail: {
    3: { value: 25.86, stock_pct: 27.1, idx_pct: 1.24, window: '2026-09-22→2026-09-24', base_date: '2026-09-19' },
    10: { value: 105.6, stock_pct: 106.8, idx_pct: 1.21, window: '2026-09-11→2026-09-24', base_date: '2026-09-10' },
    30: { value: 196.4, stock_pct: 199.6, idx_pct: 3.21, window: '2026-08-14→2026-09-24', base_date: '2026-08-13' },
  },
  window: { 3: '2026-09-22 → 2026-09-24（期初前 2026-09-19）', 10: '', 30: '' },
  room: { next_trigger_pct: 8.84, trigger_price: 23.4, rule: '10日+100%', reachable: true,
          limit_up_pct: 10, hit: [] },
  // ★ v4.11.71 起 project10 为**实基倒推**口径（不再是「假设天天涨停」的虚值）：
  //   need10/need30 = 累计所需涨幅%（自今日收盘起，复利）；None = 10 日内不可能；
  //   safe_gain_pct/price = 只在「靠连板真能做到」时才有值，否则 null。
  project10: [
    { day: 1, date: '2026-09-25', limit_up_pct: 10.0,
      need3: 8.84, need10: null, need30: null,
      safe_gain_pct: null, price: null,
      trigger: '不触发', trigger_rule: '无', zt_trigger: false,
      dev10: 12.4, dev30: 18.9, left10: 4, left30: null },
    { day: 2, date: '2026-09-28', limit_up_pct: 10.0,
      need3: 14.29, need10: null, need30: null,
      safe_gain_pct: null, price: null,
      trigger: '不触发', trigger_rule: '无', zt_trigger: false,
      dev10: 24.9, dev30: 40.2, left10: 3, left30: null },
  ],
  warn: { level: 'red', msg: '已触发10日偏离值异动线' },
}

const g11d = await renderComp(DevRiskDetail, { data: devFixture, code: '605058' }, '/yidong')
ok('DevRiskDetail 渲染无异常/无警告', g11d.errors.length === 0, g11d.errors.join(' | '))
ok('表头带出名称/代码/板块/对应指数/涨停幅度',
  g11d.html.includes('澳弘电子') && g11d.html.includes('605058') &&
  g11d.html.includes('沪深主板') && g11d.html.includes('上证A指') && g11d.html.includes('000002'))
ok('两条偏离线各渲染一张卡（10/30 日；3 日维度已于 2026-09-28 移除）',
  countByClass(g11d.html, 'dd-card') === 2, '实际 ' + countByClass(g11d.html, 'dd-card'))
ok('🔴 计算器不得再渲染 3 日线（夹具自带 d3/detail[3] 也不许画出来）',
  !g11d.html.includes('3 日偏离值') && !g11d.html.includes('三条偏离') &&
  !g11d.html.includes('3日±') && !g11d.html.includes('+25.86%'))
ok('两条线状态用 ascii 类名上色（触发→dd-hit、临近→dd-near）',
  countByClass(g11d.html, 'dd-hit') >= 1 && countByClass(g11d.html, 'dd-near') >= 1)
// ★ 2026-09-28 修复了「阈值恒显示 —、进度条恒 0%」的既有缺陷（阈值在后端 `dev.dN.thresh`，
//   组件原先只读 `detail[n].thresh`）。以下同时钉住「偏离值 / 阈值 / 区间」，防回退。
ok('两条线各带偏离值/阈值/个股区间/指数区间',
  g11d.html.includes('+105.60%') && g11d.html.includes('/ 阈值 100%') &&
  g11d.html.includes('+106.80%') && g11d.html.includes('+1.21%') &&
  g11d.html.includes('+196.40%') && g11d.html.includes('/ 阈值 200%') &&
  g11d.html.includes('+199.60%') && g11d.html.includes('+3.21%'))
ok('🔴 进度条按「偏离值/阈值」计算（阈值取不到时会退化成恒 0%）',
  /width:100\.0%/.test(g11d.html) && /width:98\.2%/.test(g11d.html))
ok('明日触发空间：8.84% + 触发价 23.40 + 规则（10/30 口径）', g11d.html.includes('8.84%') &&
  g11d.html.includes('23.40') && g11d.html.includes('10日+100%'))
ok('hit 为空且未越涨停 ⇒ 提示「明日单日不可能触发」',
  g11d.html.includes('超过一个涨停幅度'))
ok('★ 未来十日推演表渲染 2 行（实基倒推口径）', g11d.html.indexOf('dd-table') >= 0 &&
  (g11d.html.match(/2026-09-25|2026-09-28/g) || []).length >= 2)
ok('🔴 推演表头必须是「未来十日推演」，不得再出现「投影」/「假设个股每日」虚值文案',
  g11d.html.includes('未来十日推演') &&
  !g11d.html.includes('未来十日投影') &&
  !g11d.html.includes('假设个股每日'))
ok('🔴 列头改为「需日均涨」（不再是「安全涨幅」）',
  g11d.html.includes('需日均涨') && !g11d.html.includes('>安全涨幅<'))
ok('★ 不可达时 safe_gain_pct=null ⇒ 显示 —（不得显示 0%，会被读成「不涨就触发」）',
  !/需日均涨[\s\S]{0,400}?\+0\.00%/.test(g11d.html))
ok('🔴 渲染结果不含 "undefined"', !g11d.html.includes('undefined'))
ok('🔴 渲染结果不含 "NaN"', !g11d.html.includes('NaN'))

// hit 非空 ⇒ 头部改成「明日涨停即触发」
const g11h = await renderComp(DevRiskDetail, {
  data: { ...devFixture, room: { ...devFixture.room, hit: ['10日+100%'] } }, code: '605058',
}, '/yidong')
ok('明日涨停即触发时点明「明日涨停即触发」', g11h.html.includes('明日涨停即触发'))

// ★ 算不出：ok:false + reason —— 必须翻成人话，且**绝不能**渲染成一堆 0
const g11x = await renderComp(DevRiskDetail, {
  data: { ok: false, code: '899050', reason: 'index_unavailable', msg: '对应指数数据不足' },
  code: '899050',
}, '/yidong')
ok('算不出时渲染 reason 的人话文案（弃权而非猜）', g11x.html.includes('指数数据取不到'))
ok('🔴 算不出时**不得**渲染三条线（否则等于把"不知道"画成 0）',
  countByClass(g11x.html, 'dd-card') === 0)
ok('🔴 算不出时不含 "+0.00%"（不许用 0 冒充安全）', !g11x.html.includes('+0.00%'))

const g11n = await renderComp(DevRiskDetail, { data: null, code: '' }, '/yidong')
ok('未填代码时提示输入', g11n.html.includes('输入 6 位股票代码'))

console.log('\n— G11. /yidong 本体（防模板自由变量错拼 —— 本测试抓到过两次同类事故）')
const g11v = await renderComp(YidongView, {}, '/yidong')
ok('YidongView 渲染无异常/无 Vue 警告', g11v.errors.length === 0, g11v.errors.join(' | '))
for (const label of ['严重异动', '异动计算器', '重点监控']) {
  ok(`三 tab 含「${label}」`, g11v.html.includes(label))
}
ok('恰有 3 个 tab', countByClass(g11v.html, 'yd-tab') === 3,
  '实际 ' + countByClass(g11v.html, 'yd-tab'))
ok('🔴 已删 tab 不留残留：「热门股偏离值」', !g11v.html.includes('热门股偏离值'))
ok('🔴 已删 tab 不留残留：「多次异动」', !g11v.html.includes('多次异动'))
ok('🔴 旧名「个股计算器」已彻底改名（主人 2026-09-28 要求）',
  !g11v.html.includes('个股计算器'))
ok('副标题已同步三 tab', g11v.html.includes('严重异动 · 异动计算器 · 重点监控'))
ok('首屏落在「严重异动」：渲染的是名单区（.dev-bar/加载态），不是计算器',
  g11v.html.includes('加载异动风险名单') && !g11v.html.includes('cal-input'))
ok('🔴 本体渲染不含 "undefined"', !g11v.html.includes('undefined'))
ok('🔴 本体渲染不含 "NaN"', !g11v.html.includes('NaN'))

// 🔴 2026-09-28 主人拍板：`/yidong` 置顶的「实时异动流」**整块移除**（组件 YidongFlow.vue 已删除、
//   取数一并删除）⇒ 原「必须渲染出面板 + 必须传 items」的接线断言，改为**反向守卫**：
//   面板不许再出现、源码不许再残留 YidongFlow / flowList / loadFlow（防止将来误加回来）。
console.log('\n— G13. /yidong 置顶实时异动流已移除（2026-09-28）')
ok('🔴 YidongView 首屏不得再渲染实时异动流面板（.yd-flow / .yf-*）',
  !g11v.html.includes('实时异动流') && !g11v.html.includes('yf-') && !g11v.html.includes('yd-flow'))
ok('🔴 YidongView 源码不得再残留 YidongFlow / flowList / loadFlow（取数已删）',
  !stripComments(YidongViewSrc).includes('YidongFlow') &&
  !stripComments(YidongViewSrc).includes('flowList') &&
  !stripComments(YidongViewSrc).includes('loadFlow'),
  '剥离注释后仍命中')
ok('🔴 严重异动里「交易所已公布异动」折叠表仍须调 kplYidongRealtime（同一接口的另一处在用，别误删）',
  YidongViewSrc.includes('kplYidongRealtime'))
ok('🔴 MarketView 源码不得再残留 kplYidongRealtime / YidongFlow（死代码已清）',
  // ⚠️ 必须**剔除注释**再断言：实现里刻意留了「为什么移除」的说明注释（含这两个名字），
  //    直接 substring 会把「解释」误判成「残留」——本项目已多次踩「grep 命中自己的注释」。
  !stripComments(MarketViewSrc).includes('kplYidongRealtime') &&
  !stripComments(MarketViewSrc).includes('YidongFlow'),
  '剥离注释后仍命中')

// 计算器分支必须**真的渲染一次**才谈得上被覆盖（它是纯 DOM 交互分支）
const g11vc = await renderComp(YidongView, { initialTab: 'calc' }, '/yidong')
ok('计算器 tab 渲染无异常/无 Vue 警告', g11vc.errors.length === 0, g11vc.errors.join(' | '))
ok('计算器入口（6 位代码输入框 + 计算按钮）在场', g11vc.html.includes('cal-input') &&
  g11vc.html.includes('输入 6 位股票代码') && g11vc.html.includes('cal-btn'))
ok('未填代码时给提示而不是空白', g11vc.html.includes('输入 6 位股票代码开始计算'))
ok('计算器分支不含 "undefined"', !g11vc.html.includes('undefined'))
ok('计算器分支不含 "NaN"', !g11vc.html.includes('NaN'))
const g11vm = await renderComp(YidongView, { initialTab: 'monitor' }, '/yidong')
ok('重点监控 tab 渲染无异常/无警告', g11vm.errors.length === 0, g11vm.errors.join(' | '))
ok('重点监控 tab 渲染的是监控表（加载态在场）', g11vm.html.includes('加载重点监控'))

console.log('\n— G12. 「我的」账户区块（v4.11.65 由顶部 NavBar 整块迁入）')
const mv = await renderComp(MemberView, {}, '/member')
ok('MemberView 渲染无异常/无 Vue 警告', mv.errors.length === 0, mv.errors.join(' | '))
ok('账户卡片在场（.mb-acc-card）', countByClass(mv.html, 'mb-acc-card') === 1,
  '实际 ' + countByClass(mv.html, 'mb-acc-card'))
for (const t of ['账户', '个人信息', '修改密码', '退出登录', '字号', '字体']) {
  ok(`账户卡片含「${t}」`, mv.html.includes(t))
}
ok('账户卡片渲染 3 档字号按钮', countByClass(mv.html, 'mb-set-btn') === 3,
  '实际 ' + countByClass(mv.html, 'mb-set-btn'))
ok('账户卡片渲染 4 个字体族选项', countByClass(mv.html, 'mb-fontfam') === 4,
  '实际 ' + countByClass(mv.html, 'mb-fontfam'))
// 2026-10-05 (S5): 新增「系统字体」并置于首位 —— 默认路径零网络字体下载
ok('字体族四选都在（系统字体 / 思源黑体 / 思源宋体 / 霞鹜等宽）',
  ['系统字体', '思源黑体', '思源宋体', '霞鹜等宽'].every((s) => mv.html.includes(s)))
// ★ 本卡片刻意放在 loading 判断之外 —— 会员接口慢/挂了也必须能改密、能退出登录
ok('🔴 账户卡片在 loading 闸门之外（接口未返回时也已渲染）',
  mv.html.includes('加载会员信息') && mv.html.includes('mb-acc-card'))
ok('🔴 MemberView 渲染不含 "undefined"', !mv.html.includes('undefined'))
ok('🔴 MemberView 渲染不含 "NaN"', !mv.html.includes('NaN'))

// 顶部导航必须"瘦身"到位：用户名按钮 / 账户下拉项一律不许再出现（否则就是"从顶部移除"没做干净）
const navMember = await renderAt('/member')
ok('顶部导航不再有用户名按钮（.user-name-btn）', !navMember.html.includes('user-name-btn'))
ok('顶部导航不再有账户下拉项（修改密码 / 退出登录）',
  !navMember.html.includes('修改密码') && !navMember.html.includes('退出登录'))
// 2026-10-04 主人指令「深色和浅色背景 转移到用户中心里面」⇒ 断言方向**反转**：
//   顶部**不许**再有主题圆点，且会员页必须提供了「深色/浅色」两个选项
//   （不写这条，以后有人把圆点加回顶栏、或搬进却漏了一个选项，都不会被拦住）。
ok('🔴 顶部导航已移除主题圆点（迁入用户中心）', !navMember.html.includes('nav-theme-dot'),
  '仍有 nav-theme-dot ⇒ 背景切换没从顶栏搬走')
ok('用户中心有「背景」设置项', mv.html.includes('mb-bg-btn'))
ok('背景两档都在（深色 / 浅色）', ['深色', '浅色'].every((s) => mv.html.includes(s)),
  '缺少某一档 ⇒ 用户换不了该背景')

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
