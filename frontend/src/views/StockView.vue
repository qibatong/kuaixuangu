<template>
  <div>
    <!-- 窄屏(<1100px) 切换栏: 选股 / 竞价异动 (宽屏 50/50 并排, 本栏隐藏) -->
    <div class="home-mob-toggle">
      <button class="home-mob-tab" :class="{ active: mobilePane === 'stock' }" @click="mobilePane = 'stock'">
        <i class="fa fa-sun-o"></i> 选股
      </button>
      <button class="home-mob-tab" :class="{ active: mobilePane === 'auction' }" @click="mobilePane = 'auction'">
        <i class="fa fa-bullhorn"></i> 竞价异动
      </button>
    </div>

    <!-- 首页两块布局: 左=选股主流程, 右=竞价异动; 宽屏 50/50 并排, 窄屏按 mobilePane 切一块 -->
    <div class="home-grid">
      <!-- 市场情绪面板: 横跨左右视图置于最顶部 -->
      <div class="home-sentiment"><SentimentPanel /></div>

      <!-- 左栏: 选股主流程 -->
      <div class="home-col home-col-left" :class="{ 'home-col-hidden': mobilePane !== 'stock' }">
        <!-- 紧凑规则条 + 内联模式切换 + 操作按钮 -->
        <div class="alert-rule alert-rule-compact">
          <span class="rule-text"><strong>9:30前可重新选股 · 9:30后仅更新</strong></span>
          <!-- 模式切换(内联): 竞价 / 盘中 -->
          <span class="mode-tabs mode-tabs-inline">
            <button class="mode-tab mode-tab-compact" :class="{ active: stocks.mode === 'auction' }" @click="switchMode('auction')">竞价</button>
            <button class="mode-tab mode-tab-compact" :class="{ active: stocks.mode === 'spot' }" @click="switchMode('spot')">盘中</button>
          </span>
          <!-- 操作按钮 -->
          <span class="right-group">
            <button class="tdx-export-btn reset-lock-btn" :disabled="!isBefore930()" @click="reLock"><i class="fa fa-lock"></i> 锁定</button>
            <button class="tdx-export-btn real-time-btn" @click="refreshRealTime"><i class="fa fa-refresh"></i> 刷新</button>
          </span>
        </div>

        <!-- 筛选面板 -->
        <div class="home-filter"><FilterPanel /></div>

        <!-- 会员门禁: 竞价选股 / 盘中实时选股 仅在工作日 9:15-15:00 要求会员; 其他时段放开 -->
        <VipGate v-if="!user.isMember && isMemberOnlyTime()" title="竞价选股" />

        <template v-if="user.isMember || !isMemberOnlyTime()">
        <!-- 奖牌区(仅竞价模式) -->
        <MedalPanel v-if="stocks.mode === 'auction'" :stocks="stocks.cachedStocks" />

        <!-- 自选股票池 -->
        <StockPoolPanel v-if="stocks.mode === 'spot'" @open-chart="showChart" />

        <!-- 主表: 按模式显示 -->
        <template v-if="stocks.mode === 'spot'">
          <div v-if="!stocks.isSpotCached" class="stock-table-container">
            <div class="loading-placeholder"><div class="spinner"></div><div>后台正在计算选股中...</div></div>
          </div>
          <StockTable v-else :stocks="stocks.spotStocks" mode="spot" :bid-seal-map="bidSealMap" />
        </template>
        <template v-else>
          <div v-if="!stocks.isDataCached" class="stock-table-container">
            <div class="loading-placeholder"><div class="spinner"></div><div>后台正在计算选股中...</div></div>
          </div>
          <StockTable v-else :stocks="stocks.cachedStocks" mode="auction" :bid-seal-map="bidSealMap" />
        </template>
        </template>
      </div><!-- /.home-col-left -->

      <!-- 右栏: 竞价异动 -->
      <div class="home-col home-col-right" :class="{ 'home-col-hidden': mobilePane !== 'auction' }">
        <AuctionView/>
      </div>
    </div><!-- /.home-grid -->

    <!-- 股票图表弹窗(分时/日K/周K/月K) -->
    <StockChartModal
      v-if="chartCode"
      v-model:visible="chartVisible"
      :code="chartCode"
      :name="chartName"
    />
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import FilterPanel from '../components/FilterPanel.vue'
import SentimentPanel from '../components/SentimentPanel.vue'
import MedalPanel from '../components/MedalPanel.vue'
import StockPoolPanel from '../components/StockPoolPanel.vue'
import StockTable from '../components/StockTable.vue'
import StockChartModal from '../components/StockChartModal.vue'
import AuctionView from './AuctionView.vue'
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
const bidSealMap = ref({})        // 竞价涨停委买额 map: code -> {limitBoards, bidSealAmt, bidNetAmt}

// 窄屏切换: 选股 / 竞价异动
const mobilePane = ref('stock')

// 图表弹窗控制
const chartVisible = ref(false)
const chartCode = ref('')
const chartName = ref('')

// 全局点击事件委托: 点击股票代码/名称单元格打开图(生产机还原版)
function onDocClick(ev) {
  // 点击在按钮/链接/设置了 data-no-chart 交互区内不触发
  if (ev.target.closest('button, a, [data-no-chart], [role="button"]')) return
  const cell = ev.target.closest('.stock-info-cell, .st-row-cell-code, .st-cell-code, [data-stock-code]')
  if (!cell) return
  let code = cell.getAttribute('data-stock-code') || ''
  let name = cell.getAttribute('data-stock-name') || ''
  if (!code) {
    const c = cell.querySelector('.stock-code'); c && (code = c.textContent.trim())
    const nm = cell.querySelector('.stock-name'); nm && (name = nm.textContent.trim())
  }
  if (code) {
    chartCode.value = code
    chartName.value = name
    chartVisible.value = true
    ev.stopPropagation()
  }
}

