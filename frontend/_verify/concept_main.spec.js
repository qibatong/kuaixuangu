/**
 * 「主要概念」挑选单测 —— 2026-09-29
 * =====================================================================
 * 背景（主人原话）：「一进二显示的概念有点多，只显示主要的就可以」。
 * 一进二原来的概念列直接渲染东财 `f103`（一整串，如「面板、MiniLED、华为概念、OLED…」），
 * 表格态被 CSS 限宽截成省略号、卡片态折好几行。
 *
 * 被测：`src/utils/conceptMain.js`（纯函数）
 * 口径：N=2（对齐全仓既有「主要概念」：concept_refresh.TRUNCATE_N=2 /
 *       kpl.apply_board_concept_db(truncate=2) / 金睛火眼 conceptText=slice(0,2)），
 *       「哪 2 个」按**本名单内的概念共鸣频次**排序（不是东财原顺序，后者无语义）。
 *
 * 断言重点：
 *   ① 只取 2 个，且按共鸣频次选（今日主线）；频次并列时保持原顺序；
 *   ② **以整条概念为单位** —— 绝不出现「Mini」这种把 MiniLED 拦腰切断的结果；
 *   ③ 边界：''/'-'/null/单概念/恰好 2 个/混用分隔符；
 *   ④ 频次统计口径与 splitConcepts 一致（混用分隔符也能统计进去）。
 *
 * 跑法：vite build --ssr _verify/concept_main.spec.js --outDir .cmspec --emptyOutDir \
 *       && node .cmspec/concept_main.spec.js
 *   ⚠️ 不能输出到 /tmp（Node 向上找不到 node_modules）
 *   ⚠️ 不能写顶层 await（esbuild 报 "Top-level await is not available"）
 */
import { splitConcepts, buildConceptFreq, pickMainConcept } from '../src/utils/conceptMain.js'

let fail = 0
function eq(got, want, label) {
  const ok = JSON.stringify(got) === JSON.stringify(want)
  console.log((ok ? '  PASS ' : '  FAIL ') + label + (ok ? '' : '  got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want)))
  if (!ok) fail++
}
function ok(cond, label) {
  console.log((cond ? '  PASS ' : '  FAIL ') + label)
  if (!cond) fail++
}

console.log('=== 1) splitConcepts 拆分口径 ===')
eq(splitConcepts('面板、MiniLED、华为概念'), ['面板', 'MiniLED', '华为概念'], '顿号拆分')
eq(splitConcepts('面板,MiniLED，华为概念;OLED'), ['面板', 'MiniLED', '华为概念', 'OLED'], '混用分隔符(半角逗号/全角逗号/分号)')
eq(splitConcepts('  面板 、  MiniLED '), ['面板', 'MiniLED'], '首尾空白会被去掉')
eq(splitConcepts(''), [], '空串 → []')
eq(splitConcepts('   '), [], '全空白 → []')
eq(splitConcepts('-'), [], '「-」视为无概念 → []')
eq(splitConcepts(null), [], 'null → []')
eq(splitConcepts(undefined), [], 'undefined → []')

console.log('\n=== 2) 按"本名单共鸣频次"选主要概念 ===')
const rows = [
  { concept: '面板、MiniLED、华为概念、OLED' },   // 待测这只
  { concept: '华为概念、CPO' },
  { concept: '华为概念、算力' },
  { concept: '面板' },
]
const freq = buildConceptFreq(rows)
eq(freq.get('华为概念'), 3, '频次统计: 华为概念=3')
eq(freq.get('面板'), 2, '频次统计: 面板=2')
eq(freq.get('MiniLED'), 1, '频次统计: MiniLED=1')
eq(pickMainConcept('面板、MiniLED、华为概念、OLED', freq), '华为概念、面板',
  '取 2 个且按频次排序(华为概念3 > 面板2 > 其余)')

console.log('\n=== 3) 整条概念为单位, 绝不切断英文概念 ===')
const got1 = pickMainConcept('MiniLED、CPO、HBM、PCB', new Map())
eq(got1, 'MiniLED、CPO', '无频次时退化为原顺序前 2')
ok(!/(^|、)Mini($|、)/.test(got1), '不会出现被切半的「Mini」')
ok(got1.split('、').length === 2, '结果恰好 2 条概念')

console.log('\n=== 4) 频次并列 → 保持原顺序 ===')
const tie = buildConceptFreq([{ concept: 'A、B、C' }, { concept: 'A、B、C' }])
eq(pickMainConcept('C、B、A', tie), 'C、B', '全并列时按原顺序取前 2')

console.log('\n=== 5) 边界：概念数 ≤ N 时原样返回 ===')
eq(pickMainConcept('面板、MiniLED', new Map()), '面板、MiniLED', '恰好 2 个 → 原样')
eq(pickMainConcept('面板', new Map()), '面板', '只有 1 个 → 原样')
eq(pickMainConcept('-', freq), '-', '无概念 → 「-」')
eq(pickMainConcept('', freq), '-', '空串 → 「-」')

console.log('\n=== 6) N 可配(默认 2 = 全仓口径) ===')
eq(pickMainConcept('面板、MiniLED、华为概念、OLED', freq, 3), '华为概念、面板、MiniLED',
  'n=3 时取 3 个(频次序), 便于将来一行改口径')

console.log('\n' + (fail ? ('❌ ' + fail + ' 条未通过') : '✅ 全部通过'))
process.exit(fail ? 1 : 0)
