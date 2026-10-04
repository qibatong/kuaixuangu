// 通达信相关: 下载 .blk / 点击联动 / 复制代码
import { showToast } from './toast'
import { isNative } from './native'

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

// 通达信「唤起客户端」是否可用（Windows/桌面浏览器可用；安卓壳内不可用）
// 2026-10-04：手机上点个股的**本意是看详情**（App.vue 全局委托会开个股抽屉），
// 而 treeid:// 这类老协议在安卓 WebView 里是个打不开的域名 ⇒ 必须整体屏蔽，
// 只保留 .blk 下载（文件本身在安卓上是有用的）。
export function canLinkTdx() {
  return !isNative()
}

// 点击股票代码联动通达信(老协议, 没反应就点下载)
export function linkToSoftware(code) {
  if (!code) return
  // 🔴 安卓壳内**静默返回**：不 toast、不跳转。
  //   原因：① `http://www.treeid/...` 在 WebView 里无法解析 ⇒ 跳过去是一片报错页，
  //      用户感知就是"点股票跳转到一个打不开的地方"；
  //   ② 手机端点个股要走 App.vue 的全局委托（≤768px 开底部个股抽屉），
  //      这里若还跳 URL，两个行为会叠加 —— 抽屉刚起来页面就跳走了。
  //   静默（而非提示）是因为它是**同一手势的副作用**，弹 toast 会打扰看行情。
  if (!canLinkTdx()) return
  showToast(`正在唤起通达信：${code}（如浏览器询问请选择"打开"，没反应就点"下载自选股"）`, 'info')
  setTimeout(() => { window.location.href = `http://www.treeid/code_${code}` }, 400)
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
