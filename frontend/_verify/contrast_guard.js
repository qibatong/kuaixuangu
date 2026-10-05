#!/usr/bin/env node
/**
 * 语义色对比度闸门（2026-10-05 新增，接入 `npm run verify`）
 *
 * 为什么需要：本轮的审查里，「浅色主题 435 处对比度不达标」「主按钮 1.90:1」这类问题
 *   都是**真机逐个元素实算**才发现的 —— 但它没有任何防回归手段：改一个 token 值就可能
 *   重新跌破 AA，而肉眼在小屏/低亮度下看不出来。color_guard 只管"裸值不许新增"，
 *   管不了"值本身是否够亮/够暗"。这里补上这一层。
 *
 * 做法：解析 `src/styles/main.css` 里的两套主题变量块（:root 与 body[data-bg="light"]），
 *   按 WCAG 2.x 相对亮度公式算出「前景 token × 背景」的对比度，逐对断言 ≥ 阈值。
 *   · 背景取**最不利**的那个：深色主题用页面渐变最深色 #0a0c12（不是最亮的 #0f1219），
 *     否则会把"勉强达标"误判成"达标"；
 *   · 支持 var() 链式解析（token 引用 token）；
 *   · 系数按"正文 4.5 / 大字 3.0 / 极弱角标 3.0"分档，每对显式写出理由。
 *
 * 跑法：node _verify/contrast_guard.js        （失败退出码 1）
 * 加阈值前先想清楚：**不要为了让闸门变绿去调低阈值**，要么改 token 值，要么给出这条
 *   为什么可以低于 4.5 的书面理由并写进 EXCEPTIONS。
 */
// 本项目 package.json 是 "type": "module" ⇒ 必须用 ESM（与 color_guard / fa_guard 保持一致）
import fs from 'node:fs'
import { fileURLToPath } from 'node:url'

const CSS = fileURLToPath(new URL('../src/styles/main.css', import.meta.url))
const src = fs.readFileSync(CSS, 'utf8')

