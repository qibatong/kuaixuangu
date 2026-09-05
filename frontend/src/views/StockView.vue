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
        <!-- 模式切换(内联): 竞价 / AI预测 (2026-09-01: 原"盘中"替换为 AI预测)
             2026-09-05 主人需求: ① 去掉原顶部提示文案(竞价「9:30前可重新选股…」与
             AI预测「AI 竞价预测…」两行) ② 原右上角「锁定」按钮移除(下方 FilterPanel
             已有同功能锁定) ③ 「刷新」按钮下移至 FilterPanel 的 应用/重置/锁定 组 -->
        <div class="alert-rule alert-rule-compact">
          <span class="mode-tabs mode-tabs-inline">
            <button class="mode-tab mode-tab-compact" :class="{ active: leftTab === 'auction' }" @click="switchTab('auction')"><i class="fa fa-sun-o"></i> 竞价</button>
            <button class="mode-tab mode-tab-compact" :class="{ active: leftTab === 'aipick' }" @click="switchTab('aipick')"><i class="fa fa-android"></i> AI预测</button>
          </span>
        </div>

        <!-- 筛选面板(仅竞价模式) -->
        <!-- 2026-09-05: 刷新按钮已下移到 FilterPanel(与 应用/重置/锁定 同组),
             这里监听其 emit 并执行本视图的刷新逻辑 -->
        <div v-if="leftTab === 'auction'" class="home-filter"><FilterPanel @refresh="refreshRealTime" /></div>

        <template v-if="leftTab === 'auction'">
          <!-- 会员门禁: 竞价选股 仅在工作日 9:15-15:00 要求会员; 其他时段放开 -->
          <VipGate v-if="!user.isMember && isMemberOnlyTime()" title="竞价选股" />
          <template v-if="user.isMember || !isMemberOnlyTime()">
            <!-- 奖牌区 -->
            <MedalPanel :stocks="stocks.cachedStocks" />
            <!-- 主表 -->
            <div v-if="!stocks.isDataCached" class="stock-table-container">
              <div class="loading-placeholder"><div class="spinner"></div><div>后台正在计算选股中...</div></div>
            </div>
            <StockTable v-else :stocks="stocks.cachedStocks" mode="auction" :bid-seal-map="bidSealMap" />
          </template>
        </template>

        <!-- AI预测(2026-09-01 替换原盘中选股; 自带 VIP 门禁/日期回看/规则过滤) -->
        <!-- 2026-09-01: AI预测内嵌左视图; 隐藏日期回看(回看入口在导航栏「历史回看」页) -->
        <AipickView v-else :embedded="true" :show-date-picker="false" />
      </div><!-- /.home-col-left -->

      <!-- 右栏: 竞价异动 -->
      <div class="home-col home-col-right" :class="{ 'home-col-hidden': mobilePane !== 'auction' }">
        <AuctionView/>
      </div>
    </div><!-- /.home-grid -->
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import FilterPanel from '../components/FilterPanel.vue'
import SentimentPanel from '../components/SentimentPanel.vue'
import MedalPanel from '../components/MedalPanel.vue'
import StockTable from '../components/StockTable.vue'
import AuctionView from './AuctionView.vue'
import AipickView from './AipickView.vue'
import VipGate from '../components/VipGate.vue'
import { useStocksStore } from '../stores/stocks'
import { usePoolStore } from '../stores/pool'
import { useUserStore } from '../stores/user'
import { kplBidSeal } from '../api/kpl'
import { showToast } from '../utils/toast'
import { useYidongMonitor } from '../composables/useYidongMonitor'
import { copyText, downloadBlkFile } from '../utils/tdx'
// 2026-09-05: isBefore930 随「锁定」按钮移除后本视图不再使用, 从 import 中去掉
import { bjDateTimeStr, isIntradayNow, isMemberOnlyTime } from '../utils/time'

