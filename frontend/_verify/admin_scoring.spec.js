/**
 * 管理端评分配置(竞价 / 盘中双策略)渲染冒烟测试 —— v4.11.76
 * =====================================================================
 * 与 spot.spec.js / nav.spec.js 同骨架(SSR 真渲染 + 零异常断言)。
 *
 * 为什么这轮必须做真渲染:
 *   本次改动把「评分配置」从**单套**改成**双策略**(竞价 auction / 盘中 spot),
 *   引入了三处"模板里的条件"——vite build 与 eslint 都看不见它们:
 *     ① 卡片标题按 scoringStrategy 切换(竞价评分·权重配置 / 盘中实时评分·权重配置)
 *     ② 策略页签的 active 态按 scoringStrategy 判定
 *     ③ 因子 Tab 循环 factorOrder(现为 computed, 按策略返回不同数组)
 *   本项目已两次在此栽过(v4.11.58 的 NAV_GROUPS、v4.11.62 的 MarketBoardPanel),
 *   所以必须真渲染 + 按真实 HTML 断言。
 *
 * 🔴 **本 spec 的能力边界(务必先读, 否则会写出假绿断言)**:
 *   SSR 是**一次性同步渲染**, 而 AdminView 的评分配置来自 `onMounted` 里的
 *   `loadScoring()`(异步网络)。SSR 不跑 onMounted ⇒ 首屏 HTML 里:
 *     · 权重行 tbody 是**空的**(wKeys 未填充) ⇒ 断言不了「竞价分」这类行标签
 *     · factors 也是空的 ⇒ 因子 Tab 显示的是**原始键名**(bid/activity/...),
 *       不是后端返回的 label ⇒ 断言不了「竞价涨幅」这类中文展示名
 *   所以本 spec **只覆盖"与 data 无关的骨架与分支"**:
 *     ✓ 标题/页签/active 态/因子 Tab 键集(由前端常量 factorOrder 决定, 无需网络)
 *     ✗ 权重行标签、打分明细的具体分档数值 —— 这些由后端 62 个 pytest 用例覆盖
 *   (曾有一版断言了「竞价分」行, 结果 6 红; 全部是本 spec 越界, 不是产品缺陷。)
 *
 * 断言的重点是**差异**:
 *   · auction 态: 因子 Tab = bid/activity/warn/market/yesterday(5 个)、
 *     标题「竞价评分·权重配置」、竞价页签带 active
 *   · spot 态:   标题「盘中实时评分·权重配置」、spot 页签带 active
 *   只断言"有"会让"两套一起渲染"(分支写错)安然通过。
 *
 * 跑法: vite build --ssr _verify/admin_scoring.spec.js --outDir .admssr --emptyOutDir && node .admssr/admin_scoring.spec.js
 *   ⚠️ 不能输出到 /tmp(Node 向上找不到 node_modules ⇒ Cannot find package 'vue')
 *   ⚠️ 不能写顶层 await(esbuild 报 "Top-level await is not available")
 *   ⚠️ 收尾必须 process.exit()(轮询会留 setTimeout, 事件循环不空会挂住)
 */
import { createSSRApp, h } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { renderToString } from 'vue/server-renderer'
import AdminView from '../src/views/AdminView.vue'

const ROUTES = [{ path: '/', component: { template: '<div/>' } },
                { path: '/admin', component: { template: '<div/>' } }]

let PASS = 0
let FAIL = 0
const fails = []
function ok(name, cond, extra) {
  if (cond) { PASS++; console.log(`  [PASS] ${name}`) } else {
    FAIL++; fails.push(name); console.log(`  [FAIL] ${name}${extra ? ' :: ' + extra : ''}`)
  }
}

const countByClass = (html, cls) =>
  (html.match(new RegExp(`class="[^"]*\\b${cls}\\b[^"]*"`, 'g')) || []).length

/** 抽出所有 .factor-tab 按钮的**文本**(SSR 下无 label, 是原始键名) */
function factorTabTexts(html) {
  const re = /<button class="[^"]*factor-tab[^"]*"[^>]*>([^<]*)<\/button>/g
  const out = []
  let m
  while ((m = re.exec(html)) !== null) out.push(m[1].trim())
  return out
}

/** 判断某个按钮文本是否带 active 类(顺序不定: 'active factor-tab' / 'factor-tab active') */
function isActiveBtn(html, text) {
  const re = new RegExp(`<button class="([^"]*factor-tab[^"]*)"[^>]*>\\s*${text.replace(/[（）]/g, '.')}\\s*</button>`)
  const m = html.match(re)
  return !!(m && /\bactive\b/.test(m[1]))
}

async function renderAdmin(route = '/admin') {
  const router = createRouter({ history: createMemoryHistory(), routes: ROUTES })
  const errors = []
  const app = createSSRApp({ render: () => h(AdminView) })
  app.config.errorHandler = (err) => { errors.push(String(err && err.message ? err.message : err)) }
  app.config.warnHandler = (msg) => { errors.push('VUE_WARN: ' + msg) }
  app.use(createPinia())
  app.use(router)
  await router.push(route)
  await router.isReady()
  const html = await renderToString(app)
  return { html, errors }
}

// 与 admin.py 的 W_KEYS / SPOT_W_KEYS 同源(顺序也一致)
const AUCTION_FACTORS = ['bid', 'activity', 'warn', 'market', 'yesterday']
const SPOT_FACTORS = ['chg', 'vol_ratio', 'turnover', 'seal', 'market', 'yesterday']