/* ---------------- 1. 抽变量块 ---------------- */
function blockAfter(startRe) {
  const m = src.match(startRe)
  if (!m) return ''
  const from = m.index + m[0].length
  const end = src.indexOf('}', from)
  return src.slice(from, end)
}
const darkVars = blockAfter(/:root\s*\{/)
const lightVars = blockAfter(/body\[data-bg=["']light["']\]\s*\{/)

function parseVars(text) {
  const out = {}
  for (const m of text.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) out[m[1]] = m[2].trim()
  return out
}
const V = { dark: parseVars(darkVars), light: parseVars(lightVars) }

/* ---------------- 2. 颜色解析（含 var() 链） ---------------- */
function hex(c) {
  c = c.replace('#', '')
  if (c.length === 3) c = c.split('').map((x) => x + x).join('')
  return [parseInt(c.slice(0, 2), 16), parseInt(c.slice(2, 4), 16), parseInt(c.slice(4, 6), 16)]
}
function resolve(name, theme, depth = 0) {
  if (depth > 8) return null
  const raw = V[theme][name] != null ? V[theme][name] : V.dark[name]
  if (raw == null) return null
  const v = String(raw).trim()
  const varRef = v.match(/^var\((--[\w-]+)/)
  if (varRef) return resolve(varRef[1], theme, depth + 1)
  const rgba = v.match(/rgba?\(([^)]+)\)/i)
  if (rgba) {
    const p = rgba[1].split(',').map((s) => parseFloat(s))
    // 半透明色按"叠在主题页面底色上"估算（面板多为半透明底，这是最接近实际观感的算法）
    const bg = theme === 'light' ? [255, 255, 255] : hex('#0a0c12')
    const a = p.length > 3 ? p[3] : 1
    return p.slice(0, 3).map((c, i) => c * a + bg[i] * (1 - a))
  }
  if (/^#[0-9a-f]{3,8}$/i.test(v)) return hex(v.slice(0, 7))
  return null
}
const lum = ([r, g, b]) => {
  const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4) }
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
}
const ratio = (fg, bg) => {
  const a = lum(fg), b = lum(bg)
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
}

/* ---------------- 3. 背景基准 ---------------- */
// ⚠️ 深色取**最深**的渐变端点：对比度算的是最坏情况，取亮端会自欺欺人。
const BG = {
  dark: { name: '页面最深底 #0a0c12', rgb: hex('#0a0c12') },
  light: { name: '纯白底 #ffffff', rgb: [255, 255, 255] },
}

/* ---------------- 4. 断言表 ---------------- */
const CHECKS = [
  // [token, 阈值, 理由]
  ['--text-main', 7.0, '正文主色（AAA 目标）'],
  ['--text-secondary', 4.5, '次要正文'],
  ['--text-muted', 4.5, '弱化正文/说明'],
  ['--text-dim', 4.5, '更弱文字（时间戳、角标说明）'],
  ['--text-faint', 3.0, '极弱文字：仅用于角标/时间戳（WCAG 对非正文的下限 3:1）'],
  ['--up', 4.5, '涨（数字）'],
  ['--down', 4.5, '跌（数字）'],
  ['--up-strong', 4.5, '强涨（竞价表红）'],
  ['--warn-amber', 4.5, '警告琥珀'],
  ['--star', 4.5, '星级/重点'],
  ['--dim-soft', 4.5, '弱化文字（叠水印场景）'],
  ['--chart-bar', 3.0, '图表柱/折线（非文字信息）'],
]
// 按钮：文字必须压在**自身底色**上判断
const BUTTON_CHECKS = [
  // 🔴 关键区分：半透明红底 vs **实心**红底用的前景色不是一个 token
  //   （原先「浅粉字 --accent-text 压实心 --accent」实测只有 1.90:1，就是 S3 的那个坑）
  ['--accent-text', '--accent-bg2', 4.5, '次要/描边按钮：文字压半透明红底'],
  ['--on-accent', '--accent-solid', 4.5, '实心填充（选中态 tab / 徽标 / 注册钮）：白字压 accent-solid'],
  ['--on-accent', '--accent-deep2', 4.5, '实心主按钮：白字压最强红（S3 主战场）'],
  ['--warn-text', '--warn-bg', 4.5, '警告标签'],
  ['--gold-text', '--warn-bg', 4.5, '金色标签'],
]

/* ---------------- 5. 危险搭配静态扫描 ---------------- */
// token 级别达标 ≠ 用对了。这里扫源码：同一条 CSS 声明里若同时出现
//   color: var(--accent-text)  与  background: var(--accent | --accent-deep | --accent-deep2)
// 就是"浅粉字压实心红" ⇒ 直接判失败（这正是 S3 修复前 .apply-btn 的写法）。
function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = dir + '/' + e.name
    if (e.isDirectory()) walk(p, out)
    else if (/\.(vue|css)$/.test(e.name)) out.push(p)
  }
  return out
}
const dangerous = []
for (const f of walk(fileURLToPath(new URL('../src', import.meta.url)))) {
  const text = fs.readFileSync(f, 'utf8')
  for (const m of text.matchAll(/\{([^{}]*)\}/g)) {
    const body = m[1]
    const hasFg = /color\s*:\s*var\(--accent-text\)/.test(body)
    const hasSolidBg = /background(?:-color)?\s*:\s*var\(--accent(?:-deep|-deep2)?\)/.test(body)
    if (hasFg && hasSolidBg) {
      const line = text.slice(0, m.start()).split('\n').length
      const sel = text.slice(text.lastIndexOf('\n', m.start() - 1) + 1, m.start()).trim().slice(0, 60)
      dangerous.push(`${f.replace(/.*\/src\//, 'src/')}:${line}  ${sel}`)
    }
  }
}

let fail = 0
console.log('— 语义色对比度闸门 (contrast_guard) —')
for (const theme of ['dark', 'light']) {
  console.log(`  ▸ ${theme === 'dark' ? '深色' : '浅色'}主题（背景基准：${BG[theme].name}）`)
  const bg = BG[theme].rgb
  for (const [tok, need, why] of CHECKS) {
    const c = resolve(tok, theme)
    if (!c) { console.log(`    ? ${tok.padEnd(18)} 未定义 —— 跳过`); continue }
    const r = ratio(c, bg)
    const ok = r >= need
    if (!ok) fail++
    console.log(`    ${ok ? '✓' : '✗'} ${tok.padEnd(18)} ${r.toFixed(2)}:1  (需 ≥${need})  ${why}`)
  }
  for (const [fgT, bgT, need, why] of BUTTON_CHECKS) {
    const fg = resolve(fgT, theme), b = resolve(bgT, theme)
    if (!fg || !b) { console.log(`    ? ${fgT} on ${bgT} 未定义 —— 跳过`); continue }
    const r = ratio(fg, b)
    const ok = r >= need
    if (!ok) fail++
    console.log(`    ${ok ? '✓' : '✗'} ${(fgT + ' on ' + bgT).padEnd(18)} ${r.toFixed(2)}:1  (需 ≥${need})  ${why}`)
  }
}
console.log('\n  ▸ 危险搭配扫描（实心红底 + --accent-text）')
if (dangerous.length) {
  for (const d of dangerous) console.log('    ✗ ' + d)
  fail += dangerous.length
} else {
  console.log('    ✓ 未发现"浅粉字压实心红底"的写法（实心底一律用 --on-accent）')
}

if (fail) {
  console.log(`\n✗ contrast_guard 失败：${fail} 项低于阈值`)
  console.log('  修法：改 main.css 里对应主题的 token 值（深色要更亮、浅色要更深），')
  console.log('        或在 EXCEPTIONS/CHECKS 里给出书面理由并调低该项阈值。')
  process.exit(1)
}
console.log('\n  ✓ 两套主题的语义色对比度全部达标')
