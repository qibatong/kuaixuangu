/**
 * 导航渲染冒烟测试（SSR 版，无需浏览器）—— 2026-09-27 v4.11.60 新增
 *
 * 为什么需要它：
 *   v4.11.58 的 `GroupNav.vue` 里有一行 `void NAV_GROUPS`，而该标识符**没有被 import**。
 *   `<script setup>` 的顶层语句会被编译进 setup()，于是运行时抛
 *   `ReferenceError: NAV_GROUPS is not defined` ⇒ **二级导航 pill 行整块不渲染**
 *   （桌面只剩 5 个一级入口；手机端点「复盘」只落到 /ladder，组内页面全部不可达）。
 *   这个缺陷：构建成功、无 warning、静态一致性自检全绿、发布校验 8 项全过 —— **只有真渲染才暴露**。
 *
 * 本测试做的事：
 *   用 vue/server-renderer 在 Node 里把 NavBar / GroupNav / AppTabBar **真的渲染成 HTML**，
 *   断言关键导航项存在、且渲染过程中零异常零 Vue 警告。
 *   因此它能拦住「setup 抛异常 / 模板引用不存在的变量 / 分组数据与路由不匹配」这一整类问题。
 *
 * 跑法（两步，见 frontend/package.json 的 `test:nav`）：
 *   vite build --ssr _verify/nav.spec.js --outDir /tmp/kx_navssr --emptyOutDir
 *   node /tmp/kx_navssr/nav.spec.js
 */

import { createSSRApp, h } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { renderToString } from 'vue/server-renderer'
import GroupNav from '../src/components/GroupNav.vue'
import NavBar from '../src/components/NavBar.vue'
import AppTabBar from '../src/components/AppTabBar.vue'
import { NAV_GROUPS } from '../src/composables/useNavGroups'

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

console.log(`\n=== 结果：PASS=${PASS}  FAIL=${FAIL} ===`)
if (FAIL) { console.log('失败项：\n  - ' + fails.join('\n  - ')); process.exit(1) }
console.log('导航渲染冒烟测试全绿。\n')
}

main().catch((e) => {
  console.error('\n[FATAL] 冒烟测试自身异常：', e && e.stack ? e.stack : e)
  process.exit(2)
})