const stocks = useStocksStore()
const pool = usePoolStore()
const user = useUserStore()
const bjTime = ref('--:--:--')
const bidSealMap = ref({})        // 竞价涨停委买额 map: code -> {limitBoards, bidSealAmt, bidNetAmt}
const { refreshYidongCodes } = useYidongMonitor()

// 2026-09-01: 左视图模式切换 竞价 / AI预测(原"盘中"已被 AI预测替换)
// 使用本地 leftTab 而非 stocks.mode: 不再触发盘中数据流(fetchSpot/spotStocks)
const leftTab = ref('auction')

// 窄屏切换: 选股 / 竞价异动
const mobilePane = ref('stock')

// 图表弹窗: 由 App.vue 全局托管(uiBus.chartModal 驱动); 左视图已无自选池入口,
// 股票点击图表由 StockTable 内部通过 uiBus 触发
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
let realTimeTimer = null

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
  autoAddTimer = setInterval(() => pool.autoAdd(currentList(), stocks.isDataCached), 20000)
  expiryTimer = setInterval(() => pool.checkExpiry(), 30000)
  pool.autoAdd(currentList(), stocks.isDataCached)
  // 盘中 9:30-15:00: 每 30s 自动刷新一次现涨/实时涨幅(静默, 不弹 toast)。
  // 后端行情缓存 TTL: 竞价30s, 30s 轮询既能跟上涨幅变化又不超压。
  realTimeTimer = setInterval(() => {
    if (!isIntradayNow()) return            // 盘前/收盘/周末: 不轮询, 现涨固定为当日收盘
    const safe = (p) => p.catch(() => {})  // 轮询失败静默, 不打断
    safe(stocks.updateRealTimeOnly({ silent: true }))
  }, 30000)
}

// 当前模式的选股结果(左视图仅竞价模式, 恒为 cachedStocks)
function currentList() {
  return stocks.cachedStocks
}

// 2026-09-05: 原 reLock() 随右上角「锁定」按钮一并移除(下方 FilterPanel 已提供
// 锁定/解锁, 且 store.reLockData() 保留供其使用), 此处不再需要包装函数。
function refreshRealTime() {
  stocks.updateRealTimeOnly().catch(e => showToast('❌ 更新失败：' + e.message, 'error'))
}
// 左视图模式切换: auction(竞价, 数据流与 store 联动) / aipick(AI预测, AipickView 自加载)
function switchTab(m) {
  if (leftTab.value === m) return
  leftTab.value = m
  if (m === 'auction' && !stocks.isDataCached) {
    stocks.fetchAndCache().catch(e => showToast('❌ ' + e.message, 'error'))
  }
}
function downloadAll() { downloadBlkFile(stocks.cachedStocks, 0) }
function copyCodes() {
  if (!stocks.cachedStocks.length) { showToast('无数据', 'error'); return }
  copyText(stocks.cachedStocks.map(s => s.code).join('\n'), `✅ 已复制 ${stocks.cachedStocks.length} 个代码，可粘贴到电脑端导入`)
}

// 2026-08-24: 首页表格固定表头 — 测量 sticky 元素(filter/tabs)高度写入 CSS 变量
let _stickyResizeFn = null
function setupStickyOffsets() {
  const update = () => {
    const leftCol = document.querySelector('.home-col-left')
    const rightCol = document.querySelector('.home-col-right')
    if (leftCol) {
      const f = leftCol.querySelector('.home-filter')
      if (f) leftCol.style.setProperty('--sticky-thead-top', f.offsetHeight + 'px')
    }
    if (rightCol) {
      const t = rightCol.querySelector('.auc-tabs')
      if (t) rightCol.style.setProperty('--sticky-thead-top', t.offsetHeight + 'px')
    }
  }
  setTimeout(update, 200)
  _stickyResizeFn = update
  window.addEventListener('resize', _stickyResizeFn)
}

