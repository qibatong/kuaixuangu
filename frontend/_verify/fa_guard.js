#!/usr/bin/env node
/**
 * 防复发闸门：**不许再出现"永不渲染"的图标类**
 *
 * 背景（v4.11.84 · P1-5）：
 *   项目用 Font Awesome **4.7.0**，但代码里混进了 **FA5 才有的类名**：
 *     · `fa-chart-line`（AipickReport）—— FA4 里叫 `fa-line-chart`
 *     · `fa-crown`（VipGate 会员皇冠 ×3）—— FA4 **完全没有**这个图标
 *   表现是**静默的**：元素渲染但不显示任何字形（空白），没人报错，一直没人发现
 *   （`fa-robot` 曾在 2026-09-05 被单独发现并改成 `fa-android`，说明这类问题会反复出现）。
 *   ⇒ 需要一个"图标类是否真的有字形"的闸门。
 *
 * 判据：src 里出现的每个 `fa-*` 类，必须
 *   ① 在本地子集 CSS 里有 `.fa-x:before{content:...}` 规则，或
 *   ② 属于 FA 的非图标工具类（尺寸/旋转/动画/列表…，白名单）。
 * 新图标应重跑 `scripts/_kx_gen_fa_subset.py` 生成子集后再提交。
 */
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs'
import { join, dirname, extname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const SRC = join(ROOT, 'src')
const SUBSET = join(SRC, 'styles/fontawesome-subset.css')

// FA4 的非图标工具类（不产生字形，无需 :before 规则）
const UTIL = new Set([
  'fa-spin', 'fa-pulse', 'fa-fw', 'fa-ul', 'fa-li', 'fa-border', 'fa-inverse',
  'fa-stack', 'fa-stack-1x', 'fa-stack-2x', 'fa-rotate-90', 'fa-rotate-180', 'fa-rotate-270',
  'fa-flip-horizontal', 'fa-flip-vertical', 'fa-lg',
  'fa-2x', 'fa-3x', 'fa-4x', 'fa-5x',
])
const SIZE_UTIL = /^fa-\dx$/

function walk(dir, out = []) {
  for (const f of readdirSync(dir)) {
    const p = join(dir, f)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (['.vue', '.js', '.css', '.html'].includes(extname(p))) out.push(p)
  }
  return out
}

const subset = existsSync(SUBSET) ? readFileSync(SUBSET, 'utf8') : ''
const known = new Set([...subset.matchAll(/\.(fa-[a-z0-9-]+):before\{/g)].map((m) => m[1]))

// 🔴 先剥注释再扫 —— 否则注释里写的"旧类名/反面例子"会被当成真实用法
//   （与 spot.spec.js 的 textOnly 同一教训：判据要落在**真实代码**上）
const strip = (t) =>
  t.replace(/<!--[\s\S]*?-->/g, '')          // html/模板注释
    .replace(/\/\*[\s\S]*?\*\//g, '')        // 块注释
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1')    // 行注释（避开 https://）

const used = new Map()
for (const p of walk(SRC)) {
  if (p === SUBSET) continue
  for (const m of strip(readFileSync(p, 'utf8')).matchAll(/\bfa-[a-z0-9-]+/g)) {
    const c = m[0]
    if (c === 'fa' || c.startsWith('fa fa')) continue
    if (!used.has(c)) used.set(c, p.replace(SRC + '/', ''))
  }
}

const missing = [...used.keys()].filter((c) => !known.has(c) && !UTIL.has(c) && !SIZE_UTIL.test(c))

console.log('— 图标字形闸门 (fa_guard) —')
console.log('  · 本地子集图标 %d 个；src 引用 %d 个类', known.size, used.size)
if (missing.length) {
  console.error('\n✗ fa_guard 失败：下列类**没有字形**（FA4.7 里不存在或未生成进子集）：')
  for (const c of missing) console.error('   %s   ← %s', c.padEnd(24), used.get(c))
  console.error('\n   修法：① 换成 FA4.7 里存在的名字（如 fa-chart-line → fa-line-chart）；')
  console.error('         ② 新图标先加进源码再重跑 scripts/_kx_gen_fa_subset.py。')
  process.exit(1)
}
console.log('  ✓ 全部图标都有字形（无"静默空白"）')
