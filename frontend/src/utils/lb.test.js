// lb.js 纯函数单测(node:test 零依赖 ESM 版)
//
// 重点不只是"映射对不对", 更是把**三条边界**变成可执行断言:
//   ① 未知不能渲染成「新启动」(把故障伪装成结论);
//   ② 提示文案里不得出现方向结论(档位实测非单调);
//   ③ 分档表的样本量自洽(整体 n == 各档 n 之和), 防生成脚本出错后静默上线。
//
// ⚠️ 2026-09-23 15:5x: 连板标签的**悬停提示已整体取消**(主人要求), 故 `lbTip` 现在
//    **没有任何组件调用它**。它的单测仍保留 —— `lbTip()` 与 `overallText/overallFoot`
//    一样, 属于"口径文案的唯一权威副本", 将来若要恢复提示不必重写; 但**不要再把它
//    当"线上正在显示的内容"来读**。组件侧的 `title` 已被移除, 由构建产物断言守住。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { LB_LEVELS, LB_STATS, LB_OVERALL, LB_WINDOW, lbLevel, lbLabel, lbClass, lbTip,
         lbStatOf, overallText, overallFoot, countByLevel, dirClass } from './lb.js'

test('LB_LEVELS: 6 档, 与回测分档 1:1(不合并 4 板/5 板+)', () => {
  assert.equal(LB_LEVELS.length, 6)
  assert.deepEqual(LB_LEVELS, ['新启动', '昨首板', '昨2板', '昨3板', '昨4板', '昨5板+'])
})

test('lbLevel: 0..4 原样, ≥5 归到 5 档', () => {
  assert.equal(lbLevel(0), 0)
  assert.equal(lbLevel(1), 1)
  assert.equal(lbLevel(4), 4)
  assert.equal(lbLevel(5), 5)
  assert.equal(lbLevel(9), 5)      // 9 连板与 5 连板同档展示
  assert.equal(lbLevel('3'), 3)    // 后端偶尔下发字符串
})

test('lbLevel: 未知一律 null —— 不能退化成 0(那是「新启动」)', () => {
  for (const v of [null, undefined, '', NaN, 'abc', -1]) {
    assert.equal(lbLevel(v), null, 'lb=' + String(v) + ' 应为 null')
  }
})

test('lbLabel / lbClass: 未知 → 空串(调用方据此不渲染)', () => {
  assert.equal(lbLabel(0), '新启动')
  assert.equal(lbLabel(2), '昨2板')
  assert.equal(lbLabel(5), '昨5板+')
  assert.equal(lbLabel(null), '')
  assert.equal(lbClass(0), 'lb-tag-lv0')
  assert.equal(lbClass(5), 'lb-tag-lv5')
  assert.equal(lbClass(null), '')
  // 类名不得与其它视图已有的 .lb-badge(「N板」橙标签, scoped)同名
  assert.ok(!/lb-badge/.test(lbClass(3)))
})

test('lbTip: 含档位释义 + 连板数来源日 + 同档 n 与收益数字', () => {
  const t = lbTip(2, '2026-09-22')
  assert.match(t, /昨2板/)
  assert.match(t, /2026-09-22 涨停池/)          // 取数日必须显式可见
  assert.match(t, /n=20/)                      // 该档样本量(见回测分档表)
  assert.match(t, /持有 1 日均值/)
  assert.match(t, /持有 5 日均值/)
  assert.match(t, /当日涨停率/)
  assert.match(t, new RegExp(LB_WINDOW))       // 样本区间必须写明
  assert.match(t, /不构成收益承诺/)
})

test('lbTip: 缺 lbDate 时不抛错, 只是少一行', () => {
  const t = lbTip(1, null)
  assert.match(t, /昨首板/)
  assert.ok(!/涨停池/.test(t))
})

test('lbTip: 未知 → 空串(不渲染任何提示)', () => {
  assert.equal(lbTip(null, '2026-09-22'), '')
  assert.equal(lbTip(undefined), '')
})

test('lbTip: n<10 的档位必须带样本不足告警', () => {
  assert.match(lbTip(4, '2026-09-22'), /样本偏少/)     // n=4
  assert.match(lbTip(5, '2026-09-22'), /样本偏少/)     // n=8
  assert.ok(!/样本偏少/.test(lbTip(2, '2026-09-22')))  // n=20 不加告警
})

test('lbTip 文案不得出现方向结论(档位实测非单调)', () => {
  for (let lb = 0; lb <= 6; lb++) {
    const t = lbTip(lb, '2026-09-22')
    assert.ok(!/越高越好|越强越好|建议|推荐|买入信号|看涨|必涨|稳赚/.test(t),
      '第 ' + lb + ' 档提示出现方向性表述: ' + t)
  }
})

