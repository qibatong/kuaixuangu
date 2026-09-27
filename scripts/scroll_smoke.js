#!/usr/bin/env node
/**
 * 发布前「滚动 + 导航 IA」浏览器冒烟探针（Chromium 真实滚轮 / 真实触摸）
 *
 * 为什么需要它（2026-09-27 v4.11.65 事故）：
 *   v4.11.63 写了 `html, body { overscroll-behavior: none }`，导致**全站每一页都滑不动**。
 *   这个缺陷：`npm run build` 成功、eslint 0 error、SSR 冒烟 244 项全过、发布校验 8 项全过
 *   —— 因为**CSS 的滚动链行为只有真浏览器才看得见**，静态检查与 SSR 都碰不到。
 *   同类教训在本仓库已出现多次：能"编译通过"与"用户能用"是两件事。
 *   因此本探针专盯两件静态测试覆盖不到的事：
 *     ① 页面**真的能上下滚动**（桌面滚轮 + 手机触摸，两种手势分别验）；
 *     ② 手机端二级 pill 行**真的完整可见**（不是"渲染出来了但在屏幕外被裁掉"——
 *        v4.11.65 前「异动监管」pill 就落在 390px 屏的 341~413 处，右侧被裁）。
 *
 * 依赖（本文件不在 npm 依赖里，故意如此：它只在"要发布前端时"才需要）：
 *   · playwright-core  —— 建议装在 WorkBuddy 托管 node 工作区：
 *       mkdir -p ~/.workbuddy/binaries/node/workspace && cd $_ && npm install playwright-core
 *   · 一个 Chromium 可执行文件 —— 优先 $KX_CHROME，其次 playwright-core 期望的版本，
 *     再次扫描 ~/Library/Caches/ms-playwright/ 下的 chromium-* 目录（见 findChrome()）。
 *
 * 跑法（在仓库根执行）：
 *   node scripts/scroll_smoke.js
 *   # 本机若走 HTTP 代理，必须绕开（否则 127.0.0.1 会被代理接管成 502）：
 *   env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy \
 *       no_proxy='127.0.0.1,localhost' node scripts/scroll_smoke.js
 *
 * 退出码：0 = 全绿；1 = 有断言失败（**发布前必须为 0**）
 *
 * ⚠️ 本文件在仓库根（无 package.json）下 ⇒ Node 按 CJS 解析；写成 ESM 的 import 会炸。
 */

'use strict'

const fs = require('fs')
const os = require('os')
const http = require('http')
const path = require('path')

const ROOT = path.resolve(__dirname, '..')
const DIST = path.join(ROOT, 'frontend', 'dist')
const PORT = Number(process.env.KX_PORT || 8899)
const BASE = `http://127.0.0.1:${PORT}`

const SESSION = JSON.stringify({
  token: 'smoke-fake-token', username: 'smoke', is_admin: true,
  expire_at: 0, expired: false, member_level: 2,
})

/* ---------------- 依赖定位 ---------------- */

function loadPlaywright() {
  const candidates = [
    'playwright-core',
    path.join(os.homedir(), '.workbuddy/binaries/node/workspace/node_modules/playwright-core'),
  ]
  for (const c of candidates) {
    try { return require(c) } catch (e) { /* 试下一个 */ }
  }
  fail([
    '找不到 playwright-core。请安装到一个可解析的位置，例如：',
    '  mkdir -p ~/.workbuddy/binaries/node/workspace && cd ~/.workbuddy/binaries/node/workspace',
    '  npm install playwright-core',
    '然后带 NODE_PATH 运行：',
    '  NODE_PATH=~/.workbuddy/binaries/node/workspace/node_modules node scripts/scroll_smoke.js',
  ])
}

function findChrome() {
  const cands = []
  if (process.env.KX_CHROME) cands.push(process.env.KX_CHROME)
  const cache = path.join(os.homedir(), 'Library/Caches/ms-playwright')
  const lnx = path.join(os.homedir(), '.cache/ms-playwright')
  for (const base of [cache, lnx]) {
    if (!fs.existsSync(base)) continue
    for (const d of fs.readdirSync(base).filter((x) => x.startsWith('chromium'))) {
      cands.push(path.join(base, d, 'chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'))
      cands.push(path.join(base, d, 'chrome-mac/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'))
      cands.push(path.join(base, d, 'chrome-linux/chrome'))
      cands.push(path.join(base, d, 'chrome-win/chrome.exe'))
    }
  }
  for (const c of cands) if (c && fs.existsSync(c)) return c
  fail([
    '找不到 Chromium 可执行文件。可用 KX_CHROME=/abs/path/to/chrome 显式指定。',
    '（或用 npx playwright install chromium 下载）',
  ])
}

