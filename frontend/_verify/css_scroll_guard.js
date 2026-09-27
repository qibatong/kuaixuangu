#!/usr/bin/env node
/**
 * 防复发闸门：**禁止把 overscroll-behavior 写在 body 上**
 *
 * 背景（v4.11.65 事故，P0）：
 *   v4.11.63 为「禁整页下拉回弹」写了 `html, body { overscroll-behavior: none }`，
 *   结果**全站每一页都无法上下滚动**（桌面滚轮 + 手机触摸都不行）。
 *   机制：本应用在 ≤768px 对 html/body/#app/.page-shell/.container 全局强制
 *   `overflow-x: hidden !important` ⇒ 按 CSS Overflow 规范，元素只要一个轴不是 visible，
 *   另一轴的计算值就从 visible 变成 auto ⇒ **body 也成了滚动容器**。overscroll-behavior
 *   写在 body 上，浏览器沿滚动链走到 body 就判定「不得向父级上链」，而 body 自身
 *   height:auto 没有可滚距离 ⇒ 手势被吃掉，根滚动容器（html/视口）永远收不到滚动事件。
 *   Chromium 消融实测：只把 body 改回 auto 即恢复；只改 html 无效 ⇒ 元凶唯一是 body 这条。
 *   ⇒ overscroll-behavior 只能写在**真正承担滚动的元素**上（本应用 = html / :root），
 *     绝不可写在 body 上。
 *
 * 本脚本查两处（源码 + 构建产物，防止"改了源码但 dist 没重建"蒙混过关）：
 *   ① frontend/src 下所有 .css 与 .vue 的 <style> 块；
 *   ② frontend/dist/assets/*.css（存在才查）。
 *   任一处出现「选择器把 body 当类型选择器 且 块内含 overscroll-behavior 声明」即 FAIL。
 *
 * 用法: node _verify/css_scroll_guard.js    （已接入 npm run verify）
 * 退出码: 0 = 全绿；1 = 发现违规
 *
 * ⚠️ 本仓库 package.json 声明了 `"type": "module"` ⇒ 本文件是 **ESM**，
 *    必须用 import（写成 require 会炸 "require is not defined in ES module scope"，
 *    首版就是这么挂的）。同目录的 nav.spec.js 同样是 ESM。
 */

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const FRONTEND = path.resolve(__dirname, '..')
const SRC = path.join(FRONTEND, 'src')
const DIST_ASSETS = path.join(FRONTEND, 'dist', 'assets')

/** 去掉 CSS 注释（逐字符替换为空格并保留换行，保证行号不漂）。 */
function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
}

/**
 * 极简 CSS 块扫描器：按花括号配对取出每个「非 at-rule 块」的 (选择器串, 块体, 起始行号)。
 * 正确支持 @media / @supports / @keyframes 等 at-rule 嵌套：
 *   —— 只有栈顶是「空 / at-rule 帧」时才累积前导串（即我们确实在写选择器）；
 *   —— 一旦进入声明块，就忽略后续字符，块体用 slice 取。
 */
function scanBlocks(css, file) {
  const text = stripComments(css)
  const out = []
  const stack = []
  let prelude = ''
  let preludeLine = 1
  let line = 1
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (ch === '\n') line++
    if (ch === '{') {
      stack.push({ sel: prelude.trim(), isAt: prelude.trim().startsWith('@'), bodyStart: i, line: preludeLine })
      prelude = ''
    } else if (ch === '}') {
      const f = stack.pop()
      if (f && !f.isAt && f.sel) out.push({ selector: f.sel, body: text.slice(f.bodyStart + 1, i), file, line: f.line })
      prelude = ''
    } else {
      const top = stack[stack.length - 1]
      if (!top || top.isAt) {
        // ⚠️ 判"前导串还没开始"必须用 trim()：块结束后紧接着的是换行/缩进，
        //    若按 `!prelude` 判，则换行已经让 prelude 非空 ⇒ 起始行号永远停在上一行
        //    （首版就是这样把 @media 里的 body 报成第 1 行的）。
        if (!prelude.trim() && ch.trim()) preludeLine = line
        prelude += ch
      }
    }
  }
  return out
}

