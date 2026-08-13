<template>
  <div class="page-shell">
    <div class="page-back" @click="$router.push('/')"><i class="fa fa-arrow-left"></i> 返回选股</div>

    <div class="auc-head">
      <span class="auc-title"><i class="fa fa-bullhorn"></i> 竞价异动</span>
      <span class="auc-sub">多时点对比 · 竞价委买/爆量/净额/上榜/炸板（开盘啦 + 东财）</span>
      <span class="auc-time">{{ bjTime }}</span>
    </div>

    <!-- 顶部多时点对比(最近4个交易日) -->
    <div class="ov-panel">
      <table class="ov-table">
        <thead>
          <tr>
            <th class="ov-dim">时点</th>
            <th v-for="d in days" :key="d.date" class="ov-day">
              <div class="ov-date">{{ d.date.slice(5) }}</div>
              <div v-if="d.yizi_count !== null" class="ov-yizi">一字 <b>{{ d.yizi_count }}</b> 个 · 封单 <b>{{ yi(d.yizi_amt) }}亿</b></div>
              <div v-else class="ov-yizi dim">无数据</div>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="tp in timePoints" :key="tp.key">
            <td class="ov-dim">{{ tp.label }}</td>
            <td v-for="d in days" :key="d.date + tp.key" class="ov-cell">
              <template v-if="d.points[tp.key]">
                <div :class="d.points[tp.key].avg_change !== null && d.points[tp.key].avg_change >= 0 ? 'up' : 'down'">
                  {{ fmtAvg(d.points[tp.key].avg_change) }}
                </div>
                <div class="ov-amt dim">{{ yi(d.points[tp.key].total_amt) }}亿</div>
              </template>
              <span v-else class="dim">-</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Tab 切换 -->
    <div class="auc-tabs">
      <button class="auc-tab" :class="{ active: tab === 'seal' }" @click="tab = 'seal'"><i class="fa fa-gavel"></i> 竞价委买</button>
      <button class="auc-tab" :class="{ active: tab === 'boom' }" @click="tab = 'boom'"><i class="fa fa-bolt"></i> 竞价爆量</button>
      <button class="auc-tab" :class="{ active: tab === 'net' }" @click="tab = 'net'"><i class="fa fa-exchange"></i> 竞价净额</button>
      <button class="auc-tab" :class="{ active: tab === 'lhb' }" @click="tab = 'lhb'"><i class="fa fa-list-alt"></i> 昨上榜</button>
      <button class="auc-tab" :class="{ active: tab === 'broken' }" @click="tab = 'broken'"><i class="fa fa-chain-broken"></i> 炸板</button>
    </div>

    <div class="auc-panel">
      <div v-if="loading" class="loading-placeholder"><div class="spinner"></div><div>加载中...</div></div>

      <!-- 竞价委买/爆量/净额 共用表 -->
      <table v-else-if="tab !== 'lhb' && tab !== 'broken'" class="stock-table">
        <thead>
          <tr>
            <th>排名</th><th>代码</th><th>名称</th><th>实时涨幅</th><th>竞价涨幅</th>
            <th>涨停委买额(亿)</th><th>竞价净额(亿)</th><th>连板</th><th>板块</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(it, idx) in sealList" :key="it.code">
            <td class="rank-col">{{ idx + 1 }}</td>
            <td class="code-click" @click="linkToSoftware(it.code)">{{ it.code }}</td>
            <td class="name-col"><div class="name-main">{{ it.name }}</div></td>
            <td :class="it.realChange > 0 ? 'up' : 'down'">{{ signed(it.realChange) }}%</td>
            <td :class="it.bidChange > 0 ? 'up' : 'down'">{{ signed(it.bidChange) }}%</td>
            <td :class="it.bidSealAmt > 0 ? 'up' : 'dim'">{{ yi(it.bidSealAmt) }}</td>
            <td :class="it.bidNetAmt > 0 ? 'up' : it.bidNetAmt < 0 ? 'down' : 'dim'">{{ yi(it.bidNetAmt) }}</td>
            <td><span v-if="it.limitBoards > 0" class="lb-badge">{{ it.limitBoards }}板</span><span v-else class="dim">-</span></td>
            <td class="dim" style="max-width:150px;white-space:pre-wrap;">{{ it.board }}</td>
            <td><button class="pool-add-btn" @click="addToPool(it)">＋池</button></td>
          </tr>
        </tbody>
      </table>

      <!-- 昨上榜(龙虎榜) -->
      <table v-else-if="tab === 'lhb'" class="stock-table">
        <thead>
          <tr><th>代码</th><th>名称</th><th>涨幅%</th><th>连板</th><th>买入(亿)</th><th>成交额(亿)</th><th>换手%</th><th>振幅%</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="l in lhbList" :key="l.code">
            <td class="code-click" @click="linkToSoftware(l.code)">{{ l.code }}</td>
            <td class="name-col"><div class="name-main">{{ l.name }}</div></td>
            <td :class="l.change > 0 ? 'up' : 'down'">{{ signed(l.change) }}%</td>
            <td><span v-if="l.limitBoards > 0" class="lb-badge">{{ l.limitBoards }}板</span><span v-else class="dim">-</span></td>
            <td :class="l.buyIn > 0 ? 'up' : 'dim'">{{ yi(l.buyIn) }}</td>
            <td>{{ yi(l.amount) }}</td>
            <td>{{ l.turnover.toFixed(2) }}</td>
            <td>{{ l.amplitude.toFixed(2) }}</td>
            <td><button class="pool-add-btn" @click="addToPool(l)">＋池</button></td>
          </tr>
        </tbody>
      </table>

      <!-- 炸板 -->
      <table v-else class="stock-table">
        <thead>
          <tr><th>代码</th><th>名称</th><th>涨幅%</th><th>连板</th><th>炸板次数</th><th>涨停时间</th><th>炸板时间</th><th>涨停原因</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="b in brokenList" :key="b.code">
            <td class="code-click" @click="linkToSoftware(b.code)">{{ b.code }}</td>
            <td class="name-col"><div class="name-main">{{ b.name }}</div></td>
            <td :class="b.change > 0 ? 'up' : 'down'">{{ signed(b.change) }}%</td>
            <td><span v-if="b.limitUpDays > 0" class="lb-badge">{{ b.limitUpDays }}板</span><span v-else class="dim">-</span></td>
            <td><span v-if="b.breakTimes > 1" class="bk-hot">{{ b.breakTimes }}次</span><span v-else>{{ b.breakTimes }}</span></td>
            <td class="dim">{{ fmtT(b.firstLimitUp) }}</td>
            <td class="dim">{{ fmtT(b.firstBreak) }}</td>
            <td class="dim" style="max-width:220px;white-space:pre-wrap;font-size:12px;">{{ b.reason || '-' }}</td>
            <td><button class="pool-add-btn" @click="addToPool(b)">＋池</button></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { kplBidSeal, kplBidBoom, kplBroken, kplLhb } from '../api/kpl'