/* ---------------- 断言 ---------------- */

const results = []
let PASS = 0, FAIL = 0
function ok(name, cond, extra) {
  if (cond) { PASS++; results.push(`  [PASS] ${name}`); console.log(`  [PASS] ${name}`) }
  else { FAIL++; results.push(`  [FAIL] ${name}${extra ? ' :: ' + extra : ''}`); console.log(`  [FAIL] ${name}${extra ? ' :: ' + extra : ''}`) }
}
function fail(lines) {
  console.error('\n[FATAL] ' + lines.join('\n        '))
  process.exit(2)
}

/* ---------------- SPA 静态服务（try_files 回退，模拟 nginx） ---------------- */

const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.ico': 'image/x-icon',
  '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.woff': 'font/woff',
  '.webmanifest': 'application/manifest+json', '.map': 'application/json; charset=utf-8',
}
function startServer() {
  return new Promise((resolve, reject) => {
    const srv = http.createServer((req, res) => {
      const urlPath = decodeURIComponent(req.url.split('?')[0])
      let f = path.join(DIST, urlPath)
      const isAsset = urlPath.startsWith('/assets/')
      if (!fs.existsSync(f) || fs.statSync(f).isDirectory()) {
        if (isAsset) { res.writeHead(404); return res.end('not found') }
        f = path.join(DIST, 'index.html')
      }
      res.writeHead(200, { 'Content-Type': MIME[path.extname(f)] || 'application/octet-stream' })
      fs.createReadStream(f).pipe(res)
    })
    srv.on('error', reject)
    srv.listen(PORT, '127.0.0.1', () => resolve(srv))
  })
}

/* ---------------- 探针 ---------------- */

async function scrollProbe(page, kind) {
  const get = `(() => { const se=document.scrollingElement; return { top: Math.round(se.scrollTop), sh: se.scrollHeight, ch: se.clientHeight,
    htmlOb: getComputedStyle(document.documentElement).overscrollBehaviorY, bodyOb: getComputedStyle(document.body).overscrollBehaviorY } })()`
  const before = await page.evaluate(get)
  if (kind === 'wheel') {
    await page.mouse.move(600, 500)
    for (let i = 0; i < 6; i++) { await page.mouse.wheel(0, 200); await page.waitForTimeout(60) }
  } else {
    const cdp = await page.context().newCDPSession(page)
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: 195, y: 600 }] })
    for (let y = 600; y >= 180; y -= 40) {
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: 195, y }] })
      await page.waitForTimeout(25)
    }
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
    await page.waitForTimeout(600)
  }
  const after = await page.evaluate(get)
  return { moved: after.top - before.top, sh: after.sh, ch: after.ch, htmlOb: before.htmlOb, bodyOb: before.bodyOb, top: after.top }
}

/**
 * ★ 按端点给「形状正确」的桩响应，不要用一把通用空对象糊弄所有接口。
 *   教训（v4.11.65 本探针首跑当场抓到）：最初对所有 /api/** 一律返回
 *   `{ok:true,data:[],rows:[],...}`。MemberView 的 load() 里有
 *   `plans.value = await memberPlans()`，拿到这个空壳后 `plans.free` 变成 undefined，
 *   模板 `plans.free.label` 直接抛 `TypeError: Cannot read properties of undefined
 *   (reading 'label')` ⇒ **整个「我的」页白屏**（连 .page-shell 都没渲染出来）。
 *   这个缺陷 SSR 冒烟测试看不见（SSR 不跑 onMounted 里的 load()），
 *   只有"真浏览器 + 真网络桩"才暴露 —— 桩写得不真实，等于把探针的判别力自己废掉。
 *   （同版已把 MemberView 的 plans 改为与默认值合并，作为纵深防御。）
 */
