/**
 * 「主要概念」挑选 + 非概念过滤 单测 —— 2026-09-29
 * =====================================================================
 * 背景（主人原话）：「一进二显示的概念有点多，只显示主要的就可以」
 *                  +「昨日一字涨停这种不是概念吧」。
 * 一进二原来直接渲染东财 `f103` —— 概念与**交易属性/状态标签**混在一起的一整串。
 *
 * 被测：`src/utils/conceptMain.js`（纯函数）
 * 口径：① 先过滤非概念（昨日涨停/融资融券/指数成分/机构重仓/预增预减…）；
 *       ② 取 N=3（主人 2026-09-29 定；全仓既有口径是 2）；
 *       ③ 「哪 3 个」按本名单内的题材共鸣频次降序（并列保持原顺序）；
 *       ④ 以整条概念为单位，不做字符串截断；⑤ 全是属性标签时兜底显示原串。
 *
 * 下面第 2/3 组用的是 **2026-09-29 生产真实串**（当天一进二名单 4 只）作 fixture ——
 * 不是编的数据，改口径时会被这组真实串挡住。
 *
 * 跑法：vite build --ssr _verify/concept_main.spec.js --outDir .cmspec --emptyOutDir \
 *       && node .cmspec/concept_main.spec.js
 *   ⚠️ 不能输出到 /tmp（Node 向上找不到 node_modules）
 *   ⚠️ 不能写顶层 await（esbuild 报 "Top-level await is not available"）
 */
import { splitConcepts, themeConcepts, isThemeConcept, buildConceptFreq, pickMainConcept } from '../src/utils/conceptMain.js'

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

// ---- 生产真实串（2026-09-29 一进二名单 4 只，东财 f103 原样）----
const REAL = {
  九阳: '融资融券,电商概念,智能家居,深股通,昨日涨停,富时罗素,标准普尔,昨日涨停_含一字,净水概念,昨日首板,东方财富热股,最近多板,近期新高,趋势股,题材股',
  津药: '滨海新区,沪股通,昨日涨停,辅助生殖,昨日涨停_含一字,昨日首板,昨日高振幅,东方财富热股,最近多板',
  中新: '互联网服务,深圳特区,机构重仓,融资融券,大数据,网络安全,安防概念,央国企改革,国产软件,人工智能,昨日涨停,工业互联网,富时罗素,标准普尔,车联网(车路云),职业教育,数据安全,昨日涨停_含一字,算力概念,数据要素,低空经济,DeepSeek概念,AI应用,东方财富热股,最近多板,趋势股,题材股',
  浙江新能: '新能源,光伏概念,风能,融资融券,央国企改革,中证500,上证380,沪股通,昨日涨停,氢能源,抽水蓄能,绿色电力,昨日涨停_含一字,昨日首板,昨日高振幅,东方财富热股,最近多板,小盘股,小盘成长,破增发价股,2026中报预减',
}

console.log('=== 1) splitConcepts 拆分口径（东财实际用半角逗号） ===')
eq(splitConcepts('面板、MiniLED、华为概念'), ['面板', 'MiniLED', '华为概念'], '顿号拆分')
eq(splitConcepts('面板,MiniLED，华为概念;OLED'), ['面板', 'MiniLED', '华为概念', 'OLED'], '混用分隔符(半角/全角/分号)')
eq(splitConcepts(REAL.津药).length, 9, '真实串(津药)拆出 9 条')
eq(splitConcepts('  面板 、  MiniLED '), ['面板', 'MiniLED'], '首尾空白会被去掉')
eq(splitConcepts(''), [], '空串 → []')
eq(splitConcepts('-'), [], '「-」→ []')
eq(splitConcepts(null), [], 'null → []')

