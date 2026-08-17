<template>
  <div>
    <!-- 规则条 + 顶栏按钮组 -->
    <div class="alert-rule">
      <div class="rule-text"><i class="fa fa-clock-o"></i> <strong>9:30前可重新选股 · 9:30后仅更新实时涨幅</strong></div>
      <div class="right-group">
        <div class="btn-group">
          <button class="tdx-export-btn reset-lock-btn" :disabled="!isBefore930()" @click="reLock"><i class="fa fa-refresh"></i> 重新锁定(9:30前可用)</button>
          <button class="tdx-export-btn real-time-btn" @click="refreshRealTime"><i class="fa fa-refresh"></i> 刷新实时涨幅</button>
          <a href="/download/tdx_import.exe" class="tdx-export-btn tdx-only nav-btn nav-tdx"><i class="fa fa-windows"></i> 下载通达信工具</a>
          <button class="tdx-export-btn tdx-only nav-btn nav-pool-import" data-tip="💡 首次用：先点「下载通达信工具」并运行，再在通达信『选项/工具』勾选『监控剪贴板』，之后点下载即可自动导入" @click="downloadAll"><i class="fa fa-download"></i> 下载自选股(自动导入)</button>
        </div>
        <div id="mobileTdxHint">
          📱 通达信导入需在<b>电脑端</b>操作（电脑上点「下载自选股」即可自动导入）。手机上可点此
          <button class="pool-btn" @click="copyCodes"><i class="fa fa-copy"></i> 复制代码列表</button>
        </div>
        <div class="live-time"><div class="time-digital">{{ bjTime }}</div></div>
      </div>
    </div>

    <!-- 会员门禁: 竞价选股 / 盘中实时选股 仅在工作日 9:15-15:00 要求会员; 其他时段放开 -->
    <VipGate v-if="!user.isMember && isMemberOnlyTime()" title="竞价选股" />

    <template v-else>
    <!-- 市场情绪面板: 涨停家数/情绪值/连板高度(置于模式切换上方, 整体大盘氛围先行) -->
    <SentimentPanel :yizi-today="yiziToday" :yizi-trend="yiziTrend" />

    <!-- 模式切换 Tab: 竞价选股 / 盘中实时选股 -->
    <div class="mode-tabs">
      <button class="mode-tab" :class="{ active: stocks.mode === 'auction' }" @click="switchMode('auction')">
        <i class="fa fa-sun-o"></i> 竞价选股 <span class="mode-desc">9:15-9:31 · 竞价锁定</span>
      </button>
      <button class="mode-tab" :class="{ active: stocks.mode === 'spot' }" @click="switchMode('spot')">
        <i class="fa fa-bolt"></i> 盘中实时选股 <span class="mode-desc">9:30-15:00 · 实时刷新</span>
      </button>
    </div>

    <!-- 筛选面板 -->
    <FilterPanel />

    <!-- 奖牌区(仅竞价模式) -->
    <template v-if="stocks.mode === 'auction'">
      <!-- 左右分栏: 左=金银铜奖牌 + 右=自选股票池(主人要求, 仅这两块并排) -->
      <div class="medal-pool-layout">
        <div class="medal-pool-left">
          <MedalPanel :stocks="stocks.cachedStocks" />
        </div>
        <div class="medal-pool-right">
          <StockPoolPanel />
        </div>
      </div>

      <!-- 奖牌导出 -->
      <!-- 奖牌区下方不展示导出按钮(主流程已有下载自选股, 此处避免重复) -->
    </template>
    <!-- 盘中模式无奖牌: 自选池放主流程 -->
    <template v-else>
      <StockPoolPanel />
    </template>

    <!-- 全部结果导出(上下) -->
    <!-- 主表上下不再放下载按钮(顶部「下载自选股(自动导入)」已覆盖, 此处避免重复) -->


    <!-- 主表: 按模式显示 -->
    <template v-if="stocks.mode === 'spot'">
      <div v-if="!stocks.isSpotCached" class="stock-table-container">
        <div class="loading-placeholder"><div class="spinner"></div><div>正在初始化盘中数据...</div></div>
      </div>
      <StockTable v-else :stocks="stocks.spotStocks" mode="spot" :bid-seal-map="bidSealMap" />
    </template>
    <template v-else>
      <div v-if="!stocks.isDataCached" class="stock-table-container">
        <div class="loading-placeholder"><div class="spinner"></div><div>正在初始化选股数据...</div></div>
      </div>
      <StockTable v-else :stocks="stocks.cachedStocks" mode="auction" :bid-seal-map="bidSealMap" />
    </template>

    <!-- 主表上下不再放下载按钮(顶部「下载自选股(自动导入)」已覆盖, 此处避免重复) -->

    </template>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import FilterPanel from '../components/FilterPanel.vue'
