<template>
  <div class="container" :class="{ 'has-tabbar': showTabBar }">
    <!-- 2026-10-04 登录页(meta.bare)独立布局: 顶栏/二级 pill 不挂载, 登录页全屏铺满 -->
    <template v-if="!isBare">
      <NavBar />
      <!-- 2026-09-27 v4.11.58 信息架构改造: 二级页 pill 行, 随当前一级分组列出该组二级页
           （手机端同样显示 —— 底部 tab 只切一级分组, 组内切换靠这一行;
            v4.11.61 起「竞价」组标了 hidePills, 该组不渲染这一行）
           2026-10-05 (S1): 未登录不渲染 —— 落地页上这些 pill 全部指向需登录的页面, 点了只会
             被守卫弹到 /login, 属于"死链式"点击（整块 v-if 而非 CSS 隐藏, 不残留无用 DOM）。 -->
      <GroupNav v-if="userStore.isLoggedIn" />
    </template>
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
      :mode="chartMode"
    />
    <!-- 2026-10-04 登录页(meta.bare): 页脚/底部tab/PWA安装条也不挂载(登录页要全屏) -->
    <template v-if="!isBare">
      <!-- 2026-09-21 主人拍板: 页脚只保留免责声明一条(规则条/术语图例移除, 术语解释已有各列表头 title 悬浮)
           2026-10-05 (S6) 扩为**合规页脚**: 免责声明 + 三张协议链接 + 联系客服 + 版权/备案。
           背景: 此前全站页脚只有一行免责声明 —— 没有任何"我们是谁 / 我的手机号怎么用 /
           买了能不能退 / 出问题找谁"的入口, 而产品要收手机号与会员费, 这四点直接决定付费意愿,
           备案号在国内还是合规硬要求。 -->
      <footer class="app-footer">
        <nav class="footer-links" aria-label="站点信息">
          <router-link to="/terms">用户协议</router-link>
          <router-link to="/privacy">隐私政策</router-link>
          <router-link to="/refund">退款说明</router-link>
          <!-- 2026-10-05: 「联系客服」升级为可展开面板（原生 <details>，零 JS 状态、零依赖）：
               面板里同时给二维码与"复制微信号"两条路 —— 电脑端扫码、手机端长按保存图片或直接复制。
               二维码缺图时 ContactQr 自身不渲染，面板仍只剩「复制微信号」，不出现裂图。 -->
          <details class="footer-wx">
            <summary class="footer-link-btn">
              <i class="fa fa-weixin" aria-hidden="true"></i> 联系客服
            </summary>
            <div class="footer-wx-pop">
              <ContactQr :width="150" compact />
              <button type="button" class="footer-link-btn" @click="copySupportWx">
                复制微信号 {{ SUPPORT_WECHAT }}
              </button>
            </div>
          </details>
        </nav>
        <div class="disclaimer">本平台仅提供软件工具使用权，不构成任何投资建议，股市有风险，投资需谨慎。</div>
        <div class="footer-copy">
          <span>© {{ year }} 快选</span>
          <!-- 🔴 2026-10-05 (S6) 备案号**待主人提供**: 填好下面 ICP 常量即自动展示(绝不写假号)。
               国内经营性网站需在页脚展示 ICP 备案号并链接到 beian.miit.gov.cn。 -->
          <a v-if="ICP" class="footer-icp" href="https://beian.miit.gov.cn/" target="_blank" rel="noopener">{{ ICP }}</a>
        </div>
      </footer>
      <!-- 2026-09-27: 手机端(≤768px) 底部固定 tab 栏(v4.11.61 起 6 个一级 tab); 登录页/404/管理后台不显示 -->
      <AppTabBar v-if="showTabBar" />
      <!-- 2026-10-03: PWA 提示条(≤768px 才渲染) ——「安装到桌面」+「有新版本·刷新」;
           自带"已安装/7 天内关过"判定 ⇒ 未满足条件时组件内部不渲染任何东西。
           2026-10-04: 安卓壳内本就不该有「装到桌面」⇒ 补 !isBare 并由 PwaBar 组件内再兜底一层。 -->
      <PwaBar v-if="!isNativeApp && !isBare" />
    </template>
  </div>
</template>

