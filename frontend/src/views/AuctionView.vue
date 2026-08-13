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
            <td v-for="d in days" :key="d.date + tp.key" class="ov-cell ov-click" title="点击查看该时点个股"
                @click="showSnapshot(d.date, tp.key)">
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
      <button class="auc-tab" :class="{ active: tab === 'qc' }" @click="tab = 'qc'" title="9:15-9:30 竞价抢筹(异动板块大单)"><i class="fa fa-fire"></i> 竞价抢筹</button>
      <button class="auc-tab" :class="{ active: tab === 'yestZt' }" @click="tab = 'yestZt'"><i class="fa fa-sun-o"></i> 昨日涨停</button>
      <button class="auc-tab" :class="{ active: tab === 'yestBroken' }" @click="tab = 'yestBroken'"><i class="fa fa-bell-slash"></i> 昨断板</button>
      <button class="auc-tab" :class="{ active: tab === 'lhb' }" @click="tab = 'lhb'"><i class="fa fa-list-alt"></i> 昨上榜</button>
      <button class="auc-tab" :class="{ active: tab === 'brokenYest' }" @click="tab = 'brokenYest'"><i class="fa fa-history"></i> 昨炸板</button>
      <button class="auc-tab" :class="{ active: tab === 'brokenToday' }" @click="tab = 'brokenToday'"><i class="fa fa-chain-broken"></i> 今炸板</button>
    </div>

    <div class="auc-panel">
      <div v-if="loading" class="loading-placeholder"><div class="spinner"></div><div>加载中...</div></div>

      <!-- 竞价委买/爆量/净额 共用表 -->
      <table v-else-if="tab === 'seal' || tab === 'boom' || tab === 'net'" class="stock-table">
        <thead>
          <tr>
            <th>排名</th><th>代码</th><th>名称</th><th>实时涨幅</th><th>竞价涨幅</th>
            <th>{{ tab === 'boom' ? '竞价成交额(亿)' : '涨停委买额(亿)' }}</th><th>竞价净额(亿)</th><th>连板</th><th>板块</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(it, idx) in sealList" :key="it.code">
            <td class="rank-col">{{ idx + 1 }}</td>
            <td class="code-click" @click="linkToSoftware(it.code)">{{ it.code }}</td>
            <td class="name-col"><div class="name-main">{{ it.name }}</div></td>
            <td :class="it.realChange > 0 ? 'up' : 'down'">{{ signed(it.realChange) }}%</td>
            <td :class="it.bidChange > 0 ? 'up' : 'down'">{{ signed(it.bidChange) }}%</td>
            <td v-if="tab === 'boom'" :class="it.bidAmt > 0 ? 'up' : 'dim'">{{ yi(it.bidAmt) }}</td>
            <td v-else :class="it.bidSealAmt > 0 ? 'up' : 'dim'">{{ yi(it.bidSealAmt) }}</td>
            <td :class="it.bidNetAmt > 0 ? 'up' : it.bidNetAmt < 0 ? 'down' : 'dim'">{{ yi(it.bidNetAmt) }}</td>
            <td><span v-if="it.limitBoards > 0" class="lb-badge">{{ it.limitBoards }}板</span><span v-else class="dim">-</span></td>
            <td class="dim" style="max-width:150px;white-space:pre-wrap;">{{ it.board }}</td>
            <td><button class="pool-add-btn" :class="{ added: inPool(it.code) }" @click.stop="addToPool(it)">{{ inPool(it.code) ? '已入池' : '＋池' }}</button></td>
          </tr>
        </tbody>
      </table>

      <!-- 竞价抢筹(双段: 9:20→9:25 + 9:24→9:25 最后阶段, 对标短线侠) -->
      <table v-else-if="tab === 'qc'" class="stock-table">
        <thead>
          <tr><th>排名</th><th>代码</th><th>名称</th><th>涨幅%</th><th>竞额</th><th>9:20-9:25抢筹%</th><th>9:24-9:25最后%</th><th>竞涨%</th><th>竞价换手</th><th>流通Z</th><th>概念</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="(q, idx) in qcList" :key="q.code">
            <td class="rank-col">{{ idx + 1 }}</td>
            <td class="code-click" @click="linkToSoftware(q.code)">{{ q.code }}</td>
            <td class="name-col"><div class="name-main">{{ q.name }}</div></td>
            <td :class="q.realChange > 0 ? 'up' : q.realChange < 0 ? 'down' : 'dim'">{{ q.realChange !== null && q.realChange !== undefined ? signed(q.realChange) + '%' : '-' }}</td>
            <td :class="q.bidAmt > 0 ? 'up' : 'dim'">{{ amtText(q.bidAmt) }}</td>
            <td :class="q.qc20 > 0 ? 'up' : q.qc20 < 0 ? 'down' : 'dim'"><b>{{ signed(q.qc20) }}%</b></td>
            <td :class="q.qcLast > 0 ? 'up' : q.qcLast < 0 ? 'down' : 'dim'">{{ q.qcLast !== null && q.qcLast !== undefined ? signed(q.qcLast) + '%' : '-' }}</td>
            <td :class="q.bidChange > 0 ? 'up' : q.bidChange < 0 ? 'down' : 'dim'">{{ q.bidChange !== null && q.bidChange !== undefined ? signed(q.bidChange) + '%' : '-' }}</td>
            <td>{{ q.bidTurnover ? q.bidTurnover.toFixed(2) : '-' }}</td>
            <td>{{ q.floatMv ? (q.floatMv / 1e8).toFixed(1) : '-' }}</td>
            <td class="dim" style="max-width:150px;white-space:pre-wrap;">{{ q.board || '-' }}</td>
            <td><button class="pool-add-btn" :class="{ added: inPool(q.code) }" @click.stop="addToPool(q)">{{ inPool(q.code) ? '已入池' : '＋池' }}</button></td>
          </tr>
          <tr v-if="!qcList.length">
            <td colspan="12" class="snap-empty">竞价抢筹数据 9:15-9:30 竞价时段可用（当前非竞价时段）</td>
          </tr>
        </tbody>
      </table>

      <!-- 昨日涨停(今日竞价表现) -->
      <table v-else-if="tab === 'yestZt'" class="stock-table">
        <thead>
          <tr><th>排名</th><th>代码</th><th>名称</th><th>连板</th><th>实时涨幅</th><th>竞价换手</th><th>竞价净额(亿)</th><th>竞额(亿)</th><th>概念</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="(z, idx) in yestZtList" :key="z.code">
            <td class="rank-col">{{ idx + 1 }}</td>
            <td class="code-click" @click="linkToSoftware(z.code)">{{ z.code }}</td>
            <td class="name-col"><div class="name-main">{{ z.name }}</div>
              <span v-if="z.stillLimit" class="lb-badge">连板</span></td>
            <td><span v-if="z.limitUpDays > 0" class="lb-badge">{{ z.limitUpDays }}板</span><span v-else class="dim">-</span></td>
            <td :class="z.change > 0 ? 'up' : z.change < 0 ? 'down' : 'dim'">{{ z.change !== null && z.change !== undefined ? signed(z.change) + '%' : '-' }}</td>
            <td>{{ z.bidTurnover ? z.bidTurnover.toFixed(2) : '-' }}</td>
            <td :class="z.bidNetAmt > 0 ? 'up' : z.bidNetAmt < 0 ? 'down' : 'dim'">{{ z.bidNetAmt ? amtText(z.bidNetAmt) : '-' }}</td>
            <td>{{ z.bidAmt ? amtText(z.bidAmt) : '-' }}</td>
            <td class="dim" style="max-width:150px;white-space:pre-wrap;">{{ z.board || '-' }}</td>
            <td><button class="pool-add-btn" :class="{ added: inPool(z.code) }" @click.stop="addToPool(z)">{{ inPool(z.code) ? '已入池' : '＋池' }}</button></td>
          </tr>
        </tbody>
      </table>

      <!-- 昨断板(昨涨停今断) -->
      <table v-else-if="tab === 'yestBroken'" class="stock-table">
        <thead>
          <tr><th>排名</th><th>代码</th><th>名称</th><th>昨涨幅</th><th>今日竞价涨幅</th><th>竞额(亿)</th><th>竞价换手</th><th>概念</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="(b2, idx) in yestBrokenList" :key="b2.code">
            <td class="rank-col">{{ idx + 1 }}</td>
            <td class="code-click" @click="linkToSoftware(b2.code)">{{ b2.code }}</td>
            <td class="name-col"><div class="name-main">{{ b2.name }}</div></td>
            <td :class="b2.yestChange > 0 ? 'up' : 'down'">{{ signed(b2.yestChange) }}%</td>
            <td :class="b2.bidChange > 0 ? 'up' : b2.bidChange < 0 ? 'down' : 'dim'">{{ b2.bidChange !== null && b2.bidChange !== undefined ? signed(b2.bidChange) + '%' : '-' }}</td>
            <td>{{ b2.bidAmt ? amtText(b2.bidAmt) : '-' }}</td>
            <td>{{ b2.bidTurnover ? b2.bidTurnover.toFixed(2) : '-' }}</td>
            <td class="dim" style="max-width:150px;white-space:pre-wrap;">{{ b2.board || '-' }}</td>
            <td><button class="pool-add-btn" :class="{ added: inPool(b2.code) }" @click.stop="addToPool(b2)">{{ inPool(b2.code) ? '已入池' : '＋池' }}</button></td>
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
            <td><button class="pool-add-btn" :class="{ added: inPool(l.code) }" @click.stop="addToPool(l)">{{ inPool(l.code) ? '已入池' : '＋池' }}</button></td>
          </tr>
        </tbody>
      </table>

      <!-- 炸板(昨/今) -->
      <table v-else-if="tab === 'brokenYest' || tab === 'brokenToday'" class="stock-table">
        <thead>
          <tr><th>代码</th><th>名称</th><th>涨幅%</th><th>连板</th><th>炸板次数</th><th>涨停时间</th><th>炸板时间</th><th>涨停原因</th><th>操作</th></tr>        </thead>
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
            <td><button class="pool-add-btn" :class="{ added: inPool(b.code) }" @click.stop="addToPool(b)">{{ inPool(b.code) ? '已入池' : '＋池' }}</button></td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 时点个股弹窗(点击多时点对比卡) -->
    <div v-if="snapModal.show" class="modal-mask" @click.self="snapModal.show = false">
      <div class="snap-modal">
        <div class="snap-head">
          <span class="snap-title">{{ snapModal.date }} {{ snapPointLabel }} · 竞价个股 <span class="dim">(点击格子查看, 共 {{ snapModal.list.length }} 只)</span></span>
          <span class="snap-close" @click="snapModal.show = false">✕</span>
        </div>
        <table v-if="snapModal.list.length" class="stock-table">
          <thead>
            <tr><th>#</th><th>代码</th><th>名称</th><th>竞价涨幅</th><th>竞价额(万)</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="(s, i) in snapModal.list" :key="s.code">
              <td class="rank-col">{{ i + 1 }}</td>
              <td class="code-click" @click="linkToSoftware(s.code)">{{ s.code }}</td>
              <td>{{ s.name || s.code }}</td>
              <td :class="s.bid_change > 0 ? 'up' : s.bid_change < 0 ? 'down' : 'dim'">{{ signed(s.bid_change) }}%</td>
              <td>{{ wan(s.bid_amt) }}</td>
              <td><button class="pool-add-btn" :class="{ added: inPool(s.code) }" @click.stop="addToPool(s)">{{ inPool(s.code) ? '已入池' : '＋池' }}</button></td>
            </tr>
          </tbody>
        </table>
        <div v-else class="snap-empty">该时点暂无数据</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { kplBidSeal, kplBidBoom, kplBidQiangcang, kplBroken, kplLhb, kplYestBroken, kplYestZt } from '../api/kpl'