function closeChart() {
  chartVisible.value = false
}

async function loadBidSeal() {
  try {
    const d = await kplBidSeal()
    const map = {}
    ;(d.list || []).forEach((it) => { map[it.code] = it })
    bidSealMap.value = map
  } catch (e) { /* 竞价委买额可选, 失败静默 */ }
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
  loadBidSeal()
  document.addEventListener('click', onDocClick, true)
})
onBeforeUnmount(() => {
  if (clockTimer) clearInterval(clockTimer)
  if (autoAddTimer) clearInterval(autoAddTimer)
  if (expiryTimer) clearInterval(expiryTimer)
  document.removeEventListener('click', onDocClick, true)
})
</script>

<style scoped>
/* 奖牌(金银铜) + 自选股票池 在左栏内: 保留原有紧凑处理 */
.medal-section {
  display: flex;
  flex-wrap: nowrap;
  gap: 8px;
  justify-content: center;
  align-items: stretch;
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

/* 紧凑规则条: 规则文本 + 内联模式切换 + 操作按钮 一行排布 */
.alert-rule.alert-rule-compact {
  padding: 4px 10px;
  margin: 4px 0 6px;
  gap: 8px;
  border-radius: 6px;
  border-left-width: 3px;
  flex-wrap: wrap;
}
.alert-rule.alert-rule-compact .rule-text { font-size: 12px; gap: 4px; flex: 0 1 auto; min-width: 0; }
.alert-rule.alert-rule-compact .rule-text strong { font-size: 12px; }
.alert-rule.alert-rule-compact .right-group {
  gap: 6px;
  flex: 0 1 auto;
  flex-wrap: wrap;
  display: inline-flex;
  align-items: center;
}
.mode-tabs.mode-tabs-inline {
  margin: 0;
  gap: 4px;
  flex: 0 1 auto;
  flex-wrap: wrap;
  display: inline-flex;
  align-items: center;
}
.mode-tab.mode-tab-compact {
  padding: 3px 10px;
  font-size: 12px;
  border-radius: 6px;
  gap: 4px;
  line-height: 1.2;
  font-weight: 500;
  box-shadow: none;
}
.mode-tab.mode-tab-compact:hover { transform: none; box-shadow: none; border-color: #ffb400; }
.mode-tab.mode-tab-compact.active {
  padding: 3px 10px;
  font-size: 12px;
  font-weight: 700;
  box-shadow: 0 2px 6px rgba(255,160,0,0.3);
  transform: none;
}
.mode-tab.mode-tab-compact.active:after { display: none; }
.alert-rule.alert-rule-compact .tdx-export-btn { padding: 3px 8px; font-size: 11.5px; gap: 3px; border-radius: 4px; font-weight: 500; }
.alert-rule.alert-rule-compact .tdx-export-btn:hover { transform: none; box-shadow: 0 2px 6px rgba(var(--accent-rgb),0.25); }
/* 左栏筛选面板紧凑 */
.home-col-left .filter-custom { padding: 5px 9px; margin: 4px 0; gap: 3px; border-radius: 7px; }
.home-col-left .filter-row-1 { gap: 5px; }
.home-col-left .filter-custom label,
.home-col-left .filter-row-2 .filter-cell { font-size: 11.5px; }
.home-col-left .filter-row-2 input[type=number] { font-size: 11px; }
.home-col-left .filter-apply { padding: 3px 8px; font-size: 11px; }
.home-col-left .filter-reset,
.home-col-left .filter-lock { padding: 3px 7px; font-size: 11px; }
/* 左栏奖牌区紧凑 */
.home-col-left .medal-section { padding: 5px; margin: 4px 0; gap: 8px; border-radius: 8px; }
.home-col-left .medal-card { padding: 8px 4px; gap: 4px; border-radius: 10px; }
.home-col-left .medal-rank { font-size: 12px; }
.home-col-left .medal-name-big { font-size: 13px; }
.home-col-left .medal-code { font-size: 11px; }
.home-col-left .medal-real-big { font-size: 30px; margin: 0; }
.home-col-left .medal-bid-sm { font-size: 12px; }
.home-col-left .medal-score-row { font-size: 12px; gap: 5px; }
.home-col-left .qc-badge { font-size: 11px; margin-left: 4px; }
@media (max-width: 1099px) {
  .alert-rule.alert-rule-compact { flex-wrap: wrap; gap: 6px; }
}
@media (max-width: 768px) {
  .alert-rule.alert-rule-compact { padding: 4px 6px !important; margin: 2px 0 4px !important; gap: 4px !important; row-gap: 4px !important; flex-wrap: wrap !important; }
  .alert-rule.alert-rule-compact .rule-text,
  .alert-rule.alert-rule-compact .rule-text strong { font-size: 11px !important; }
  .mode-tabs.mode-tabs-inline { gap: 3px !important; }
  .mode-tab.mode-tab-compact { padding: 4px 9px !important; font-size: 11.5px !important; min-height: 26px; }
  .alert-rule.alert-rule-compact .tdx-export-btn { padding: 4px 6px !important; font-size: 11px !important; min-height: 26px; }
}

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