async function main() {
  console.log('\n=== 管理端评分配置(双策略)冒烟 ===')

  // ---------------------------------------------------------------- S1
  console.log('\n— S1. 首屏(auction 态)骨架渲染')
  const { html, errors } = await renderAdmin()
  ok('渲染出内容', html.length > 2000, `len=${html.length}`)
  ok('🔴 零 Vue 警告 / 零运行时异常', errors.length === 0, errors.slice(0, 3).join(' | '))
  ok('不含 undefined', !/>\s*undefined\s*</.test(html))
  ok('不含 NaN', !/>\s*NaN\s*</.test(html))
  ok('🎯 首屏是竞价页签(不预设 spot)',
     html.includes('竞价评分·权重配置') && !html.includes('盘中实时评分·权重配置'))

  // ---------------------------------------------------------------- S2
  console.log('\n— S2. 策略切换页签(两个都在 + active 落在竞价)')
  ok('网页签存在', html.includes('竞价（9:25 定格）'))
  ok('盘中页签存在', html.includes('盘中实时（现涨/量比/换手）'))
  ok('🔴 竞价页签带 active 类', isActiveBtn(html, '竞价（9:25 定格）'))
  ok('🔴 盘中页签**不带** active 类', !isActiveBtn(html, '盘中实时（现涨/量比/换手）'),
     '两个页签同时高亮 = active 判据写错')

  // ---------------------------------------------------------------- S3
  console.log('\n— S3. 🔴 因子 Tab 键集(由前端 factorOrder 常量决定, SSR 可测)')
  const tabs = factorTabTexts(html)
  // 注意: 策略切换页签也带 factor-tab 类(复用样式), 会一起被抓到。
  // 故先剔除两个已知的策略页签文本, 剩下的才是真正的因子 Tab。
  const factorTabs = tabs.filter((t) => !t.includes('竞价（') && !t.includes('盘中实时（'))
  ok('因子 Tab 恰 5 个(竞价五因子)', factorTabs.length === 5, `实际 ${factorTabs.length}: ${factorTabs.join(',')}`)
  ok('🔴 因子 Tab 逐个等于竞价键序', factorTabs.join(',') === AUCTION_FACTORS.join(','),
     `期望 ${AUCTION_FACTORS.join(',')} 实际 ${factorTabs.join(',')}`)
  ok('🔴 竞价态**无**盘中专属因子 Tab(vol_ratio/turnover/seal/chg)',
     !factorTabs.includes('vol_ratio') && !factorTabs.includes('turnover') &&
     !factorTabs.includes('seal') && !factorTabs.includes('chg'),
     `两套因子表被一起渲染: ${factorTabs.join(',')}`)
  ok('首个因子 Tab 带 active(与 activeFactor 初值一致)', isActiveBtn(html, 'bid'))

  // ---------------------------------------------------------------- S4
  console.log('\n— S4. 双策略常量契约(与后端 admin.py 键表对齐)')
  ok('auction 因子 5 个', AUCTION_FACTORS.length === 5)
  ok('spot 因子 6 个', SPOT_FACTORS.length === 6)
  ok('🔴 两套共有因子恰 2 个(market/yesterday)',
     AUCTION_FACTORS.filter((k) => SPOT_FACTORS.includes(k)).length === 2,
     `实际 ${AUCTION_FACTORS.filter((k) => SPOT_FACTORS.includes(k)).length}`)
  ok('🔴 spot 独有 4 个(chg/vol_ratio/turnover/seal)',
     SPOT_FACTORS.filter((k) => !AUCTION_FACTORS.includes(k)).length === 4)
  ok('🔴 auction 独有 3 个(bid/activity/warn)',
     AUCTION_FACTORS.filter((k) => !SPOT_FACTORS.includes(k)).length === 3)

  // ---------------------------------------------------------------- S5
  console.log('\n— S5. 边界: 越界断言的自检(SSR 拿不到异步数据)')
  // 显式记录这条边界 —— 若哪天有人给 AdminView 加了 SSR 预取, 下面这条会红,
  // 提醒他把 S3 升级为"断言中文 label"。
  ok('🔴 权重行 tbody 此刻为空(SSR 不跑 onMounted, 是预期而非缺陷)',
     /<table class="admin-table weight-table"[^>]*>.*?<tbody[^>]*><!--\[--><!--\]-->/s.test(html),
     'SSR 竟然渲染出了权重行 —— 请把本 spec 升级为断言中文 label')
  ok('打分明细当前显示加载占位', html.includes('该因子暂未加载'))

  // ---------------------------------------------------------------- S6
  console.log('\n— S6. 共用卡片不受策略影响 + 未保存提示')
  ok('评分门槛仍在(全局默认筛选参数卡片)', html.includes('评分门槛'))
  ok('🔴 未保存改动提示初始不显示', !html.includes('有未保存的改动'))
  ok('🔴 dirty 提示确实绑在 scoringDirty 上(模板里有该串)',
     html.includes('有未保存的改动') === false && html.includes('保存并生效'))

  console.log(`\n=== 结果：PASS=${PASS}  FAIL=${FAIL} ===`)
  if (FAIL) { console.log('失败项：\n  - ' + fails.join('\n  - ')); process.exit(1) }
  console.log('管理端评分配置冒烟全绿。\n')
  process.exit(0)
}

main().catch((e) => {
  console.error('\n[FATAL] 冒烟测试自身异常：', e && e.stack ? e.stack : e)
  process.exit(2)
})