const API_FIXTURES = {
  '/api/member/plans': {
    free: { label: '免费', picker: 3, aipick: 0, auction: 0 },
    member: { label: '付费会员', picker: -1, aipick: 3, auction: -1 },
    vip: { label: 'VIP', picker: -1, aipick: -1, auction: -1 },
    checkin_bonus: 3, invite_reward_days: 5, new_user_days: 5,
  },
  '/api/member/overview': {
    member: {
      username: 'smoke', member_level: 2, member_label: 'VIP', permanent: true,
      expired: false, privileged: true, expire_date: '',
    },
    quota: [
      { feature: 'picker', remain: 5, limit: 5, bonus: 0, privileged: true, label: '选股' },
      { feature: 'aipick', remain: 1, limit: 3, bonus: 0, privileged: false, label: 'AI 预测' },
    ],
    checkin: { done_today: true, reward: 3, streak: 2 },
    invite: { code: 'SMOKE1', invited_count: 0, earned_days: 0, reward_days: 5, invitees: [] },
  },
  '/api/member/checkin': { history: [], done_today: true, reward: 3, streak: 2 },
  '/api/activity/track': { ok: true },
}

/** 其余接口的通用空态（列表类页面走空态而不是报错态） */
const EMPTY = { ok: true, data: [], rows: [], list: [], items: [], total: 0 }

/** 收集页面级 JS 异常 —— 白屏类缺陷（模板抛错）只能这样抓到 */
function collectPageErrors(page) {
  const errs = []
  page.on('pageerror', (e) => errs.push(String(e && e.message ? e.message : e)))
  page.on('console', (m) => { if (m.type() === 'error') errs.push('CONSOLE: ' + m.text().slice(0, 200)) })
  return errs
}

async function newCtx(browser, viewport, mobile) {
  const ctx = await browser.newContext({ viewport, hasTouch: mobile, isMobile: mobile })
  await ctx.addInitScript((s) => { try { localStorage.setItem('kuaixuan_session_v1', s) } catch (e) {} }, SESSION)
  await ctx.route('**/api/**', (r) => {
    const p = new URL(r.request().url()).pathname
    const body = Object.prototype.hasOwnProperty.call(API_FIXTURES, p) ? API_FIXTURES[p] : EMPTY
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  })
  return ctx
}

async function openAt(ctx, routePath) {
  const page = await ctx.newPage()
  page.__errs = collectPageErrors(page)
  await page.goto(BASE + routePath, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1500)
  // ★ 必须注入超高元素：否则「滑不动」可能只是因为页面本来就短（会把 bug 当成没问题）
  await page.evaluate(`(() => { if (!document.getElementById('__pad')) { const d=document.createElement('div'); d.id='__pad'; d.style.height='4000px'; document.body.appendChild(d) } })()`)
  await page.waitForTimeout(200)
  return page
}

const PILLS = `(() => {
  const g = document.querySelector('.group-nav'); if (!g) return { has: false }
  const gr = g.getBoundingClientRect()
  return { has: true, cw: g.clientWidth, sw: g.scrollWidth, ox: getComputedStyle(g).overflowX,
    pills: Array.from(g.querySelectorAll('.group-nav-item')).map(a => { const r = a.getBoundingClientRect()
      return { label: a.textContent.trim(), left: Math.round(r.left), right: Math.round(r.right),
               inRow: r.left >= gr.left - 1 && r.right <= gr.right + 1 } }) }
})()`

