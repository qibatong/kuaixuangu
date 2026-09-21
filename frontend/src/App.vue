<template>
  <div class="container">
    <NavBar />
    <!-- 无障碍: 主内容区用语义地标 main 包裹, 读屏可跳转到主内容 -->
    <main class="app-main">
      <router-view />
    </main>
    <Watermark />
    <!-- 全局股票图表弹窗(分时/日K/周K/月K): 全站任意表格点击股票单元格弹出(data 委托在 App) -->
    <StockChartModal
      v-if="chartVisible"
      v-model:visible="chartVisible"
      :code="chartCode"
      :name="chartName"
    />
    <!-- 无障碍: 页脚用语义地标 footer 包裹规则条/术语图例/免责声明 -->
    <footer class="app-footer">
      <div class="footnote">
        <i class="fa fa-bullhorn"></i> 9:30前可唯一选股并缓存 | 9:30后仅更新实时涨幅 | 实时涨幅＜竞价涨幅自动标绿 | 通达信导入：首次需下载工具并勾选通达信「监控剪贴板」一次 | 股票池10小时防刷新锁定
      </div>
      <!-- 5-7: 术语图例 —— 页面缩写/标记统一解释, 降低专业术语歧义 -->
      <div class="footnote glossary">
        术语：现涨=实时涨幅 · 竞涨=竞价涨幅 · 实体=实体涨幅 · 竞额=竞价成交额 · 竞换=竞价换手率 · 自由流通=自由流通市值(亿) · 竞标记=竞价额兜底(非封单额) · 偏离较大=热门股偏离值告警
      </div>
      <div class="disclaimer">本平台仅提供软件工具使用权，不构成任何投资建议，股市有风险，投资需谨慎。</div>
    </footer>
  </div>
</template>

<script setup>
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, watch } from 'vue'
import Watermark from './components/Watermark.vue'
import NavBar from './components/NavBar.vue'
// 图表弹窗按需异步加载: 其内部引用了 echarts(数百 KB), 若静态引入会把 echarts 打进首屏主 bundle。
// defineAsyncComponent 让 echarts 相关代码拆成独立 chunk, 首次点开图表才下载。
const StockChartModal = defineAsyncComponent(() => import('./components/StockChartModal.vue'))
import { uiBus, openStockChart, closeStockChart } from './composables/uiBus'
import { useTheme } from './composables/useTheme'
import { useUserStore } from './stores/user'

const { load: loadTheme } = useTheme()
const userStore = useUserStore()

// ============================================================
// 全局股票图表弹窗: 由 uiBus.chartModal 驱动(全站任意表格点击股票单元格打开)
// ============================================================
const chartVisible = computed({
  get: () => uiBus.chartModal.visible,
  set: (v) => { if (!v) closeStockChart() },
})
const chartCode = computed(() => uiBus.chartModal.code)
const chartName = computed(() => uiBus.chartModal.name)

// 全局点击事件委托: 点击股票代码/名称单元格 → 弹 分时/K线 图(生产机还原版)
// 覆盖: 首页选股/自选/竞价异动(.stock-info-cell)、连板天梯/市场雷达(td.code-click)、
// 异动监管/市场雷达成分股等(.name-col / [data-stock-code])。
function onDocClick(ev) {
  // 不拦截 按钮/链接/输入/表头/自定义 no-chart 交互区
  if (ev.target.closest('button, a, input, select, textarea, th, [data-no-chart], [role="button"]')) return
  const cell = ev.target.closest('.stock-info-cell, .st-row-cell-code, .st-cell-code, td.code-click, .name-col, [data-stock-code]')
  if (!cell) return
  let code = cell.getAttribute('data-stock-code') || ''
  let name = cell.getAttribute('data-stock-name') || ''
  const row = cell.closest('tr')
  // 单元格内提取(stock-info-cell 风格: 内含 .stock-code/.stock-name)
  if (!code) { const c = cell.querySelector('.stock-code'); if (c) code = c.textContent.trim() }
  if (!name) { const n = cell.querySelector('.stock-name'); if (n) name = n.textContent.trim() }
  // 点名称列(.name-col)或代码列(td.code-click)时, 从同一行补另一字段
  if (!code && row) { const cc = row.querySelector('td.code-click'); if (cc) code = cc.textContent.trim() }
  if (!name && row) { const nm = row.querySelector('.name-col .name-main') || row.querySelector('.stock-name'); if (nm) name = nm.textContent.trim() }
  // 只有合法股票代码(6位数字)才弹图: 避免把板块行(boardCode 等)误当股票
  if (!/^\d{6}$/.test(code)) return
  openStockChart(code, name)
  ev.stopPropagation()
}