import { auctionOverview } from '../api/stats'
import { linkToSoftware } from '../utils/tdx'
import { bjTimeStr } from '../utils/time'
import { usePoolStore } from '../stores/pool'
import { showToast } from '../utils/toast'

const pool = usePoolStore()
const tab = ref('seal')
const days = ref([])
const sealRaw = ref([])
const boomList = ref([])
const lhbList = ref([])
const brokenList = ref([])
const loading = ref(true)
const bjTime = ref('--:--:--')
let clockTimer = null
let refreshTimer = null

const timePoints = [
  { key: '9_15', label: '9:15 竞价' },
  { key: '9_20', label: '9:20 竞价' },
  { key: '9_25', label: '9:25 竞价' }
]

// 竞价委买/爆量/净额共用表格: 数据源按 Tab 切换
const sealList = computed(() => {
  if (tab.value === 'boom') return boomList.value
  if (tab.value === 'net') return [...sealRaw.value].sort((a, b) => (b.bidNetAmt || 0) - (a.bidNetAmt || 0))
  return sealRaw.value
})

function yi(v) { return (v / 1e8).toFixed(2) }
function signed(v) { return (v > 0 ? '+' : '') + Number(v).toFixed(2) }
function fmtAvg(v) { return v === null || v === undefined ? '-' : (v > 0 ? '+' : '') + v.toFixed(2) + '%' }
function fmtT(ts) {
  if (!ts) return '-'
  const d = new Date((ts + 8 * 3600) * 1000)
  return d.toISOString().slice(11, 16)
}