test('LB_STATS: 样本量自洽(整体 n == 各档 n 之和), 防生成脚本出错', () => {
  assert.equal(LB_STATS.length, 6)
  assert.deepEqual(LB_STATS.map((s) => s.lvl), [0, 1, 2, 3, 4, 5])
  const sumN = LB_STATS.reduce((a, s) => a + s.n, 0)
  const sumAll = LB_STATS.reduce((a, s) => a + s.all, 0)
  assert.equal(sumN, LB_OVERALL.n, '各档 n 之和应与整体 n 一致')
  assert.ok(sumAll >= sumN, '出票数应不少于同口径样本数')
  LB_STATS.forEach((s) => {
    assert.ok(s.n > 0, s.label + ' 的 n 必须为正')
    assert.ok(s.all >= s.n, s.label + ' 的出票数应不少于样本数')
    assert.ok(typeof s.h1 === 'number' && typeof s.h5 === 'number')
    assert.ok(s.zt >= 0 && s.zt <= 100)
  })
})

test('lbStatOf: 有/无档位', () => {
  assert.equal(lbStatOf(0).label, '新启动')
  assert.equal(lbStatOf(5).label, '昨5板+')
  assert.equal(lbStatOf(9), null)
})

test('overallText / overallFoot: 含口径、样本量与免责', () => {
  const t = overallText()
  assert.match(t, /竞价接力/)
  assert.match(t, /持有 1 日均值/)
  assert.match(t, /胜率/)
  const f = overallFoot()
  assert.match(f, new RegExp(String(LB_OVERALL.n)))
  assert.match(f, new RegExp(String(LB_OVERALL.days)))
  assert.match(f, new RegExp(LB_WINDOW))
  assert.match(f, /不构成收益承诺/)
})

test('countByLevel: 忽略未知, 统计各档只数', () => {
  const c = countByLevel([{ lb: 0 }, { lb: 0 }, { lb: 2 }, { lb: 7 }, {}, { lb: null }])
  assert.deepEqual(c, { 0: 2, 2: 1, 5: 1 })
  assert.deepEqual(countByLevel(null), {})
})

test('dirClass: 红涨绿跌, 0 与缺失不配色', () => {
  assert.equal(dirClass(2.05), 'up')
  assert.equal(dirClass(-3.37), 'down')
  assert.equal(dirClass(0), '')
  assert.equal(dirClass(null), '')
  assert.equal(dirClass(''), '')
})

// 源码级护栏(2026-09-23 15:5x): 主人要求取消连板标签悬停提示 + 隐藏「偏离较大」异动标签。
// 这两条都发生在组件/模板层, 纯函数单测覆盖不到 → 直接读源码断言, 防止将来被改回去。
test('组件护栏: lb-tag 不再绑 title; 偏离较大 统一在 composable 里屏蔽', async () => {
  const fs = await import('node:fs')
  const url = await import('node:url')
  const here = url.fileURLToPath(new URL('.', import.meta.url))
  const table = fs.readFileSync(here + '../components/StockTable.vue', 'utf8')
  const mon = fs.readFileSync(here + '../composables/useYidongMonitor.js', 'utf8')
  const auction = fs.readFileSync(here + '../views/AuctionView.vue', 'utf8')

  // ① 连板标签不得再挂 title(悬停提示已整体取消)
  assert.ok(!/lb-tag[\s\S]{0,120}?:title=/.test(table),
    'lb-tag 不应再绑定 title —— 悬停提示已整体取消')
  // 且组件不得再**调用** lbTip(注释里提到这个词是允许的, 故按调用形式匹配,
  //  并排除 import/注释行 —— 否则"读到下一行的 import 例"会误报)
  const calls = table.split('\n').filter(
    (l) => !/^\s*(import|import\{|\/\/|\*|<!--)/.test(l) && /lbTip\s*\(/.test(l))
  assert.deepEqual(calls, [], 'StockTable 不应再调用 lbTip')

  // ② 「偏离较大」的屏蔽集中在 composable 的 yidongTag() 里 —— 选股表与竞价异动栏共用它,
  //    所以只需在这一处挡住, 两边同时生效。
  assert.match(mon, /HIDDEN_LABELS\s*=\s*\[[^\]]*'偏离较大'/, 'composable 缺少隐藏标签清单')
  assert.match(mon, /HIDDEN_LABELS\.indexOf\(t\)\s*>=\s*0\s*\?\s*''\s*:\s*t/,
    'yidongTag() 未按 HIDDEN_LABELS 过滤')
  // 另两档必须仍在(别把整个功能删了)
  assert.match(mon, /REALTIME:\s*'严重异动'/, '「严重异动」应保留')
  assert.match(mon, /MONITOR:\s*'重点监控'/, '「重点监控」应保留')
  // 竞价异动栏的渲染仍走 yidongTag(即自动继承过滤), 不得自己另写一套判断
  assert.match(auction, /yidongTag\(/, 'AuctionView 应继续用 yidongTag 渲染')
  assert.ok(!/偏离较大/.test(auction), 'AuctionView 不应自己硬编码「偏离较大」判断')
})
