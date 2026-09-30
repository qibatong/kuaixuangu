#!/usr/bin/env node
/**
 * 防复发闸门（棘轮）：**不再新增响应式断点**
 *
 * 背景（v4.11.84 · P1-3）：
 *   全仓曾有 **12 个不同断点**（390/430/480/560/576/700/768×31/899/900/1099/1100/1280），
 *   其中 `899↔900`、`576↔560`、`480↔430` 是同一意图写了两处的"魔数对" ——
 *   改一处必漏一处，历史上"手机横滑漏网点"就是这么来的（见 main.css 里 768 块的长注释）。
 *
 *   目标收敛为 5 档（见 main.css 顶部约定）：≥1280 / 1100–1279 / 769–1099 / 481–768 / ≤480(含 ≤430)。
 *
 * 本闸门是**棘轮**：老断点允许存在（合并需逐档截图回归，属单独一轮），
 *   但**任何新断点值都会失败**、不同断点总数只许减不许增。
 *   真要合并老断点时：删代码后把 ALLOWED 里的值一并删掉（棘轮下调）。
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, dirname, extname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const SRC = join(ROOT, 'src')

// 2026-09-30 v4.11.84 实测：现存断点（合并一个就删一个）
const ALLOWED = [390, 430, 480, 560, 576, 700, 768, 899, 900, 1099, 1100, 1280]
const MAX_DISTINCT = ALLOWED.length

function walk(dir, out = []) {
  for (const f of readdirSync(dir)) {
    const p = join(dir, f)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (['.vue', '.css'].includes(extname(p))) out.push(p)
  }
  return out
}

const files = walk(SRC)
const found = new Map()          // px → [位置]
for (const p of files) {
  readFileSync(p, 'utf8').split('\n').forEach((l, i) => {
    for (const m of l.matchAll(/@media[^{]*?(\d+)px[^{]*?\{/g)) {
      const v = Number(m[1])
      if (!found.has(v)) found.set(v, [])
      found.get(v).push(`${p.replace(SRC + '/', '')}:${i + 1}`)
    }
  })
}

const vals = [...found.keys()].sort((a, b) => a - b)
const extra = vals.filter((v) => !ALLOWED.includes(v))

console.log('— 响应式断点棘轮闸门 (breakpoint_guard) —')
console.log('  · 扫描 %d 个文件，实测 %d 个断点（基线 %d）', files.length, vals.length, MAX_DISTINCT)
for (const v of vals) console.log('    ' + String(v).padEnd(5) + ' × ' + String(found.get(v).length).padEnd(3) + ' ' +
  found.get(v).slice(0, 3).join(' ') + (found.get(v).length > 3 ? ' …' : ''))

let fail = []
if (extra.length) fail.push('新增断点（不在允许列表）: ' + extra.join(', '))
if (vals.length > MAX_DISTINCT) fail.push(`不同断点数 ${vals.length} > 基线 ${MAX_DISTINCT}`)
const target = [1280, 1100, 1099, 768, 480]
console.log('  · 目标 5 档 = %s（现存 %d 个，待分批合并）', target.join('/'), vals.length)

if (fail.length) {
  console.error('\n✗ breakpoint_guard 失败：\n   ' + fail.join('\n   '))
  console.error('\n   修法：复用 main.css 顶部约定的 5 档；确实需要新档位时，先改约定再同步本文件 ALLOWED。')
  process.exit(1)
}
console.log('\n  ✓ 未新增断点碎片')