/**
 * 选择器串里是否把 body 当**类型选择器**用（而不是 .somebody / #mybody 这类子串）。
 * 命中：body、body[data-bg]、html, body、body.dark、> body、body:not(.x)
 * 不命中：.somebody、#mybody、[data-body]、.x-body
 */
function hasBodyTypeSelector(selectorList) {
  return /(?:^|[\s,>+~(])body(?![\w-])/.test(selectorList)
}

/** 块内是否有 overscroll-behavior 声明（含 -x/-y 与 !important）。 */
function hasOverscrollDecl(body) {
  return /(?:^|[;{\s])overscroll-behavior(?:-[xy])?\s*:/i.test(body)
}

/** 收集 src 下所有 .css / .vue（递归，跳过 node_modules）。 */
function collectStyleSources() {
  const files = []
  const walk = (dir) => {
    for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, ent.name)
      if (ent.isDirectory()) {
        if (ent.name !== 'node_modules') walk(p)
      } else if (ent.name.endsWith('.css') || ent.name.endsWith('.vue')) {
        files.push(p)
      }
    }
  }
  walk(SRC)
  return files.sort()
}

/** 取一个源文件里的若干段 CSS（.vue 逐个 <style> 块，并记录行偏移）。 */
function sourcesFor(file) {
  const raw = fs.readFileSync(file, 'utf8')
  if (file.endsWith('.css')) return [{ css: raw, offset: 0 }]
  const out = []
  const re = /<style[^>]*>([\s\S]*?)<\/style>/g
  let m
  while ((m = re.exec(raw)) !== null) {
    // <style> 标签所在行（content 从该行开始）
    out.push({ css: m[1], offset: raw.slice(0, m.index).split('\n').length - 1 })
  }
  return out
}

function main() {
  const violations = []
  const lines = []
  const scanOne = (tag, file, css, offset) => {
    const blocks = scanBlocks(css, file)
    for (const b of blocks) {
      if (hasBodyTypeSelector(b.selector) && hasOverscrollDecl(b.body)) {
        violations.push(`${file}:${b.line + offset}  [${tag}]  选择器 \`${b.selector.replace(/\s+/g, ' ')}\` 上写了 overscroll-behavior`)
      }
    }
    return blocks.length
  }

  let nSrc = 0
  let nFiles = 0
  for (const file of collectStyleSources()) {
    nFiles++
    for (const s of sourcesFor(file)) nSrc += scanOne('src', file, s.css, s.offset)
  }
  lines.push(`源码 frontend/src：${nFiles} 个文件 / ${nSrc} 个 CSS 块`)

  if (fs.existsSync(DIST_ASSETS)) {
    const cssFiles = fs.readdirSync(DIST_ASSETS).filter((f) => f.endsWith('.css')).sort()
    let nDist = 0
    for (const f of cssFiles) {
      const p = path.join(DIST_ASSETS, f)
      nDist += scanOne('dist', p, fs.readFileSync(p, 'utf8'), 0)
    }
    lines.push(
      cssFiles.length
        ? `构建产物 dist/assets：${cssFiles.length} 个 css / ${nDist} 个 CSS 块`
        : '构建产物 dist/assets：⚠️ 一个 css 都没有（确认是否已构建）'
    )
  } else {
    lines.push('构建产物 dist/assets：不存在（跳过；发布前务必先 npm run build）')
  }

  console.log('— 滚动安全闸门 (css_scroll_guard) —')
  for (const l of lines) console.log('  · ' + l)

  if (violations.length) {
    console.error('\n[FAIL] 发现把 overscroll-behavior 写在 body 上的规则：')
    for (const v of violations) console.error('  ✗ ' + v)
    console.error('\n为什么必须拦：本应用全局强制 body{overflow-x:hidden} ⇒ body 的 overflow-y')
    console.error('计算值变成 auto ⇒ body 成为滚动容器；在 body 上写 overscroll-behavior 会掐断')
    console.error('整条滚动链，导致**全站滑不动**（2026-09-27 v4.11.65 已真实发生过一次）。')
    console.error('正确写法：只写根元素 —— `html { overscroll-behavior: none }`。')
    process.exit(1)
  }
  console.log('  ✓ 未发现「body + overscroll-behavior」组合\n')
}

main()