async function main() {
  if (!fs.existsSync(path.join(DIST, 'index.html'))) fail(['frontend/dist 不存在或没有 index.html —— 先 cd frontend && npm run build'])
  const { chromium } = loadPlaywright()
  const CHROME = findChrome()
  console.log(`\n=== 前端滚动 / 导航 IA 浏览器冒烟 ===\n  dist  : ${DIST}\n  chrome: ${CHROME}\n`)

  const srv = await startServer()
  const browser = await chromium.launch({ executablePath: CHROME })
  try {
    for (const [viewport, mobile, kind] of [
      [{ width: 390, height: 844 }, true, '手机 390x844 触摸'],
      [{ width: 1280, height: 900 }, false, '桌面 1280x900 滚轮'],
    ]) {
      console.log(`— ${kind}`)
      const ctx = await newCtx(browser, viewport, mobile)

      /* A. 滚动：/ladder 与 /member 两页都要能滚 */
      for (const p of ['/ladder', '/member']) {
        const page = await openAt(ctx, p)
        const r = await scrollProbe(page, mobile ? 'touch' : 'wheel')
        ok(`${kind} ${p} 可上下滚动`, r.moved > 5,
          `scrollTop 只移动 ${r.moved}px（scrollH ${r.sh} / clientH ${r.ch}）`)
        // 根元素保留 none、body 必须回到 auto —— 正是 v4.11.65 的修法，防止再写回 body
        ok(`${kind} ${p} overscroll-behavior 只写根元素（html=none / body=auto）`,
          r.htmlOb === 'none' && r.bodyOb === 'auto', `html=${r.htmlOb} body=${r.bodyOb}`)
        // ★ 白屏类缺陷（模板抛错导致整页不渲染）只有这里抓得到：SSR 不跑 onMounted，
        //   eslint/vite 对"渲染期才炸的取值"完全静默。
        const pageShell = await page.evaluate(`!!document.querySelector('.page-shell')`)
        ok(`${kind} ${p} 页面已真正渲染（.page-shell 在场）`, pageShell)
        ok(`${kind} ${p} 无页面级 JS 异常`, page.__errs.length === 0, page.__errs.join(' | '))
        await page.close()
      }

      /* B. 复盘组二级 pill：不能有任何一个被屏幕裁掉 */
      const page = await openAt(ctx, '/ladder')
      const info = await page.evaluate(PILLS)
      ok(`${kind} 复盘组二级 pill 行已渲染`, info.has)
      if (info.has) {
        ok(`${kind} 复盘组含「异动监管」pill`, info.pills.some((x) => x.label === '异动监管'),
          '实际 ' + JSON.stringify(info.pills.map((x) => x.label)))
        const clipped = info.pills.filter((x) => !x.inRow)
        ok(`${kind} 复盘组二级 pill 全部完整可见（无一被裁）`, clipped.length === 0,
          '被裁: ' + JSON.stringify(clipped.map((x) => `${x.label}(${x.left}~${x.right})`)) +
          ` / pill 行宽 ${info.cw}~${info.sw} overflow-x=${info.ox}`)
      }

      /* C. 「我的」页承接了顶部账户区块，且顶部已不再有用户入口 */
      const mp = await openAt(ctx, '/member')
      const mv = await mp.evaluate(`(() => ({
        acc: !!document.querySelector('.mb-acc-card'),
        badges: document.querySelectorAll('.mb-acc-badges .member-badge').length,
        fonts: document.querySelectorAll('.mb-fontfam').length,
        sizes: document.querySelectorAll('.mb-set-btn').length,
        navUser: !!document.querySelector('.nav-bar .user-name-btn'),
        text: document.querySelector('.mb-acc-card') ? document.querySelector('.mb-acc-card').innerText : '',
      }))()`)
      ok(`${kind} /member 渲染「账户」卡片`, mv.acc)
      ok(`${kind} /member 账户卡片含 个人信息/修改密码/退出登录`,
        ['个人信息', '修改密码', '退出登录'].every((t) => mv.text.includes(t)), JSON.stringify(mv.text))
      ok(`${kind} /member 账户卡片含 3 档字号 + 3 个字体族`, mv.sizes === 3 && mv.fonts === 3,
        `sizes=${mv.sizes} fonts=${mv.fonts}`)
      ok(`${kind} 顶部导航已无用户名按钮（账户区块迁走）`, !mv.navUser)
      await mp.close()

      await ctx.close()
    }
  } finally {
    await browser.close()
    srv.close()
  }

  console.log(`\n=== 结果：PASS=${PASS}  FAIL=${FAIL} ===`)
  if (FAIL) {
    console.log('\n失败项：\n' + results.filter((r) => r.includes('[FAIL]')).join('\n'))
    process.exit(1)
  }
  console.log('滚动 / 导航 IA 冒烟全绿。\n')
  process.exit(0)
}

main().catch((e) => {
  console.error('\n[FATAL] 探针自身异常：', e && e.stack ? e.stack : e)
  process.exit(2)
})