import SentimentPanel from '../components/SentimentPanel.vue'
import MedalPanel from '../components/MedalPanel.vue'
import StockPoolPanel from '../components/StockPoolPanel.vue'
import StockTable from '../components/StockTable.vue'
import VipGate from '../components/VipGate.vue'
import { useStocksStore } from '../stores/stocks'
import { usePoolStore } from '../stores/pool'
import { useUserStore } from '../stores/user'
import { kplBidSeal } from '../api/kpl'
import { showToast } from '../utils/toast'
import { copyText, downloadBlkFile } from '../utils/tdx'
import { bjDateTimeStr, isBefore930, isMemberOnlyTime } from '../utils/time'

const stocks = useStocksStore()
const pool = usePoolStore()
const user = useUserStore()
const bjTime = ref('--:--:--')
const yiziToday = ref(null)       // {yizi_count, bid_amt} 今日一字涨停
const yiziTrend = ref([])         // 近 5 日趋势
const yiziLoaded = ref(false)     // 接口已返回(区分 加载中/暂无)
const bidSealMap = ref({})        // 竞价涨停委买额 map: code -> {limitBoards, bidSealAmt, bidNetAmt}

async function loadBidSeal() {
  try {
    const d = await kplBidSeal()
    const map = {}
    ;(d.list || []).forEach((it) => { map[it.code] = it })
    bidSealMap.value = map
  } catch (e) { /* 竞价委买额可选, 失败静默 */ }
}

async function loadYizi() {
  try {
    const resp = await fetch('/api/stats/daily-yizi?days=5', { headers: { 'Authorization': 'Bearer ' + user.apiToken } })
    const data = await resp.json()
    if (data.ok && data.list && data.list.length) {
      yiziTrend.value = data.list
      yiziToday.value = data.list[0]
    }
  } catch (e) { /* 静默 */ } finally {
    yiziLoaded.value = true
  }
}

let clockTimer = null
let autoAddTimer = null
let expiryTimer = null

async function init() {
  user.migrateLegacyKeys()
  // 初始化股票池
  pool.loadFromStorage()
  // 初始化筛选状态(本地锁定 > 账号偏好 > 全局默认 > 内置默认)
  await Promise.all([stocks.loadUserPrefs(), stocks.loadGlobalDefaults()])
  stocks.initFilterFromStorage()
  // 首次拉数据
  try {
    await stocks.fetchAndCache()
  } catch (e) {
    showToast('❌ ' + e.message, 'error')
  }
  // 启动定时器: 时钟 / 自动收录 / 过期检查
  clockTimer = setInterval(() => { bjTime.value = bjDateTimeStr() }, 1000)
  autoAddTimer = setInterval(() => pool.autoAdd(currentList(), stocks.isDataCached || stocks.isSpotCached), 20000)
  expiryTimer = setInterval(() => pool.checkExpiry(), 30000)
  pool.autoAdd(currentList(), stocks.isDataCached || stocks.isSpotCached)
}

// 当前模式的选股结果(竞价 cachedStocks / 盘中 spotStocks)
function currentList() {
  return stocks.mode === 'spot' ? stocks.spotStocks : stocks.cachedStocks
}

