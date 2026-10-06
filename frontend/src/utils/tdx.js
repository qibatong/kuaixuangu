// 通达信相关: 下载 .blk / 点击联动 / 复制代码
import { showToast } from './toast'
import { isNative } from './native'
import { openStockChart } from '../composables/uiBus'

// 通达信 .blk 标准格式: 每行 "市场#代码" (0=深 1=沪 2=北), 纯ASCII无BOM
export function marketPrefix(code) {
  if (code.startsWith('6')) return '1'
  if (code.startsWith('4') || code.startsWith('8')) return '2'
  return '0'
}

export function downloadBlkFile(stocks, count, suffix = '') {
  const list = count ? stocks.slice(0, count) : stocks
  if (!list.length) { showToast('无数据', 'error'); return }
  const lines = list.map(it => `${marketPrefix(it.code)}#${it.code}`).join('\r\n')
  const blob = new Blob([lines], { type: 'text/plain' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  const now = new Date()
  a.download = `自选股_${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}${suffix}.blk`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  setTimeout(() => URL.revokeObjectURL(a.href), 3000)
  showToast('✅ 已下载 .blk，工具会自动导入通达信', 'success')
}

// 通达信「唤起客户端」是否可用。
//
// 🔴 2026-10-06 主人指令：**全端（电脑 / 网页 / 手机）点击个股一律显示个股详情/分时，
//    不再唤起通达信客户端** ⇒ 本开关恒为 false（保留函数与调用点，只关掉行为）。
//
// 历史：2026-10-04 曾在安卓壳内屏蔽（`return !isNative()`），因为 treeid 老协议在安卓
// WebView 里是打不开的域名。但那次的判断范围太窄 —— 桌面与移动浏览器仍会跳走。
export function canLinkTdx() {
  return false
}

/**
 * 点击股票 → **打开个股详情（分时/K线）**。
 *
 * 🔴 2026-10-06 主人指令：全端一律看详情，**不再跳转通达信**。
 *    原实现是 `setTimeout(() => { window.location.href = 'http://www.treeid/code_' + code })`
 *    唤起通达信客户端（仅安卓壳内被 `canLinkTdx()` 屏蔽）。
 *
 * ⚠️ 为什么改本函数而不是删这些调用点：全站有 **16 处** `@click="linkToSoftware(...)"`
 *    （AuctionView 9 处、YidongView/HistoryView/MarketView/MedalPanel/HisPickPanel/
 *    DevWarnList/ZhPicksPanel/SectorRotationPanel/HotRankMulti/TodayPicksPanel 各若干），
 *    其中 **7 处不在** App.vue 全局委托的选择器覆盖内
 *    （`.medal-code` / `.msd-cell.msd-name` / 非 `td` 的 `.code-click` / `<tr>` 整行）——
 *    那些位置原本既弹不出详情、又照旧跳通达信。
 *    把本函数改成与全局委托**同一动作**（`uiBus.openStockChart`），
 *    16 处一次性全部变成"弹详情"，且 **不碰任何模板**、零回归面。
 *
 * 与 App.vue 的关系：全局委托在**捕获阶段**先跑（会 `stopPropagation`），
 *    所以绝大多数表格里本函数根本轮不到执行；本函数是**兜底**，
 *    负责那些选择器覆盖不到的点位。两者动作一致，谁先跑都不冲突。
 */
export function linkToSoftware(code, name = '') {
  if (!code) return
  openStockChart(code, name)
}

export function copyText(text, okMsg) {
  const done = () => showToast(okMsg, 'success')
  const fallback = () => {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    try { document.execCommand('copy'); done() } catch (e) { showToast('复制失败，请手动复制', 'error') }
    document.body.removeChild(ta)
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(fallback)
  } else {
    fallback()
  }
}