function addToPool(s) {
  const n = pool.addStocks([{ code: s.code, name: s.name }])
  showToast(n ? `✅ ${s.code} ${s.name} 已加入股票池` : `${s.code} 已在池中`, n ? 'success' : 'info')
}

async function loadAll() {
  try {
    const [ov, seal, boom, lhb, broken] = await Promise.all([
      auctionOverview(), kplBidSeal(), kplBidBoom(), kplLhb(), kplBroken()
    ])
    days.value = ov.days || []
    sealRaw.value = seal.list || []
    boomList.value = boom.list || []
    lhbList.value = lhb.list || []
    brokenList.value = broken.list || []
  } catch (e) { /* 静默 */ } finally {
    loading.value = false
  }
}

onMounted(() => {
  bjTime.value = bjTimeStr()
  clockTimer = setInterval(() => { bjTime.value = bjTimeStr() }, 1000)
  loadAll()
  refreshTimer = setInterval(loadAll, 60000)
})
onBeforeUnmount(() => {
  if (clockTimer) clearInterval(clockTimer)
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<style scoped>
.page-shell { max-width: 1500px; margin: 0 auto; padding: 16px; }
.page-back { color: #9aa; cursor: pointer; font-size: 13px; margin-bottom: 12px; display: inline-block; }
.page-back:hover { color: #ffb400; }
.auc-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.auc-title { font-size: 20px; font-weight: 700; color: #ffe0a0; }
.auc-title .fa { color: #ffb400; }
.auc-sub { color: #999; font-size: 13px; }
.auc-time { margin-left: auto; color: #aaa; font-size: 14px; font-family: monospace; }
.ov-panel { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.1); border-radius: 10px; padding: 12px 14px; margin-bottom: 14px; }
.ov-table { width: 100%; border-collapse: collapse; }
.ov-table th, .ov-table td { padding: 8px 10px; text-align: center; border-bottom: 1px solid rgba(255,255,255,0.06); }
.ov-dim { color: #999; font-size: 12px; text-align: left; width: 90px; }
.ov-day { color: #ffe0a0; font-size: 13px; }
.ov-date { font-size: 14px; font-weight: 700; }
.ov-yizi { font-size: 12px; color: #ffb400; margin-top: 2px; }
.ov-yizi b { color: #ff6a6a; }
.ov-cell { font-size: 14px; font-weight: 600; }
.ov-amt { font-size: 11px; font-weight: 400; }
.auc-tabs { display: flex; gap: 8px; margin-bottom: 14px; flex-wrap: wrap; }
.auc-tab {
  padding: 8px 16px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.15);
  background: rgba(255,255,255,0.04); color: #bbb; font-size: 14px; cursor: pointer; transition: all 0.2s;
}
.auc-tab:hover { border-color: #ffb400; color: #ffe0a0; }
.auc-tab.active { background: rgba(255,180,0,0.15); border-color: #ffb400; color: #ffd700; font-weight: 600; }
.auc-panel { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.1); border-radius: 10px; padding: 14px; }
.loading-placeholder { text-align: center; padding: 40px; color: #888; }
.spinner { width: 28px; height: 28px; border: 3px solid rgba(255,180,0,0.3); border-top-color: #ffb400; border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 10px; }
@keyframes spin { to { transform: rotate(360deg); } }
.lb-badge { display: inline-block; color: #ff8a5c; border: 1px solid rgba(255,80,40,0.5); border-radius: 4px; padding: 0 5px; font-size: 11px; background: rgba(255,80,40,0.12); }
.bk-hot { color: #ff5028; font-weight: 700; }
</style>
