<template>
  <div>
    <!-- 规则条 + 顶栏按钮组 -->
    <div class="alert-rule">
      <div class="rule-text"><i class="fa fa-clock-o"></i> <strong>9:30前可重新选股 · 9:30后仅更新实时涨幅</strong></div>
      <span class="health-dot" :class="'health-' + (healthStatus || 'none')" :title="healthTip || healthText">{{ healthText }}</span>
      <span class="yizi-card" :title="'近5日一字涨停趋势: ' + yiziTrend.map(d => d.date.slice(5) + ':' + d.yizi_count + '个').join('  ')">
        <i class="fa fa-fire" style="color:#ff5028;"></i>
        <template v-if="!yiziLoaded">一字统计加载中...</template>
        <template v-else-if="yiziToday">一字 <b>{{ yiziToday.yizi_count }}</b> 个 · 竞价 <b>{{ yiziAmtText(yiziToday.bid_amt) }}</b></template>
        <template v-else>今日暂无一字涨停记录</template>
      </span>
      <div class="right-group">
        <div class="btn-group">
          <button class="tdx-export-btn reset-lock-btn" :disabled="!isBefore930()" @click="reLock"><i class="fa fa-refresh"></i> 重新锁定(9:30前可用)</button>
          <button class="tdx-export-btn real-time-btn" @click="refreshRealTime"><i class="fa fa-refresh"></i> 刷新实时涨幅</button>
          <router-link to="/history" class="tdx-export-btn" style="background:rgba(255,180,0,0.18);border:1px solid #ffb400;color:#ffe0a0;"><i class="fa fa-history"></i> 历史回看</router-link>
          <router-link to="/invite" class="tdx-export-btn" style="background:rgba(0,180,255,0.15);border:1px solid #00b4ff;color:#a0e0ff;"><i class="fa fa-share-alt"></i> 邀请</router-link>
          <router-link v-if="user.isAdmin" to="/admin" class="tdx-export-btn" style="background:rgba(255,215,0,0.15);border:1px solid #ffd700;color:#ffe9a0;"><i class="fa fa-shield"></i> 管理</router-link>
          <a href="/download/tdx_import.exe" class="tdx-export-btn tdx-only" style="background:rgba(255,150,50,0.15);border:1px solid #ff9632;color:#ffd0a0;"><i class="fa fa-windows"></i> 下载通达信工具</a>
          <button class="tdx-export-btn tdx-only" style="background:rgba(120,200,80,0.15);border:1px solid #78c850;color:#c0e8a0;" data-tip="💡 首次用：先点「下载通达信工具」并运行，再在通达信『选项/工具』勾选『监控剪贴板』，之后点下载即可自动导入" @click="downloadAll"><i class="fa fa-download"></i> 下载自选股(自动导入)</button>
        </div>
        <div id="mobileTdxHint">
          📱 通达信导入需在<b>电脑端</b>操作（电脑上点「下载自选股」即可自动导入）。手机上可点此
          <button class="pool-btn" @click="copyCodes"><i class="fa fa-copy"></i> 复制代码列表</button>
        </div>
        <div class="user-box">
          <i class="fa fa-user-circle"></i> <span>{{ user.username || '未登录' }}</span>
          <button class="logout-btn" title="修改密码" @click="changePwdModal.open()">改密</button>
          <button class="logout-btn" title="退出当前账号" @click="logout">退出</button>
        </div>
        <div class="live-time"><div class="time-digital">{{ bjTime }}</div></div>
      </div>
    </div>

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
      <MedalPanel :stocks="stocks.cachedStocks" />

      <!-- 奖牌导出 -->
      <div style="display:flex;justify-content:flex-end;margin:6px 0;">
        <div class="export-medal-group">
          <span style="color:#ffbcbc;font-size:12px;">导出前</span>
          <select v-model="medalExportCount" class="export-select">
            <option :value="3">3只</option><option :value="5">5只</option><option :value="8">8只</option><option :value="10">10只</option>
          </select>
          <button class="tdx-export-btn tdx-only" data-tip="💡 首次用：先下载并运行「通达信工具」，再在通达信『选项/工具』勾选『监控剪贴板』" @click="downloadMedal"><i class="fa fa-download"></i> 下载自选股</button>
        </div>
      </div>
    </template>

    <!-- 策略股票池 -->
    <StockPoolPanel />

    <!-- 全部结果导出(上下) -->
    <div style="display:flex;justify-content:flex-end;margin:6px 0;">
      <button class="tdx-export-btn tdx-only" data-tip="💡 首次用：先下载并运行「通达信工具」，再在通达信『选项/工具』勾选『监控剪贴板』" @click="downloadAll"><i class="fa fa-download"></i> 下载全部筛选结果</button>
    </div>

    <!-- 主表: 按模式显示 -->
    <template v-if="stocks.mode === 'spot'">
      <div v-if="!stocks.isSpotCached" class="stock-table-container">
        <div class="loading-placeholder"><div class="spinner"></div><div>正在初始化盘中数据...</div></div>
      </div>
      <StockTable v-else :stocks="stocks.spotStocks" mode="spot" />
    </template>
    <template v-else>
      <div v-if="!stocks.isDataCached" class="stock-table-container">
        <div class="loading-placeholder"><div class="spinner"></div><div>正在初始化选股数据...</div></div>
      </div>
      <StockTable v-else :stocks="stocks.cachedStocks" mode="auction" />
    </template>

    <div style="display:flex;justify-content:flex-end;margin:6px 0;">
      <button class="tdx-export-btn tdx-only" data-tip="💡 首次用：先下载并运行「通达信工具」，再在通达信『选项/工具』勾选『监控剪贴板』" @click="downloadAll"><i class="fa fa-download"></i> 下载全部筛选结果</button>
    </div>

    <ChangePwdModal ref="changePwdModal" />
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import FilterPanel from '../components/FilterPanel.vue'
import MedalPanel from '../components/MedalPanel.vue'
import StockPoolPanel from '../components/StockPoolPanel.vue'
import StockTable from '../components/StockTable.vue'
import ChangePwdModal from '../components/ChangePwdModal.vue'
import { useStocksStore } from '../stores/stocks'
import { usePoolStore } from '../stores/pool'
import { useUserStore } from '../stores/user'
import { showToast } from '../utils/toast'
import { copyText, downloadBlkFile } from '../utils/tdx'
import { bjTimeStr, isBefore930 } from '../utils/time'