console.log('\n=== 2) 非概念标签过滤（主人：「昨日一字涨停这种不是概念吧」）===')
;[['昨日涨停', '昨日涨停'], ['昨日涨停_含一字', '带后缀变体'], ['昨日首板', '首板'],
  ['融资融券', '融资融券'], ['深股通', '深股通'], ['富时罗素', '富时罗素'], ['标准普尔', '标准普尔'],
  ['中证500', '指数成分'], ['上证380', '指数成分'], ['机构重仓', '机构重仓'],
  ['东方财富热股', '平台标签'], ['最近多板', '状态'], ['近期新高', '状态'],
  ['趋势股', '风格'], ['题材股', '风格'], ['小盘股', '风格'], ['小盘成长', '风格'],
  ['破增发价股', '属性'], ['2026中报预减', '财报事件'], ['昨日高振幅', '状态'], ['次新股', '属性'],
].forEach(([name, why]) => ok(!isThemeConcept(name), '过滤非概念: ' + name + '（' + why + '）'))
;[['电商概念'], ['净水概念'], ['辅助生殖'], ['滨海新区'], ['大数据'], ['网络安全'], ['安防概念'],
  ['央国企改革'], ['国产软件'], ['人工智能'], ['车联网(车路云)'], ['数据安全'], ['算力概念'],
  ['数据要素'], ['低空经济'], ['DeepSeek概念'], ['AI应用'], ['新能源'], ['光伏概念'], ['风能'],
  ['氢能源'], ['抽水蓄能'], ['绿色电力'], ['深圳特区'],
].forEach(([name]) => ok(isThemeConcept(name), '保留真题材: ' + name))

console.log('\n=== 3) 真实串过滤结果 ===')
eq(themeConcepts(REAL.中新), ['互联网服务', '深圳特区', '大数据', '网络安全', '安防概念', '央国企改革',
  '国产软件', '人工智能', '工业互联网', '车联网(车路云)', '职业教育', '数据安全', '算力概念',
  '数据要素', '低空经济', 'DeepSeek概念', 'AI应用'], '中新赛克: 滤掉 9 个非概念, 保留 17 个题材')
const zj = themeConcepts(REAL.浙江新能)
ok(zj.indexOf('新能源') === 0 && zj.indexOf('绿色电力') > 0, '浙江新能: 题材顺序不变')
ok(['融资融券', '中证500', '上证380', '昨日涨停', '小盘股', '2026中报预减'].every(x => zj.indexOf(x) < 0),
  '浙江新能: 属性/指数/状态/财报类全部滤掉')

console.log('\n=== 4) 主要概念: N=3 + 按本名单题材共鸣频次 ===')
const rows = [{ concept: REAL.九阳 }, { concept: REAL.津药 }, { concept: REAL.中新 }, { concept: REAL.浙江新能 }]
const freq = buildConceptFreq(rows)
eq(freq.get('央国企改革'), 2, '题材频次: 央国企改革在名单里出现 2 次')
eq(freq.get('融资融券'), undefined, '非概念不进频次统计(融资融券=undefined)')
eq(freq.get('昨日涨停'), undefined, '非概念不进频次统计(昨日涨停=undefined)')
// 中新赛克: 央国企改革(2) 最高, 其余 1 次 ⇒ 按原顺序补 互联网服务、深圳特区
eq(pickMainConcept(REAL.中新, freq), '央国企改革、互联网服务、深圳特区',
  '中新赛克 → 3 个(频次最高的「央国企改革」提到最前)')
eq(pickMainConcept(REAL.浙江新能, freq), '央国企改革、新能源、光伏概念', '浙江新能 → 3 个')
eq(pickMainConcept(REAL.津药, freq), '滨海新区、辅助生殖', '津药药业: 过滤后只剩 2 个题材 → 就显示 2 个')

console.log('\n=== 5) 整条概念为单位, 绝不切断英文概念 ===')
const got1 = pickMainConcept('MiniLED、CPO、HBM、PCB', new Map())
eq(got1, 'MiniLED、CPO、HBM', '无频次时退化为原顺序前 3')
ok(!/(^|、)Mini($|、)/.test(got1), '不会出现被切半的「Mini」')

console.log('\n=== 6) 边界与兜底 ===')
eq(pickMainConcept('面板、MiniLED、华为概念', new Map()), '面板、MiniLED、华为概念', '恰好 3 个 → 原样')
eq(pickMainConcept('面板、MiniLED', new Map()), '面板、MiniLED', '少于 3 个 → 原样')
eq(pickMainConcept('-', freq), '-', '无概念 → 「-」')
eq(pickMainConcept('', freq), '-', '空串 → 「-」')
eq(pickMainConcept('次新股,融资融券', freq), '次新股、融资融券', '兜底: 全是属性标签时宁可显示也不留空')
eq(pickMainConcept('面板、MiniLED、华为概念、OLED', freq, 2), '面板、MiniLED',
  'n 可配(传 2 即回退到全仓旧口径)')

console.log('\n' + (fail ? ('❌ ' + fail + ' 条未通过') : '✅ 全部通过'))
process.exit(fail ? 1 : 0)