function reLock() {
  stocks.reLockData().catch(e => showToast('❌ ' + e.message, 'error'))
}
function refreshRealTime() {
  if (stocks.mode === 'spot') {
    stocks.updateSpotRealTime().catch(e => showToast('❌ 更新失败：' + e.message, 'error'))
    return
  }
  stocks.updateRealTimeOnly().catch(e => showToast('❌ 更新失败：' + e.message, 'error'))
}
async function switchMode(m) {
  if (stocks.mode === m) return
  stocks.setMode(m)
  // 首次进入该模式时拉一次数据
  try {
    if (m === 'spot') {
      if (!stocks.isSpotCached) await stocks.fetchSpot()
    } else {
      if (!stocks.isDataCached) await stocks.fetchAndCache()
    }
  } catch (e) {
    showToast('❌ ' + e.message, 'error')
  }
}
function downloadAll() { downloadBlkFile(stocks.cachedStocks, 0) }
function copyCodes() {
  if (!stocks.cachedStocks.length) { showToast('无数据', 'error'); return }
  copyText(stocks.cachedStocks.map(s => s.code).join('\n'), `✅ 已复制 ${stocks.cachedStocks.length} 个代码，可粘贴到电脑端导入`)
}

onMounted(() => {
  bjTime.value = bjDateTimeStr()
  init()
  loadYizi()
  loadBidSeal()
})
onBeforeUnmount(() => {
  if (clockTimer) clearInterval(clockTimer)
  if (autoAddTimer) clearInterval(autoAddTimer)
  if (expiryTimer) clearInterval(expiryTimer)
})
</script>

<style scoped>
/* 奖牌(金银铜) + 自选股票池 左右分栏(仅这两块并排; 桌面端两栏, 移动端堆叠) */
.medal-pool-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
  align-items: stretch;          /* 左右两栏高度对齐(默认就是 stretch, 写明) */
  margin: 10px 0 4px;
}
@media (min-width: 900px) {
  /* 平分两栏: 奖牌区与自选股票池各占 50%, 不再左宽右窄 */
  .medal-pool-layout { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
}
/* 左栏: medal-section 撑满整栏高度, 奖牌卡垂直居中(不再漂顶部留大片空白) */
.medal-pool-left { min-width: 0; display: flex; flex-direction: column; }
.medal-pool-left .medal-section { flex: 1; }
/* 右栏固定高度(约 3 个自选股+表头), 自选股超出时 pool-list 内部滚动, 避免拉升左右栏整体高度 */
.medal-pool-right { min-width: 0; height: 300px; }
@media (max-width: 899px) {
  /* 移动端单列堆叠, 恢复自然高度 */
  .medal-pool-right { height: auto; }
}
.yizi-card {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-left: 10px;
  padding: 3px 10px;
  border: 1px solid rgba(var(--accent-rgb), 0.4);
  border-radius: 12px;
  background: rgba(var(--accent-rgb), 0.08);
  color: var(--accent-text);
  font-size: 12px;
  white-space: nowrap;
  cursor: default;
}
.yizi-card b { color: var(--accent-deep); }
.mode-tabs {
  display: flex;
  gap: 10px;
  margin: 10px 0 4px;
}
.mode-tab {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
  background: var(--bg-hover);
  border: 1px solid var(--border-soft);
  color: var(--text-secondary);
  border-radius: 8px;
  padding: 8px 16px;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.2s;
}
.mode-tab:hover { border-color: #ffb400; color: #ffe0a0; }
.mode-tab.active {
  background: rgba(255,180,0,0.12);
  border-color: #ffb400;
  color: #ffd700;
}
.mode-desc {
  font-size: 11px;
  color: var(--text-muted);
}
.mode-tab.active .mode-desc { color: #c9a94a; }

/* 浅色主题覆盖 */
body[data-bg="light"] .mode-tab:hover {  color: #5a4a3a;  }
body[data-bg="light"] .mode-tab {  color: #5a4a3a; border-color: #b83010; background: rgba(255,255,255,0.6);  }
body[data-bg="light"] .mode-tab.active .mode-desc {  color: #6a5a20;  }
body[data-bg="light"] .page-back {  color: #5a6b85;  }
body[data-bg="light"] .page-back:hover {  color: #c79100;  }
body[data-bg="light"] .yizi-card {  color: #8a5500; background: rgba(255,150,50,0.12); border-color: rgba(255,150,50,0.5);  }
body[data-bg="light"] .yizi-card b {  color: #8a4a00;  }
body[data-bg="light"] .filter-tag {  color: #8a5500; background: rgba(255,80,80,0.12);  }
body[data-bg="light"] .filter-divider {  color: rgba(0,0,0,0.3);  }
</style>