<script setup>
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import Watermark from './components/Watermark.vue'
import NavBar from './components/NavBar.vue'
import GroupNav from './components/GroupNav.vue'
import AppTabBar from './components/AppTabBar.vue'
import PwaBar from './components/PwaBar.vue'
// 图表弹窗按需异步加载: 其内部引用了 echarts(数百 KB), 若静态引入会把 echarts 打进首屏主 bundle。
// defineAsyncComponent 让 echarts 相关代码拆成独立 chunk, 首次点开图表才下载。
const StockChartModal = defineAsyncComponent(() => import('./components/StockChartModal.vue'))
import { uiBus, openStockChart, closeStockChart } from './composables/uiBus'
import ContactQr from './components/ContactQr.vue'   // 2026-10-05 客服二维码（页脚展开面板）
import { useTheme } from './composables/useTheme'
import { useUserStore } from './stores/user'
// 2026-10-04 安卓壳: 物理返回键接管(仅壳内生效, 网页恒 no-op)。必须 setup 顶层调用。
import { useAndroidBack } from './composables/useAndroidBack'
import { isNative } from './utils/native'
// 2026-10-05 (S6): 合规页脚用 —— 客服微信号单一来源 + 复用现有剪贴板工具
import { copyText } from './utils/tdx'
import { SUPPORT_WECHAT } from './utils/contact'

// 🔴 2026-10-05 (S6) ICP 备案号 —— **待主人提供后填入**（例如 '浙ICP备2026000000号'）。
//   刻意留空 + v-if：没填就完全不渲染，**绝不显示假备案号**（那比不显示更糟）。
const ICP = ''
const year = new Date().getFullYear()

function copySupportWx() {
  copyText(SUPPORT_WECHAT, '客服微信已复制')
}

const { load: loadTheme } = useTheme()
const userStore = useUserStore()
const route = useRoute()
useAndroidBack()   // 🔴 不要放进 onMounted: 那样卸载时解绑不了 ⇒ 返回键被重复处理
const isNativeApp = isNative()   // 安卓壳: PWA 安装/更新提示条不适用(v-if 不挂载)

// 2026-10-04 登录页独立布局(主人方案): /login 带 meta.bare ⇒ 顶栏/二级pill/页脚/
//   底部tab/PWA安装条全部不渲染, 仅 <main><router-view/></main> 始终在 ——
//   登录遮罩(.auth-overlay)改成不透明全屏后, 手机端登录页就是一张完整的页。
const isBare = computed(() => route.meta.bare === true)

// 2026-10-04 安卓壳: 手动撤掉启动屏(配合 capacitor.config.ts 的 SplashScreen.launchAutoHide=false)。
//   ★ 目的: 默认行为是 Capacitor bridge 一就绪就撤屏, 但**远端页面还在拉** ⇒ 用户先看几百毫秒白屏。
//     改由前端在 DOM 挂载后再撤 ⇒ 视觉上从"品牌红启动屏"直接切到页面，中间没有白帧。
//   ★ 动态 import ⇒ 浏览器打开时**完全不会**下载这个 chunk（只有壳内才加载）。
//   ★ 兜底 3s：万一页面 mount 事件没跑到 / 插件异常，也强制撤屏，绝不让用户永远卡在红屏。
if (isNativeApp) {
  const hideSplash = () => import('@capacitor/splash-screen')
    .then(m => m.SplashScreen.hide())
    .catch(() => { /* 重复 hide/不支持都是静默失败，不必打扰用户 */ })
  onMounted(() => { setTimeout(hideSplash, 200) })
  setTimeout(hideSplash, 3000)
}

// 2026-09-27 v4.11.58: 手机端底部 tab 栏的显示判定。
// 登录页整屏、404 无归属、管理后台保持独立布局 —— 这三种都不挂 tabbar
// （工单 七「只在手机端显示」+「/admin 不显示底部 tabbar」；≤768px 由 AppTabBar 自带媒体查询控制）。
const showTabBar = computed(() => {
  const n = route.name
  return !!userStore.isLoggedIn && n !== 'login' && n !== 'notFound' && n !== 'admin'
})

