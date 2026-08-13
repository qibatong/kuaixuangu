<template>
  <div class="page-shell">
    <div class="page-back" @click="$router.push('/')"><i class="fa fa-arrow-left"></i> 返回选股</div>

    <div class="mrk-head">
      <span class="mrk-title"><i class="fa fa-radar"></i> 市场雷达</span>
      <span class="mrk-sub">板块强度排行 · 盘中人气热榜（开盘啦数据）</span>
      <span class="mrk-time">{{ bjTime }}</span>
    </div>

    <div class="mrk-tabs">
      <button class="mrk-tab" :class="{ active: tab === 'board' }" @click="tab = 'board'">
        <i class="fa fa-th-large"></i> 板块轮动
      </button>
      <button class="mrk-tab" :class="{ active: tab === 'hot' }" @click="tab = 'hot'">
        <i class="fa fa-fire"></i> 人气热榜
      </button>
      <button class="mrk-tab" :class="{ active: tab === 'lhb' }" @click="tab = 'lhb'">
        <i class="fa fa-list-alt"></i> 龙虎榜
      </button>
    </div>

    <!-- 板块强度 -->
    <div v-if="tab === 'board'" class="mrk-panel">
      <div v-if="boardLoading" class="loading-placeholder"><div class="spinner"></div><div>加载板块强度...</div></div>
      <div v-else-if="!boardList.length" class="empty-state">暂无板块强度数据</div>
      <table v-else class="stock-table">
        <thead>
          <tr>
            <th>排名</th><th>板块</th><th>强度</th><th>涨幅%</th><th>涨速%</th>
            <th>主力净额(亿)</th><th>量比</th><th>成交额(亿)</th><th>总市值(亿)</th><th>今PE</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(b, idx) in boardList" :key="b.boardCode">
            <td class="rank-col">{{ idx + 1 }}</td>
            <td class="name-col"><div class="name-main">{{ b.name }}</div><div class="board-code">{{ b.boardCode }}</div></td>
            <td class="strength">{{ Math.round(b.strength) }}</td>
            <td :class="b.change > 0 ? 'up' : 'down'">{{ signed(b.change) }}%</td>
            <td :class="b.speed > 0 ? 'up' : 'down'">{{ signed(b.speed) }}%</td>
            <td :class="b.mainNet > 0 ? 'up' : b.mainNet < 0 ? 'down' : 'dim'">{{ yi(b.mainNet) }}</td>
            <td>{{ b.volRatio.toFixed(2) }}</td>
            <td>{{ yi(b.amount) }}</td>
            <td>{{ yi(b.totalMv) }}</td>
            <td class="dim">{{ b.peNow ? b.peNow.toFixed(1) : '-' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 人气热榜 -->
    <div v-else-if="tab === 'hot'" class="mrk-panel">
      <div v-if="hotLoading" class="loading-placeholder"><div class="spinner"></div><div>加载人气热榜...</div></div>
      <div v-else-if="!hotList.length" class="empty-state">暂无热榜数据</div>
      <table v-else class="stock-table">
        <thead>
          <tr><th>人气排名</th><th>代码</th><th>名称</th><th>涨跌幅%</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="h in hotList" :key="h.code">
            <td class="rank-col">{{ h.rank }}</td>
            <td class="code-click" @click="linkToSoftware(h.code)">{{ h.code }}</td>
            <td>{{ h.name }}</td>
            <td :class="h.change > 0 ? 'up' : h.change < 0 ? 'down' : 'dim'">{{ signed(h.change) }}%</td>
            <td><button class="pool-add-btn" @click="addToPool(h)">＋池</button></td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 龙虎榜 -->
    <div v-else class="mrk-panel">
      <div v-if="lhbLoading" class="loading-placeholder"><div class="spinner"></div><div>加载龙虎榜...</div></div>
      <div v-else-if="!lhbList.length" class="empty-state">暂无龙虎榜数据</div>
      <table v-else class="stock-table">
        <thead>
          <tr><th>代码</th><th>名称</th><th>涨跌幅%</th><th>连板</th><th>买入(亿)</th><th>成交额(亿)</th><th>换手%</th><th>振幅%</th><th>流通市值(亿)</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="l in lhbList" :key="l.code">
            <td class="code-click" @click="linkToSoftware(l.code)">{{ l.code }}</td>
            <td class="name-col"><div class="name-main">{{ l.name }}</div></td>
            <td :class="l.change > 0 ? 'up' : 'down'">{{ signed(l.change) }}%</td>
            <td><span v-if="l.limitBoards > 0" class="lb-badge">{{ l.limitBoards }}板</span><span v-else class="dim">-</span></td>
            <td :class="l.buyIn > 0 ? 'up' : l.buyIn < 0 ? 'down' : 'dim'">{{ yi(l.buyIn) }}</td>
            <td>{{ yi(l.amount) }}</td>
            <td>{{ l.turnover.toFixed(2) }}</td>
            <td>{{ l.amplitude.toFixed(2) }}</td>
            <td>{{ yi(l.floatMv) }}</td>
            <td>
              <button class="pool-add-btn" style="margin-right:4px;" @click="viewLhbDetail(l)">明细</button>
              <button class="pool-add-btn" @click="addToPool(l)">＋池</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 龙虎榜营业部明细弹窗 -->
    <div v-if="lhbModal.show" class="modal-mask" @click.self="lhbModal.show = false">
      <div class="reason-modal">
        <div class="reason-head">
          <span><i class="fa fa-list-alt" style="color:#ffb400;"></i> {{ lhbModal.detail.name }} {{ lhbModal.code }} · 龙虎榜营业部</span>
          <button class="close-btn" @click="lhbModal.show = false"><i class="fa fa-close"></i></button>
        </div>
        <div v-if="lhbLoading" class="reason-loading">查询中...</div>
        <template v-else>
          <div v-if="lhbModal.detail.upReason" class="lhb-reason">涨停原因：{{ lhbModal.detail.upReason }}</div>
          <div class="lhb-total">
            <span>买入总计 <b class="up">{{ yi(lhbModal.detail.buyTotal) }}亿</b></span>
            <span>卖出总计 <b class="down">{{ yi(lhbModal.detail.sellTotal) }}亿</b></span>
            <span>换手 {{ (lhbModal.detail.turnover || 0).toFixed(2) }}%</span>
          </div>
          <div class="lhb-cols">
            <div class="lhb-col">
              <div class="lhb-col-title buy">买入营业部</div>
              <div v-for="(b, i) in lhbModal.detail.buyList" :key="i" class="lhb-row">
                <span class="lhb-idx">{{ i + 1 }}</span>
                <span class="lhb-name">{{ b.name }}</span>
                <span class="lhb-amt up">+{{ (b.buy / 1e8).toFixed(2) }}亿</span>
              </div>
              <div v-if="!lhbModal.detail.buyList.length" class="lhb-empty">无</div>
            </div>
            <div class="lhb-col">
              <div class="lhb-col-title sell">卖出营业部</div>
              <div v-for="(s, i) in lhbModal.detail.sellList" :key="i" class="lhb-row">
                <span class="lhb-idx">{{ i + 1 }}</span>
                <span class="lhb-name">{{ s.name }}</span>
                <span class="lhb-amt down">-{{ (s.sell / 1e8).toFixed(2) }}亿</span>
              </div>
              <div v-if="!lhbModal.detail.sellList.length" class="lhb-empty">无</div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, reactive } from 'vue'
import { kplBoardRank, kplHotRank, kplLhb, kplLhbDetail } from '../api/kpl'
import { linkToSoftware } from '../utils/tdx'
import { bjTimeStr } from '../utils/time'
import { usePoolStore } from '../stores/pool'
import { showToast } from '../utils/toast'

const pool = usePoolStore()
const tab = ref('board')
const boardList = ref([])
const hotList = ref([])
const lhbList = ref([])
const boardLoading = ref(true)
const hotLoading = ref(true)
const lhbLoading = ref(true)
const bjTime = ref('--:--:--')
let clockTimer = null
let refreshTimer = null

const lhbModal = reactive({ show: false, code: '', detail: { name: '', buyList: [], sellList: [], buyTotal: 0, sellTotal: 0, upReason: '', turnover: 0 } })

function yi(v) { return (v / 1e8).toFixed(2) }
function signed(v) { return (v > 0 ? '+' : '') + Number(v).toFixed(2) }

async function viewLhbDetail(l) {
  lhbModal.show = true
  lhbModal.code = l.code
  lhbModal.detail = { name: l.name, buyList: [], sellList: [], buyTotal: 0, sellTotal: 0, upReason: '', turnover: 0 }
  lhbLoading.value = true
  try {
    const d = await kplLhbDetail(l.code)
    if (d && d.detail) lhbModal.detail = d.detail
  } catch (e) { /* 静默 */ } finally {
    lhbLoading.value = false
  }
}

function addToPool(h) {
  const n = pool.addStocks([{ code: h.code, name: h.name }])
  showToast(n ? `✅ ${h.code} ${h.name} 已加入股票池` : `${h.code} 已在池中`, n ? 'success' : 'info')
}

async function loadBoard() {
  try {
    const d = await kplBoardRank()
    boardList.value = d.list || []
  } catch (e) { /* 静默 */ } finally {
    boardLoading.value = false
  }
}

async function loadHot() {
  try {
    const d = await kplHotRank()
    hotList.value = d.list || []
  } catch (e) { /* 静默 */ } finally {
    hotLoading.value = false
  }
}

async function loadLhb() {
  try {
    const d = await kplLhb()
    lhbList.value = d.list || []
  } catch (e) { /* 静默 */ } finally {
    lhbLoading.value = false
  }
}

onMounted(() => {
  bjTime.value = bjTimeStr()
  clockTimer = setInterval(() => { bjTime.value = bjTimeStr() }, 1000)
  loadBoard()
  loadHot()
  loadLhb()
  refreshTimer = setInterval(() => { loadBoard(); loadHot(); loadLhb() }, 60000)
})
onBeforeUnmount(() => {
  if (clockTimer) clearInterval(clockTimer)
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<style scoped>
.page-shell { max-width: 1400px; margin: 0 auto; padding: 16px; }
.page-back { color: #9aa; cursor: pointer; font-size: 13px; margin-bottom: 12px; display: inline-block; }
.page-back:hover { color: #ffb400; }
.mrk-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.mrk-title { font-size: 20px; font-weight: 700; color: #ffe0a0; }
.mrk-title .fa { color: #ffb400; }
.mrk-sub { color: #999; font-size: 13px; }
.mrk-time { margin-left: auto; color: #aaa; font-size: 14px; font-family: monospace; }
.mrk-tabs { display: flex; gap: 8px; margin-bottom: 14px; }
.mrk-tab {
  padding: 8px 18px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.15);
  background: rgba(255,255,255,0.04); color: #bbb; font-size: 14px; cursor: pointer; transition: all 0.2s;
}
.mrk-tab:hover { border-color: #ffb400; color: #ffe0a0; }
.mrk-tab.active { background: rgba(255,180,0,0.15); border-color: #ffb400; color: #ffd700; font-weight: 600; }
.mrk-panel { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.1); border-radius: 10px; padding: 14px; }
.loading-placeholder { text-align: center; padding: 40px; color: #888; }
.spinner { width: 28px; height: 28px; border: 3px solid rgba(255,180,0,0.3); border-top-color: #ffb400; border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 10px; }
@keyframes spin { to { transform: rotate(360deg); } }
.empty-state { text-align: center; padding: 40px; color: #888; }
.board-code { font-size: 11px; color: #777; }
.strength { color: #ffb400; font-weight: 700; }
.lb-badge { display: inline-block; color: #ff8a5c; border: 1px solid rgba(255,80,40,0.5); border-radius: 4px; padding: 0 5px; font-size: 11px; background: rgba(255,80,40,0.12); }
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,0.6); display: flex; align-items: center; justify-content: center; z-index: 1000; }
.reason-modal { background: #1c1f26; border: 1px solid rgba(255,255,255,0.15); border-radius: 12px; width: 640px; max-width: 92vw; max-height: 76vh; overflow: auto; padding: 18px; }
.reason-head { display: flex; align-items: center; justify-content: space-between; color: #ffe0a0; font-size: 16px; margin-bottom: 14px; }
.close-btn { background: none; border: none; color: #999; cursor: pointer; font-size: 16px; }
.close-btn:hover { color: #ff6a6a; }
.reason-loading { color: #888; padding: 20px; text-align: center; }
.lhb-reason { color: #ffb400; font-size: 13px; margin-bottom: 10px; line-height: 1.5; }
.lhb-total { display: flex; gap: 20px; color: #aaa; font-size: 13px; margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px solid rgba(255,255,255,0.08); }
.lhb-cols { display: flex; gap: 16px; }
.lhb-col { flex: 1; }
.lhb-col-title { font-size: 13px; margin-bottom: 8px; }
.lhb-col-title.buy { color: #ff8a8a; }
.lhb-col-title.sell { color: #8ae08a; }
.lhb-row { display: flex; align-items: center; gap: 6px; padding: 4px 0; font-size: 12px; border-bottom: 1px solid rgba(255,255,255,0.04); }
.lhb-idx { width: 16px; color: #777; }
.lhb-name { flex: 1; color: #ddd; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lhb-amt { font-family: monospace; }
.lhb-empty { color: #666; font-size: 12px; padding: 8px 0; }
</style>
