#!/usr/bin/env node
/**
 * 元素级 computed-style 快照 / 对比工具（P4 浅色主题重构的**唯一可靠 oracle**）
 *
 * 为什么需要它（2026-10-05 · v4.11.94 的教训）：
 *   P4「把 body[data-bg="light"] 属性覆盖改成变量」看起来是纯机械替换，于是我先写了
 *   静态判定器：若浅色规则 `body[data-bg="light"] .x { color: #c62828 }` 的**同选择器基准规则**
 *   `.x { color: var(--accent) }` 已用同一 token、且 token 的浅色值 == 该字面值，就认定"可证明冗余"并删除。
 *   结果**错了**，而且错得很隐蔽：浅色块**内部**规则之间会互相竞争 ——
 *       body[data-bg="light"] .nav-item        { color: #3a3f4c }    ← 权重 (0,2,1)
 *       body[data-bg="light"] .nav-item.active { color: #c62828 }    ← 权重 (0,3,1)，被我删了
 *     删掉后者后，`.active` 只能吃到前者 ⇒ **激活态丢了红色高亮**；同一批还删掉了
 *     `.nav-item:hover { color:#1a1d26 }`，悬停文字色也会变灰（快照不 hover 更不易发现）。
 *   ⇒ 结论：**"某条声明是否冗余"不能靠读源码判断，必须问渲染引擎**。
 *
 * 用法（需要 KX_TOKEN，见 scripts 里既有的 mint 套路；两个 tag 分别跑在**改前/改后**的构建上）：
 *   1) 改前： TAG=before    KX_TOKEN=... node scripts/_kx_light_diff.mjs snap
 *   2) 改后： TAG=after     KX_TOKEN=... node scripts/_kx_light_diff.mjs snap
 *   3)        node scripts/_kx_light_diff.mjs diff before after
 *
 * 判读：
 *   · 除 `backgroundImage`（水印层是含用户名/时间的 base64 data URL，每次渲染都不同）与
 *     动态列表的 DOM 顺序外，**任何差异都是回归**；
 *   · ⚠️ 本工具只覆盖"静置态"。hover/active/focus 等交互态样式**必须另外核**
 *     （本次 `.nav-item:hover` 的退化就是快照漏掉的）。
 *   · ⚠️ 页面清单有限，新增受影响组件时把它加进 PAGES。
 */
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

const CHROME = process.env.KX_CHROME ||
  '/Users/batong/Library/Caches/ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'
const BASE = process.env.KX_BASE || 'https://www.kuaixuangu.cn'
const TOKEN = process.env.KX_TOKEN || ''
const PORT = 9521
const ROOT = process.env.KX_OUT || '/tmp/kx_light_diff'

// 受影响组件出现频率较高的页面（新增受影响组件时补进来）
const PAGES = ['/', '/market', '/auction', '/ladder', '/member', '/news']
const PROPS = ['color', 'backgroundColor', 'backgroundImage', 'borderTopColor', 'borderRightColor',
  'borderBottomColor', 'borderLeftColor', 'boxShadow', 'opacity', 'fill', 'stroke']

const DUMP = `(() => {
  const P = ${JSON.stringify(PROPS)}
  const out = []
  for (const el of document.querySelectorAll('*')) {
    const s = getComputedStyle(el)
    const key = el.tagName.toLowerCase() + '|' + String(el.className || '').slice(0, 60) + '|' + (el.id || '') +
                '|' + (el.parentElement ? Array.from(el.parentElement.children).indexOf(el) : -1)
    out.push([key, ...P.map(p => s[p])])
  }
  return JSON.stringify({ n: out.length, rows: out })
})()`

