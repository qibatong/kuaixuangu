<template>
  <div class="page-shell">
    <h1 class="visually-hidden">自选</h1>
    <!-- 页面标题 + 简说明 -->
    <div class="pool-page-head">
      <div class="pool-page-title"><i class="fa fa-database"></i> 自选股票池</div>
      <div class="pool-page-sub">9:30 前自动收录竞价前五，也可手动「加入当前前五 / 加入全部」</div>
    </div>

    <!-- 通达信下载工具区: 从首页迁入, 集中管理 -->
    <div class="tdx-toolbar">
      <!-- 2026-10-04 安卓壳（主人要求：隐藏「唤起客户端」，只留「下载 .blk」）：
           · tdx_import.exe 是 **Windows 专属** ⇒ 壳内不挂载（点了也用不了）；
           · .blk 是**纯文本文件**，安卓上有用（存到「下载」目录后可手动导入通达信）⇒ 保留，
             但文案改成不带「自动导入」（壳内没有剪贴板监控，做不到自动）。 -->
      <a v-if="!isNativeApp" href="/download/tdx_import.exe" class="tdx-export-btn tdx-only nav-btn nav-tdx"><i class="fa fa-windows"></i> 下载通达信工具</a>
      <button v-if="!isNativeApp" class="tdx-export-btn tdx-only nav-btn nav-pool-import" data-tip="💡 首次用：先点「下载通达信工具」并运行，再在通达信『选项/工具』勾选『监控剪贴板』，之后点下载即可自动导入" @click="downloadAll"><i class="fa fa-download"></i> 下载自选股(自动导入)</button>
      <button v-else class="tdx-export-btn tdx-only nav-btn nav-pool-import" data-tip="💡 手机上不会自动导入：文件存到「下载」目录，到电脑上导入通达信" @click="downloadAll"><i class="fa fa-download"></i> 下载自选股 .blk</button>
      <button class="tdx-export-btn pool-btn" @click="copyCodes"><i class="fa fa-copy"></i> 复制代码列表</button>
      <div class="tdx-tip">💡 通达信导入需在<b>电脑端</b>操作；手机上可点「复制代码列表」</div>
    </div>

    <!-- 自选池(共享同一 store, 与首页/竞价异动的加自选互通) -->
    <StockPoolPanel />
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted } from 'vue'
import StockPoolPanel from '../components/StockPoolPanel.vue'
import { useStocksStore } from '../stores/stocks'
import { usePoolStore } from '../stores/pool'
import { useUserStore } from '../stores/user'
import { showToast } from '../utils/toast'
import { copyText, downloadBlkFile } from '../utils/tdx'
import { isNative } from '../utils/native'

const stocks = useStocksStore()
const pool = usePoolStore()
const user = useUserStore()
const isNativeApp = isNative()   // 安卓壳内隐藏 Windows 专属的 tdx 导入功能

let autoAddTimer = null
let expiryTimer = null

// 2026-09-27 (v4.11.75): 自选池「只取竞价名单」是有意为之, 不是遗漏。
// 盘中实时(spot)已于 v4.11.75 重新上线, 但它不适合进自选池:
//   1) 收录时机对不上 —— autoAdd() 只认「9:30 前」(cachedStocks 是 9:26 定格的竞价名单);
//      spot 无 9:26 闸门、盘中随时在变, 没有稳定的"收录时刻"。
//   2) 字段结构对不上 —— 池内记录的是 bidChange(竞涨), 而 spot 只有 realChange(现涨),
//      混入会让池子的字段语义分裂。
//   3) 自选池是「盘前决策留痕」, spot 是「盘中临时看板」, 两者生命周期不同。
// 故 spot 名单不进池; 用户如需从 spot 加自选可用行内「加自选」按钮(PoolHoverBtn)手动加。
function currentList() {
  return stocks.cachedStocks
}
function canAutoAdd() {
  return stocks.isDataCached
}

function downloadAll() { downloadBlkFile(pool.stockPool, 0) }
function copyCodes() {
  if (!pool.stockPool.length) { showToast('无数据', 'error'); return }
  copyText(pool.stockPool.map(s => s.code).join('\n'), `✅ 已复制 ${pool.stockPool.length} 个自选代码，可粘贴到电脑端导入`)
}

onMounted(async () => {
  user.migrateLegacyKeys()
  pool.loadFromStorage()
  // 初始化筛选状态 + 拉数据, 保证「加入当前前五/全部」有数据可用
  try {
    await Promise.all([stocks.loadUserPrefs(), stocks.loadGlobalDefaults()])
    stocks.initFilterFromStorage()
  } catch (e) { /* 静默: 筛选偏好不影响展示 */ }

  if (!stocks.isDataCached) {
    try {
      await stocks.fetchAndCache()
    } catch (e) {
      showToast('❌ ' + e.message, 'error')
    }
  }

  pool.autoAdd(currentList(), canAutoAdd())
  autoAddTimer = setInterval(() => pool.autoAdd(currentList(), canAutoAdd()), 20000)
  expiryTimer = setInterval(() => pool.checkExpiry(), 30000)
})

onBeforeUnmount(() => {
  if (autoAddTimer) clearInterval(autoAddTimer)
  if (expiryTimer) clearInterval(expiryTimer)
})
</script>

<style scoped>
.pool-page-head {
  margin: 0 0 var(--s2);
}
.pool-page-title {
  font-size: var(--fs-2xl);
  font-weight: 700;
  color: var(--text-main);
  letter-spacing: 1px;
  display: flex;
  align-items: center;
  gap: var(--s2);
}
.pool-page-title i { color: var(--accent); }
.pool-page-sub {
  font-size: var(--fs-xs);
  color: var(--text-muted);
  margin-top: var(--s1);
}

/* 通达信工具栏: 紧凑横排, 集中管理下载入口 */
.tdx-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--s2);
  margin: 0 0 var(--s2);
  padding: var(--s2) var(--s4);
  background: var(--bg-panel);
  border: 1px solid rgba(var(--accent-rgb), 0.25);
  border-radius: var(--r-lg);
  backdrop-filter: blur(4px);
}
.tdx-toolbar .tdx-export-btn {
  margin: 0;
}
.tdx-toolbar .tdx-tip {
  font-size: var(--fs-xs);
  color: var(--text-muted);
  margin-left: auto;
}
body[data-bg="light"] .tdx-toolbar {
  background: rgba(255,255,255,0.85);
  border-color: rgba(184,48,16,0.25);
}
body[data-bg="light"] .tdx-toolbar .tdx-tip {
  color: var(--warn-amber);
}
</style>