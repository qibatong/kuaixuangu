<template>
  <div class="page-shell">
    <!-- 页面标题 + 简说明 -->
    <div class="pool-page-head">
      <div class="pool-page-title"><i class="fa fa-database"></i> 自选股票池</div>
      <div class="pool-page-sub">9:30 前自动收录竞价前五，也可手动「加入当前前五 / 加入全部」</div>
    </div>

    <!-- 通达信下载工具区: 从首页迁入, 集中管理 -->
    <div class="tdx-toolbar">
      <a href="/download/tdx_import.exe" class="tdx-export-btn tdx-only nav-btn nav-tdx"><i class="fa fa-windows"></i> 下载通达信工具</a>
      <button class="tdx-export-btn tdx-only nav-btn nav-pool-import" data-tip="💡 首次用：先点「下载通达信工具」并运行，再在通达信『选项/工具』勾选『监控剪贴板』，之后点下载即可自动导入" @click="downloadAll"><i class="fa fa-download"></i> 下载自选股(自动导入)</button>
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

const stocks = useStocksStore()
const pool = usePoolStore()
const user = useUserStore()

let autoAddTimer = null
let expiryTimer = null

function currentList() {
  return stocks.mode === 'spot' ? stocks.spotStocks : stocks.cachedStocks
}
function canAutoAdd() {
  return stocks.isDataCached || stocks.isSpotCached
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

  if (!stocks.isDataCached && !stocks.isSpotCached) {
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
  margin: 0 0 10px;
}
.pool-page-title {
  font-size: 20px;
  font-weight: 800;
  color: var(--text-main);
  letter-spacing: 1px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.pool-page-title i { color: var(--accent); }
.pool-page-sub {
  font-size: 12px;
  color: var(--text-muted);
  margin-top: 4px;
}

/* 通达信工具栏: 紧凑横排, 集中管理下载入口 */
.tdx-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 0 0 10px;
  padding: 10px 14px;
  background: var(--bg-panel);
  border: 1px solid rgba(var(--accent-rgb), 0.25);
  border-radius: 10px;
  backdrop-filter: blur(4px);
}
.tdx-toolbar .tdx-export-btn {
  margin: 0;
}
.tdx-toolbar .tdx-tip {
  font-size: 12px;
  color: var(--text-muted);
  margin-left: auto;
}
body[data-bg="light"] .tdx-toolbar {
  background: rgba(255,255,255,0.85);
  border-color: rgba(184,48,16,0.25);
}
body[data-bg="light"] .tdx-toolbar .tdx-tip {
  color: #8a5500;
}
</style>