class CDP {
  constructor(u) {
    this.ws = new WebSocket(u); this.id = 0; this.p = new Map()
    this.ws.addEventListener('message', (e) => {
      const m = JSON.parse(e.data)
      if (m.id && this.p.has(m.id)) {
        const { res, rej } = this.p.get(m.id); this.p.delete(m.id)
        m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result)
      }
    })
  }
  ready() { return new Promise((r) => this.ws.addEventListener('open', r)) }
  send(m, p = {}, sid) { const id = ++this.id; return new Promise((res, rej) => { this.p.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method: m, params: p, sessionId: sid })) }) }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function snap(tag) {
  if (!TOKEN) throw new Error('需要 KX_TOKEN')
  const out = path.join(ROOT, tag)
  fs.mkdirSync(out, { recursive: true })
  const child = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`, '--no-first-run',
    `--user-data-dir=${out}/prof`, '--disable-gpu', '--hide-scrollbars', 'about:blank'], { stdio: 'ignore' })
  let v
  for (let i = 0; i < 60 && !v; i++) { try { const r = await fetch(`http://127.0.0.1:${PORT}/json/version`); if (r.ok) v = await r.json() } catch (_) {} if (!v) await sleep(250) }
  const c = new CDP(v.webSocketDebuggerUrl); await c.ready()
  const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' })
  const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true })
  const S = (m, p) => c.send(m, p, sessionId)
  await S('Page.enable'); await S('Runtime.enable')
  const ev = async (e) => (await S('Runtime.evaluate', { expression: e, returnByValue: true })).result.value
  await S('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false, screenWidth: 1440, screenHeight: 900 })
  await S('Page.navigate', { url: BASE + '/login' }); await sleep(3000)
  await ev(`localStorage.setItem('kuaixuan_session_v1',JSON.stringify({token:'${TOKEN}',username:'me',is_admin:0,expire_at:4102444800,expired:0,member_level:2}))`)
  for (const bg of ['light', 'dark']) {
    const list = bg === 'light' ? PAGES : ['/']
    for (const p of list) {
      await S('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: bg }] })
      await S('Page.navigate', { url: BASE + '/login' }); await sleep(1500)
      await ev(`localStorage.setItem('kuaixuan_bg',JSON.stringify({bg:'${bg}'}))`)
      await S('Page.navigate', { url: BASE + p }); await sleep(p === '/' ? 12000 : 9000)
      fs.writeFileSync(path.join(out, `${bg}${p.replace(/\//g, '_')}.json`), await ev(DUMP))
      console.log(`${tag}  ${bg.padEnd(5)} ${p.padEnd(10)} 已存`)
    }
  }
  c.ws.close(); child.kill('SIGKILL')
  console.log(`快照目录 ${out}`)
}

function diff(a, b) {
  const A = path.join(ROOT, a), B = path.join(ROOT, b)
  let tot = 0, bad = 0
  for (const f of fs.readdirSync(A).filter((x) => x.endsWith('.json'))) {
    const ra = JSON.parse(fs.readFileSync(path.join(A, f), 'utf8')).rows
    const rb = JSON.parse(fs.readFileSync(path.join(B, f), 'utf8')).rows
    if (ra.length !== rb.length) { console.log(`${f}  ⚠️ 元素数 ${ra.length} vs ${rb.length}`); bad++; continue }
    const d = []
    for (let i = 0; i < ra.length; i++) {
      tot++
      if (ra[i][0] !== rb[i][0]) { d.push(['DOM顺序', i, ra[i][0].slice(0, 40)]); continue }
      for (let k = 1; k < ra[i].length; k++) if (ra[i][k] !== rb[i][k]) d.push([PROPS[k - 1], i, ra[i][0].slice(0, 40), ra[i][k], rb[i][k]])
    }
    // 噪声桶：水印层是含用户名/时间的 base64 data URL（每次渲染都不同）；
    //           DOM 顺序会随实时数据排序而变（CSS-only 改动不可能改变 DOM 顺序）。
    // 这两类不计入失败，但会打印出来供人扫一眼。
    const NOISE = new Set(['backgroundImage', 'DOM顺序'])
    const real = d.filter((x) => !NOISE.has(x[0]))
    const noise = d.filter((x) => NOISE.has(x[0]))
    bad += real.length
    console.log(`${f.padEnd(22)} ${real.length ? '差异 ' + real.length + ' 处' : '✅ 零差异'}`
      + (noise.length ? `（另有噪声 ${noise.length}：${[...new Set(noise.map((x) => x[0]))].join('/')}）` : ''))
    real.slice(0, 12).forEach((x) => console.log(`    ${x[0]} #${x[1]} ${x[2]}  ${x[3] || ''} → ${x[4] || ''}`))
  }
  console.log(`\n比对 ${tot} 个元素；除水印外差异 ${bad} 处${bad ? '  ❌ 有回归' : '  ✅'}`)
  process.exit(bad ? 1 : 0)
}

const cmd = process.argv[2]
if (cmd === 'snap') await snap(process.env.TAG || 'before')
else if (cmd === 'diff') diff(process.argv[3] || 'before', process.argv[4] || 'after')
else console.log('用法: node _kx_light_diff.mjs snap | diff <beforeTag> <afterTag>')
