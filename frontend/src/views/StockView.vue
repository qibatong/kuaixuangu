<template>
  <div>
    <!-- 规则条 + 顶栏按钮组 -->
    <div class="alert-rule">
      <div class="rule-text"><i class="fa fa-clock-o"></i> <strong>9:30前可重新选股 · 9:30后仅更新实时涨幅</strong></div>
      <span class="health-dot" :class="'health-' + (healthStatus || 'none')" :title="healthTip || healthText">{{ healthText }}</span>
      <div class="right-group">
        <div class="btn-group">
          <button class="tdx-export-btn reset-lock-btn" :disabled="!isBefore930()" @click="reLock"><i class="fa fa-refresh"></i> 重新锁定(9:30前可用)</button>
          <button class="tdx-export-btn real-time-btn" @click="refreshRealTime"><i class="fa fa-refresh"></i> 刷新实时涨幅</button>
          <router-link to="/history" class="tdx-export-btn" style="background:rgba(255,180,0,0.18);border:1px solid #ffb400;color:#ffe0a0;"><i class="fa fa-history"></i> 历史回看</router-link>
          <router-link to="/invite" class="tdx-export-btn" style="background:rgba(0,180,255,0.15);border:1px solid #00b4ff;color:#a0e0ff;"><i class="fa fa-share-alt"></i> 邀请</router-link>
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

    <!-- 筛选面板 -->
    <FilterPanel />

    <!-- 奖牌区 -->
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

    <!-- 策略股票池 -->
    <StockPoolPanel />

    <!-- 全部结果导出(上下) -->
    <div style="display:flex;justify-content:flex-end;margin:6px 0;">
      <button class="tdx-export-btn tdx-only" data-tip="💡 首次用：先下载并运行「通达信工具」，再在通达信『选项/工具』勾选『监控剪贴板』" @click="downloadAll"><i class="fa fa-download"></i> 下载全部筛选结果</button>
    </div>

    <!-- 主表 -->
    <div v-if="!stocks.isDataCached" class="stock-table-container">
      <div class="loading-placeholder"><div class="spinner"></div><div>正在初始化选股数据...</div></div>
    </div>
    <StockTable v-else :stocks="stocks.cachedStocks" />

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
  autoAddTimer = setInterval(() => pool.autoAdd(stocks.cachedStocks, stocks.isDataCached), 20000)
  // 数据源健康状态(每 5 分钟刷新)
  loadHealth()
  healthTimer = setInterval(loadHealth, 300000)
  expiryTimer = setInterval(() => pool.checkExpiry(), 30000)
  pool.autoAdd(stocks.cachedStocks, stocks.isDataCached)
}

function reLock() {
  stocks.reLockData().catch(e => showToast('❌ ' + e.message, 'error'))
}
function refreshRealTime() {
  stocks.updateRealTimeOnly().catch(e => showToast('❌ 更新失败：' + e.message, 'error'))
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
})
onBeforeUnmount(() => {
  if (clockTimer) clearInterval(clockTimer)
  if (autoAddTimer) clearInterval(autoAddTimer)
  if (expiryTimer) clearInterval(expiryTimer)
  if (healthTimer) clearInterval(healthTimer)
})
</script>