import { auctionOverview, auctionSnapshot } from '../api/stats'
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
const brokenYestList = ref([])
const brokenTodayList = ref([])
const qcList = ref([])
const yestZtList = ref([])
const yestBrokenList = ref([])
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

// 炸板: 昨/今 按 Tab 切换
const brokenList = computed(() => (tab.value === 'brokenYest' ? brokenYestList.value : brokenTodayList.value))
const brokenTitle = computed(() => (tab.value === 'brokenYest' ? '昨炸板' : '今炸板'))

function yi(v) { return (v / 1e8).toFixed(2) }
function signed(v) { return (v > 0 ? '+' : '') + Number(v).toFixed(2) }
// 金额自适应: >=1亿 显示亿(2位), 否则显示万
function amtText(v) {
  if (!v || v <= 0) return '-'
  return v >= 1e8 ? (v / 1e8).toFixed(2) + '亿' : (v / 1e4).toFixed(0) + '万'
}
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

// 是否已在股票池(与主页面 StockTable 一致: 已入池按钮变绿禁用)
function inPool(code) {
  return pool.stockPool.some(x => x.code === code)
}

// ---- 时点个股弹窗 ----
const snapModal = ref({ show: false, date: '', tp: '', list: [] })
const snapPointLabel = computed(() => {
  const t = timePoints.find(x => x.key === snapModal.value.tp)
  return t ? t.label : snapModal.value.tp
})
async function showSnapshot(date, tp) {
  snapModal.value = { show: true, date, tp, list: [] }
  try {
    const d = await auctionSnapshot(date, tp)
    snapModal.value.list = d.list || []
  } catch (e) { /* 静默 */ }
}
function wan(v) { return v ? Number(v).toFixed(0) : '0' }

