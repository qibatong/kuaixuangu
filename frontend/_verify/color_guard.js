#!/usr/bin/env node
/**
 * 防复发闸门（棘轮）：**语义色裸值只许减不许增**
 *
 * 背景（v4.11.84 · P1-4）：
 *   全仓 `main.css` 有 400+ 处硬编码颜色，且**同类语义存在"两套红"**
 *   （全局 `.up{#ff8a6f}` 橙粉 vs 竞价表 `.real-chg-col.up{#ff5a5a}` 红）——
 *   改一次配色要在全库搜，改漏一处就出现"同屏两种涨"。
 *   本轮把语义色收口为 `:root` 的 token（`--up/--down/--up-strong/--warn-amber/--star/--dim-soft`），
 *   但**没有**一次性替换全部 400+ 处（那会是一次无法评审的大改动，且主人已习惯现有配色）。
 *
 * 因此本闸门采用**棘轮**策略：
 *   · 基线 = 本轮结束时的裸值出现次数（下表）；
 *   · 任何一次改动只允许**减少**或持平，**增加即失败**；
 *   · `styles/main.css` 的 `:root{...}` 块（token 定义处）不计入。
 *
 * 正确做法：新代码写 `var(--up)` 而不是 `#ff8a6f`；确实需要新色时先加 token 再用。
 * （值要真的换掉时，请把新数字写回本文件基线，并在提交信息里写明"棘轮下调"。）
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, dirname, extname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const SRC = join(ROOT, 'src')

// 2026-09-30 v4.11.84 实测基线（只许减）
const BASELINE = {
  '#ff8a6f': 3,    // 全局涨色（.up 已 token 化，剩其它零散处）
  '#00c864': 16,   // 跌色
  '#ff5a5a': 2,    // 竞价表强涨红（real-chg-col 已 token 化）
  '#d9822b': 4,    // 警告琥珀（v4.11.84 棘轮下调 5→4）
  '#ffb400': 78,   // 星级/重点（大头未收敛；v4.11.84 棘轮下调 79→78）
  '#b06b6b': 1,    // dim-25 旧值（已换 --dim-soft，剩 1 处注释/其它）
}
// token 必须存在且被引用（防止"删了 token 只留裸值"也能过）
const TOKENS = ['--up', '--down', '--up-strong', '--warn-amber', '--star', '--dim-soft']

function walk(dir, out = []) {
  for (const f of readdirSync(dir)) {
    const p = join(dir, f)
    const st = statSync(p)
    if (st.isDirectory()) walk(p, out)
    else if (['.vue', '.css', '.js'].includes(extname(p))) out.push(p)
  }
  return out
}

const files = walk(SRC)
let hits = Object.fromEntries(Object.keys(BASELINE).map((k) => [k, 0]))
let cssTokenBlock = ''
for (const p of files) {
  let t = readFileSync(p, 'utf8')
  if (p.endsWith('styles/main.css')) {
    cssTokenBlock = (t.match(/:root\s*\{[^}]*\}/) || [''])[0]
    t = t.replace(/:root\s*\{[^}]*\}/, '')          // token 定义处不计
  }
  for (const k of Object.keys(BASELINE)) hits[k] += t.toLowerCase().split(k).length - 1
}

console.log('— 语义色裸值棘轮闸门 (color_guard) —')
console.log('  · 扫描 %d 个文件（%s 的 :root 块已排除）', files.length, 'main.css')
let fail = []
for (const [k, base] of Object.entries(BASELINE)) {
  const n = hits[k]
  const mark = n > base ? '✗ 增加' : n < base ? '✓ 减少' : '· 持平'
  console.log('    ' + k.padEnd(9) + ' 实测 ' + String(n).padEnd(4) + ' 基线 ' + String(base).padEnd(4) + ' ' + mark)
  if (n > base) fail.push(`${k}: ${base} → ${n}`)
}
// token 存在性 + 至少被引用一次
for (const tok of TOKENS) {
  const used = files.some((p) => readFileSync(p, 'utf8').includes(`var(${tok}`))
  const defined = cssTokenBlock.includes(tok + ':')
  if (!defined) fail.push(`token ${tok} 未在 :root 定义`)
  if (!used) fail.push(`token ${tok} 定义了但无人使用`)
}
console.log('  · token %d 个：定义 + 引用均校验', TOKENS.length)

if (fail.length) {
  console.error('\n✗ color_guard 失败：\n   ' + fail.join('\n   '))
  console.error('\n   修法：用 var(--up) 等 token，别写裸色值；确需新色先加 token 再引用。')
  process.exit(1)
}
console.log('\n  ✓ 无新增裸值，token 齐备')