const router = useRouter()
const stocks = useStocksStore()
const pool = usePoolStore()
const user = useUserStore()
const changePwdModal = ref(null)
const medalExportCount = ref(3)
const bjTime = ref('--:--:--')
const healthStatus = ref('')      // ok / degraded / down / ''
const healthText = ref('数据源检查中...')
const healthTip = ref('')
const yiziToday = ref(null)       // {yizi_count, bid_amt} 今日一字涨停
const yiziTrend = ref([])         // 近 5 日趋势
const yiziLoaded = ref(false)     // 接口已返回(区分 加载中/暂无)

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

function yiziAmtText(amt) {
  if (amt === null || amt === undefined) return '-'
  return (amt / 10000).toFixed(1) + '亿'
}

async function loadHealth() {
  try {
    const resp = await fetch('/api/health', { headers: { 'Authorization': 'Bearer ' + user.apiToken } })
    const data = await resp.json()
    if (data.ok) {
      healthStatus.value = data.overall || ''
      const s = data.sources || {}
      const parts = []
      for (const [k, v] of Object.entries(s)) {
        const names = { eastmoney_clist: '东财行情', eastmoney_kline: '东财日K', ths_kline: '同花顺' }
        parts.push(`${names[k] || k}:${v.status === 'ok' ? '正常' : v.status === 'degraded' ? '降级' : '异常'}(成功${v.ok}/失败${v.fail})`)
      }
      healthTip.value = parts.join('；')
      healthText.value = data.overall === 'ok' ? '数据源正常'
        : data.overall === 'degraded' ? '数据源降级'
        : data.overall === 'down' ? '数据源异常' : '数据源检查中...'
    }
  } catch (e) { /* 静默: 不影响主流程 */ }
}

let clockTimer = null
let autoAddTimer = null
let expiryTimer = null
let healthTimer = null

async function init() {
  user.migrateLegacyKeys()
  // 初始化股票池
  pool.loadFromStorage()
  // 初始化筛选状态(本地锁定 > 账号偏好 > 默认)
  await stocks.loadUserPrefs()
  stocks.initFilterFromStorage()
  // 首次拉数据
  try {
    await stocks.fetchAndCache()
  } catch (e) {
    showToast('❌ ' + e.message, 'error')
  }
  // 启动定时器: 时钟 / 自动收录 / 过期检查
  clockTimer = setInterval(() => { bjTime.value = bjTimeStr() }, 1000)
  autoAddTimer = setInterval(() => pool.autoAdd(currentList(), stocks.isDataCached || stocks.isSpotCached), 20000)
  // 数据源健康状态(每 5 分钟刷新)
  loadHealth()
  healthTimer = setInterval(loadHealth, 300000)
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
function downloadMedal() { downloadBlkFile(stocks.cachedStocks, medalExportCount.value) }
function downloadAll() { downloadBlkFile(stocks.cachedStocks, 0) }
function copyCodes() {
  if (!stocks.cachedStocks.length) { showToast('无数据', 'error'); return }
  copyText(stocks.cachedStocks.map(s => s.code).join('\n'), `✅ 已复制 ${stocks.cachedStocks.length} 个代码，可粘贴到电脑端导入`)
}
function logout() {
  user.clearSession()
  router.replace('/login')
}

onMounted(() => {
  bjTime.value = bjTimeStr()
  init()
  loadYizi()
})
onBeforeUnmount(() => {
  if (clockTimer) clearInterval(clockTimer)
  if (autoAddTimer) clearInterval(autoAddTimer)
  if (expiryTimer) clearInterval(expiryTimer)
  if (healthTimer) clearInterval(healthTimer)
})
</script>

<style scoped>
.yizi-card {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-left: 10px;
  padding: 3px 10px;
  border: 1px solid rgba(255, 80, 40, 0.4);
  border-radius: 12px;
  background: rgba(255, 80, 40, 0.08);
  color: #ffbc9a;
  font-size: 12px;
  white-space: nowrap;
  cursor: default;
}
.yizi-card b { color: #ff6a4a; }
.mode-tabs {
  display: flex;
  gap: 10px;
  margin: 10px 0 4px;
}
.mode-tab {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
  background: rgba(255,255,255,0.05);
  border: 1px solid rgba(255,255,255,0.15);
  color: #bbb;
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
  color: #888;
}
.mode-tab.active .mode-desc { color: #c9a94a; }
</style>