// ============================================================
// 全局股票图表弹窗: 由 uiBus.chartModal 驱动(全站任意表格点击股票单元格打开)
// ============================================================
const chartVisible = computed({
  get: () => uiBus.chartModal.visible,
  set: (v) => { if (!v) closeStockChart() },
})
const chartCode = computed(() => uiBus.chartModal.code)
const chartName = computed(() => uiBus.chartModal.name)
// 2026-10-04 P1⑥ 个股详情抽屉：≤768px 走底部抽屉（多一个「竞价三时点」tab），
// 桌面仍是原来的居中弹窗 —— 不改已验收的桌面交互。
// 🔴 发版注意：生产目前跑的是「仅推送」版（2026-10-04 换盘时这里曾临时锁成 'modal'，
//    抽屉还没上线生产）；主人验收通过后，用含本段逻辑的 dist 重新部署即生效。
const chartMode = computed(() => {
  try { return window.innerWidth <= 768 ? 'drawer' : 'modal' } catch (e) { return 'modal' }
})

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
      `body { padding: 2px var(--s1) !important; padding-top: env(safe-area-inset-top, 0px) !important; padding-left: max(var(--s1), env(safe-area-inset-left)) !important; padding-right: max(var(--s1), env(safe-area-inset-right)) !important; padding-bottom: env(safe-area-inset-bottom, 0px) !important; } `
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
  padding: 0 var(--s1);
}
/* ============================================================================
   2026-09-27 v4.11.58 信息架构改造: 底部固定 tab 栏的"内容区留底"
   —— 不加这段, 手机端滚到底时最后一行(含免责声明页脚)会被 tabbar 盖住。
   —— 高度必须与 components/AppTabBar.vue 的 56px 常量保持一致(改一处要改两处)。
   —— 用 !important 覆盖 main.css 手机端块里的 `.container { padding: 0 2px}`。
   —— 只在挂了 tabbar 时生效(登录/404/管理后台不加, 免得白留一块空白)。
   ============================================================================ */
@media (max-width: 768px) {
  .container.has-tabbar { padding-bottom: calc(64px + env(safe-area-inset-bottom, 0px)) !important; }
  /* 2026-10-05 (L11): PWA 安装条（56px 高，浮在 tabbar 之上）出现时再多让出一块，
     否则它会把页面最后一段内容压住（实测手机端会员页「不限次」被截断）。
     标记由 components/PwaBar.vue 的 watch(visible) 打在 body 上。 */
  body.has-pwabar .container.has-tabbar {
    padding-bottom: calc(64px + 62px + env(safe-area-inset-bottom, 0px)) !important;
  }
}
/* 网页底部免责声明 (2026-09-21 对比度修正: 提级到 secondary 并去 opacity, 合规文字须最清晰) */
.disclaimer {
  text-align: center;
  font-size: var(--fs-xs);
  color: var(--text-secondary);
  margin: var(--s2) var(--s4) var(--s6);
  line-height: 1.7;
  letter-spacing: 0.3px;
}
/* 无障碍语义地标: 重置 main/footer 默认样式, 避免引入意外外边距 */
.app-main { display: block; }
.app-footer { display: block; }

/* ===== 2026-10-05 (S6) 合规页脚 =====
   三行式: 协议链接行 / 免责声明 / 版权+备案。字号用 --fs-xs, 颜色用 text-dim(浅色主题
   已保证 ≥4.5:1), 避免又多一处低对比度合规文字。 */
.footer-links {
  display: flex; flex-wrap: wrap; gap: var(--s2) var(--s5);
  align-items: center; justify-content: center;
  padding: var(--s4) var(--s4) 0;
}
.footer-links a, .footer-link-btn {
  color: var(--text-muted); font-size: var(--fs-xs); text-decoration: none;
  background: none; border: none; padding: var(--s1) 0; cursor: pointer;
  display: inline-flex; align-items: center; gap: var(--s1);
}
.footer-links a:hover, .footer-link-btn:hover { color: var(--accent); }
/* 2026-10-05 页脚「联系客服」展开面板：原生 <details>，绝对定位向上弹出 ⇒ 展开不推挤页脚布局 */
.footer-wx { position: relative; display: inline-flex; }
.footer-wx > summary { list-style: none; }
.footer-wx > summary::-webkit-details-marker { display: none; }
.footer-wx-pop {
  position: absolute; bottom: calc(100% + var(--s2)); right: 0; z-index: 60;
  display: flex; flex-direction: column; align-items: center; gap: var(--s2);
  padding: var(--s3); min-width: 168px;
  background: var(--bg-panel-solid); border: 1px solid var(--border-soft); border-radius: var(--r-md);
  box-shadow: 0 12px 28px rgba(0, 0, 0, 0.28);
}
.footer-wx-pop .footer-link-btn { white-space: nowrap; }
.footer-copy {
  display: flex; flex-wrap: wrap; gap: var(--s1) var(--s4);
  align-items: center; justify-content: center;
  margin: 0 var(--s4) var(--s6);
  color: var(--text-dim); font-size: var(--fs-xs);
}
.footer-icp { color: var(--text-dim); text-decoration: none; }
.footer-icp:hover { color: var(--accent); }
@media (max-width: 768px) {
  /* 手机端底部有 tabbar: 页脚最后一行再留一点余量, 免得贴住 tab 栏 */
  .footer-copy { margin-bottom: var(--s4); }
}
</style>