async function loadAll() {
  try {
    const [ov, seal, boom, qc, yestZt, yestBroken, lhb, brokenYest, brokenToday] = await Promise.all([
      auctionOverview(), kplBidSeal(), kplBidBoom(), kplBidQiangcang(), kplYestZt(), kplYestBroken(),
      kplLhb(), kplBroken('yesterday'), kplBroken()
    ])
    days.value = ov.days || []
    sealRaw.value = seal.list || []
    boomList.value = boom.list || []
    qcList.value = qc.list || []
    yestZtList.value = yestZt.list || []
    yestBrokenList.value = yestBroken.list || []
    lhbList.value = lhb.list || []
    brokenYestList.value = brokenYest.list || []
    brokenTodayList.value = brokenToday.list || []
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
.ov-click { cursor: pointer; }
.ov-click:hover { background: rgba(255,180,0,0.08); }

/* 时点个股弹窗(脱离 flex, 固定定位自居中, 不受 flex item 收缩影响) */
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 1000; }
.snap-modal { position: fixed; left: 50%; top: 50%; transform: translate(-50%, -50%); background: #1c1f26; border: 1px solid rgba(255,255,255,0.15); border-radius: 12px; width: min(1100px, 98vw); max-height: 85vh; overflow: auto; padding: 12px 14px; box-sizing: border-box; }
.snap-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.snap-title { font-size: 15px; font-weight: 700; color: #ffe0a0; }
.snap-close { cursor: pointer; color: #99a; font-size: 16px; padding: 2px 6px; }
.snap-close:hover { color: #ffb400; }
.snap-empty { text-align: center; color: #667; padding: 30px 0; font-size: 13px; }
/* 弹窗表格: 列间距紧凑, 6 列全部可见 */
.snap-modal .stock-table { width: 100%; }
.snap-modal .stock-table th, .snap-modal .stock-table td {
  padding: 3px 6px;
  font-size: 12px;
  white-space: nowrap;
}
.snap-modal .stock-table th:nth-child(1), .snap-modal .stock-table td:nth-child(1) { width: 28px; text-align: center; padding-left: 0; padding-right: 4px; }
.snap-modal .stock-table th:nth-child(2), .snap-modal .stock-table td:nth-child(2) { width: 72px; }
.snap-modal .stock-table th:nth-child(4), .snap-modal .stock-table td:nth-child(4) { width: 74px; text-align: right; }
.snap-modal .stock-table th:nth-child(5), .snap-modal .stock-table td:nth-child(5) { text-align: right; }
</style>