onMounted(() => {
  bjTime.value = bjDateTimeStr()
  init()
  loadBidSeal()
  refreshYidongCodes()   // 首页选股/竞价异动 标记异动监管股票
  setupStickyOffsets()
})
onBeforeUnmount(() => {
  if (clockTimer) clearInterval(clockTimer)
  if (autoAddTimer) clearInterval(autoAddTimer)
  if (expiryTimer) clearInterval(expiryTimer)
  if (realTimeTimer) clearInterval(realTimeTimer)
  if (_stickyResizeFn) window.removeEventListener('resize', _stickyResizeFn)
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
  /* 覆盖 main.css .alert-rule 的 space-between: 让 rule-text + mode-tabs 始终紧贴左侧,
     不再因 right-group 是否存在而在「居中/靠右」之间漂移 (2026-09-01 反馈) */
  justify-content: flex-start;
}
.alert-rule.alert-rule-compact .rule-text { font-size: 12px; gap: 4px; flex: 0 1 auto; min-width: 0; }
.alert-rule.alert-rule-compact .rule-text strong { font-size: 12px; }
.alert-rule.alert-rule-compact .right-group {
  gap: 6px;
  flex: 0 1 auto;
  flex-wrap: wrap;
  display: inline-flex;
  align-items: center;
  /* right-group 自身推到右侧; 缺失时不影响 rule-text + mode-tabs 紧贴布局 */
  margin-left: auto;
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
  /* 2026-09-05 主人需求: 竞价/AI预测 tab 适当放大(3px 10px → 6px 16px, 12→13px),
     active 态同尺寸避免切换跳动 */
  padding: 6px 16px;
  font-size: 13px;
  border-radius: 6px;
  gap: 4px;
  line-height: 1.2;
  font-weight: 500;
  box-shadow: none;
}
.mode-tab.mode-tab-compact:hover { transform: none; box-shadow: none; border-color: #ffb400; }
.mode-tab.mode-tab-compact.active {
  padding: 6px 16px;
  font-size: 13px;
  font-weight: 700;
  /* 选中态明显高亮: 实色填充 + 白底/文字对比, 深色默认主题 */
  color: #fff;
  background: linear-gradient(180deg, #ffb400, #f08c00);
  border-color: #ffb400;
  box-shadow: 0 2px 6px rgba(255,160,0,0.45);
  transform: none;
}
.mode-tab.mode-tab-compact.active:after { display: none; }
/* 浅色主题: 选中改用深色文字 + 浅橙底, 保持高对比这两态也能区分 */
body[data-bg="light"] .mode-tab.mode-tab-compact.active {
  color: #7a3d00;
  background: linear-gradient(180deg, #ffd571, #ffb84d);
  border-color: #f08c00;
}
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
/* 左栏奖牌区紧凑 (奖牌内容在子组件 MedalPanel 内, 需 :deep 才能命中) */
.home-col-left .medal-section { padding: 5px; margin: 4px 0; gap: 8px; border-radius: 8px; }
.home-col-left :deep(.medal-card) { padding: 6px 4px; gap: 3px; border-radius: 8px; }
.home-col-left :deep(.medal-rank) { font-size: 16px; }
.home-col-left :deep(.medal-name-big) { font-size: 18px; }
.home-col-left :deep(.medal-code) { font-size: 16px; }
.home-col-left :deep(.medal-real-big) { font-size: 40px; margin: 0; }
.home-col-left :deep(.medal-bid-sm) { font-size: 15px; }
.home-col-left :deep(.medal-score-row) { font-size: 15px; gap: 5px; }
.home-col-left :deep(.qc-badge) { font-size: 13px; top: 2px; right: 2px; }
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
body[data-bg="light"] .page-back {  color: #6b6b6b;  }
body[data-bg="light"] .page-back:hover {  color: #c79100;  }
body[data-bg="light"] .yizi-card {  color: #8a5500; background: rgba(255,150,50,0.12); border-color: rgba(255,150,50,0.5);  }
body[data-bg="light"] .yizi-card b {  color: #8a4a00;  }
body[data-bg="light"] .filter-tag {  color: #8a5500; background: rgba(255,80,80,0.12);  }
body[data-bg="light"] .filter-divider {  color: rgba(0,0,0,0.3);  }
</style>