// 登录/登出(用户名变化)后重新拉取账号主题偏好
watch(() => userStore.username, () => loadTheme())

// 980 虚拟视口修复函数: 提升到 setup 顶层, 便于 onBeforeUnmount 解绑 resize/orientationchange
let fixViewportIfNeeded = null

onMounted(() => {
  // 全局股票单元格点击 → 弹分时/K线(捕获阶段, 抢在单元格自己的 linkToSoftware 之前)
  document.addEventListener('click', onDocClick, true)
  // 应用主题: index.html 初始为黑色, 挂载后读取账号 preference 覆盖
  loadTheme()
  if (/Android|iPhone|iPad|iPod|Mobile|MicroMessenger/i.test(navigator.userAgent)) {
    document.body.classList.add('is-mobile')
  }

  /* ============================================================================
     2026-08-20  iPhone 14 Pro Max / iOS WKWebView / 微信 WebView 980 虚拟视口修复
     —— 用户反馈"还是完全没变化"的终极原因:
        viewport meta 写了 width=device-width, 但一部分老 iOS WebView / 壳 WebView
        仍然忽略它, 退回默认 980px 虚拟视口:
          window.innerWidth = 980  (虚拟视口宽, 页面"以为"自己是 980px 宽)
          screen.width      = 430  (iPhone 14 Pro Max 真实物理 CSS 像素宽)
          devicePixelRatio  = 3
        结果: 100vw = 980, filter row2 被 space-between 撑开 6 格全横排, 祖先被撑到 980,
        最后物理屏幕右边把 980-430=550px 内容直接切掉, 用户截图看就是"完全没变、右边还是没了".

     修复: 页面一加载立刻检测, 若发现【移动端 UA + (innerWidth > 700 或 innerWidth > 1.4*screen.width)】
     则认定为 980 虚拟视口, 立即:
       1) 覆盖 viewport meta 为 width=<物理屏宽>, initial-scale=1.0
       2) 注入 <style id=__vpfix__> 把 html/body/#app 硬锁到 screen.width, 优先级最高
       3) 把 padding-left 硬塞回 body 避免贴边太丑
       4) orientationchange 再触发一次, 横竖屏切换时重新计算
     ============================================================================ */
  fixViewportIfNeeded = () => {
    const ua = navigator.userAgent || ''
    const isMobileUA = /Android|iPhone|iPad|iPod|Mobile|MicroMessenger|HarmonyOS|XiaoMi|MIUI|Oppo|Vivo/i.test(ua)
    const dpr = Math.max(1, Math.round(window.devicePixelRatio || 1))
    const iw = Math.round(window.innerWidth)
    const sw = Math.round(window.screen?.width || iw)
    // 2026-08-20 横屏修复: 旧判定 `iw > 700` 会把 iPhone 横屏 (iw=932) 错判为 980 虚拟视口,
    // 导致 logicalW 被硬锁到 430 (屏宽短边), 横屏视觉上 = "竖屏那团挤在中间".
    //
    // 真正的 980 虚拟视口 (iOS 13 及以下 WKWebView 的 bug) 有个决定性特征:
    //   devicePixelRatio ≈ 1 (它连 Retina 都识别不了, 所有设备都当 1x 屏)
    // 正常 iPhone (iOS 14+) 不论是竖屏 430 还是横屏 932, dpr 都是 2 或 3.
    // 所以只有 "移动端 UA + innerWidth 远大于物理屏 + DPR ≈ 1" 才是真 980 虚拟视口.
    const suspect980 = isMobileUA && iw > 800 && dpr < 1.5

    let logicalW
    if (suspect980) {
      // 真 980 虚拟视口 → 锁到物理屏短边 (用 screen.width 作为基准)
      logicalW = Math.max(320, Math.min(540, sw || 390))
    } else if (isMobileUA) {
      // 正常手机 (iOS 14+/Android Chrome):
      //   竖屏 iw=430 → logicalW=430
      //   横屏 iw=932 → logicalW=932, 用满横屏宽度 (这是用户要的"横屏适配")
      logicalW = Math.max(320, Math.min(900, iw))
    } else {
      // 桌面端, 不走这套
      return
    }

    // 1) 覆盖/插入 viewport meta
    let vp = document.querySelector('meta[name="viewport"]')
    if (!vp) {
      vp = document.createElement('meta')
      vp.setAttribute('name', 'viewport')
      document.head.appendChild(vp)
    }
    vp.setAttribute(
      'content',
      `width=${logicalW}, initial-scale=1.0, maximum-scale=2.0, user-scalable=yes, viewport-fit=cover`
    )

    // 2) 注入 !important 样式把 html/body/#app 锁死到 logicalW, 最高优先级 (比 main.css 顶部还高)
    let s = document.getElementById('__vpfix__')
    if (!s) {
      s = document.createElement('style')
      s.id = '__vpfix__'
      document.head.appendChild(s)
    }
    s.textContent =
      `html, body, #app { width: ${logicalW}px !important; max-width: ${logicalW}px !important; overflow-x: hidden !important; min-width: 0 !important; } ` +
      `body { padding: 2px 4px !important; padding-top: env(safe-area-inset-top, 0px) !important; padding-left: max(4px, env(safe-area-inset-left)) !important; padding-right: max(4px, env(safe-area-inset-right)) !important; padding-bottom: env(safe-area-inset-bottom, 0px) !important; } `
  }

  try {
    fixViewportIfNeeded()
    window.addEventListener('resize', fixViewportIfNeeded, { passive: true })
    window.addEventListener('orientationchange', fixViewportIfNeeded, { passive: true })
    // WKWebView 首帧 layout 可能在 viewport meta 生效之前, 延迟 150ms 再跑一次确保覆盖
    setTimeout(fixViewportIfNeeded, 150)
    setTimeout(fixViewportIfNeeded, 600)
  } catch (_) { /* ignore */ }
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick, true)
  // 解绑 980 虚拟视口修复的 resize/orientationchange 监听(2026-09-21 修复泄漏)
  if (fixViewportIfNeeded) {
    window.removeEventListener('resize', fixViewportIfNeeded)
    window.removeEventListener('orientationchange', fixViewportIfNeeded)
  }
})
</script>

<style scoped>
/* 2026-08-20 手机端终极宽度锁定: App 根容器 .container 必须被 #app (物理屏宽度)硬约束,
   否则老 CSS chunk max-width:1500px 缓存未清时, 会在 375 物理屏撑到 980/1500,
   直接被屏幕右边切 → 用户反馈"筛选输入框右边被遮住看不到了".
   scoped 比 main.css 全局特异性更高, 不会被缓存覆盖. */
.container {
  width: 100% !important;
  max-width: 100% !important;
  box-sizing: border-box !important;
  overflow-x: hidden !important;
  min-width: 0 !important;
  margin: 0 auto;
  padding: 0 4px;
}
/* 网页底部免责声明 (2026-09-21 对比度修正: 提级到 secondary 并去 opacity, 合规文字须最清晰) */
.disclaimer {
  text-align: center;
  font-size: 0.75rem;
  color: var(--text-secondary);
  margin: 8px 16px 24px;
  line-height: 1.7;
  letter-spacing: 0.3px;
}
/* 无障碍语义地标: 重置 main/footer 默认样式, 避免引入意外外边距 */
.app-main { display: block; }
.app-footer { display: block; }
/* 5-7: 术语图例行 —— 比正文规则条更淡、更紧凑 */
.footnote.glossary {
  font-size: 0.6875rem;
  color: var(--text-dim);
  margin-top: 6px;
  padding-top: 8px;
  line-height: 1.8;
}
</style>