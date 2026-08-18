<template>
  <div class="page-shell">
    <div class="mrk-head">
      <span class="mrk-title"><i class="fa fa-radar"></i> 市场雷达</span>
      <span class="mrk-sub">板块强度排行 · 盘中人气热榜 · 龙虎榜</span>
      <span class="mrk-time">{{ bjTime }}</span>
    </div>

    <div class="mrk-tabs">
      <button class="mrk-tab" :class="{ active: tab === 'board' }" @click="switchTab('board')">
        <i class="fa fa-th-large"></i> 板块强度
      </button>
      <button class="mrk-tab" :class="{ active: tab === 'history' }" @click="switchTab('history')">
        <i class="fa fa-history"></i> 板块轮动历史
      </button>
      <button class="mrk-tab" :class="{ active: tab === 'hot' }" @click="switchTab('hot')">
        <i class="fa fa-fire"></i> 人气热榜
      </button>
      <button class="mrk-tab" :class="{ active: tab === 'lhb' }" @click="switchTab('lhb')">
        <i class="fa fa-list-alt"></i> 龙虎榜
      </button>
    </div>

    <!-- 板块强度 -->
    <div v-if="tab === 'board'" class="mrk-panel">
      <div class="rot-toolbar">
        <span class="rot-tip"><i class="fa fa-info-circle"></i> 实时板块强度排行；选日期可回看历史</span>
        <input v-model="datePicker" type="date" class="rot-date" @change="loadBoard">
        <button class="rot-reset-btn" title="回到实时" @click="clearDate('board')"><i class="fa fa-bolt"></i></button>
        <span v-if="boardDataDate && datePicker" class="rot-data-date"><i class="fa fa-calendar"></i> 数据日期 {{ boardDataDate }}<template v-if="boardDataDate !== datePicker">（{{ datePicker }} 非交易日，自动对齐）</template></span>
      </div>
      <div v-if="boardLoading" class="loading-placeholder"><div class="spinner"></div><div>加载板块强度...</div></div>
      <div v-else-if="!boardList.length" class="empty-state">暂无板块强度数据</div>
      <table v-else class="stock-table">
        <thead>
          <tr>
            <th>排名</th>
            <th class="sortable" :class="{ active: boardSort.keyOf('name') }" @click="boardSort.onSort('name', 'string')">板块<span class="sort-ind">{{ boardSort.ind('name') }}</span></th>
            <th class="sortable" :class="{ active: boardSort.keyOf('strength') }" @click="boardSort.onSort('strength')">强度<span class="sort-ind">{{ boardSort.ind('strength') }}</span></th>
            <th class="sortable" :class="{ active: boardSort.keyOf('change') }" @click="boardSort.onSort('change')">涨幅%<span class="sort-ind">{{ boardSort.ind('change') }}</span></th>
            <th class="sortable" :class="{ active: boardSort.keyOf('speed') }" @click="boardSort.onSort('speed')">涨速%<span class="sort-ind">{{ boardSort.ind('speed') }}</span></th>
            <th class="sortable" :class="{ active: boardSort.keyOf('mainNet') }" @click="boardSort.onSort('mainNet')">主力净额(亿)<span class="sort-ind">{{ boardSort.ind('mainNet') }}</span></th>
            <th class="sortable" :class="{ active: boardSort.keyOf('volRatio') }" @click="boardSort.onSort('volRatio')">量比<span class="sort-ind">{{ boardSort.ind('volRatio') }}</span></th>
            <th class="sortable" :class="{ active: boardSort.keyOf('amount') }" @click="boardSort.onSort('amount')">成交额(亿)<span class="sort-ind">{{ boardSort.ind('amount') }}</span></th>
            <th class="sortable" :class="{ active: boardSort.keyOf('totalMv') }" @click="boardSort.onSort('totalMv')">总市值(亿)<span class="sort-ind">{{ boardSort.ind('totalMv') }}</span></th>
            <th class="sortable" :class="{ active: boardSort.keyOf('peNow') }" @click="boardSort.onSort('peNow')">今PE<span class="sort-ind">{{ boardSort.ind('peNow') }}</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(b, idx) in boardSort.sorted(boardList)" :key="b.boardCode" class="board-row" @click="openBoardStocks(b)">
            <td class="rank-col">{{ idx + 1 }}</td>
            <td class="name-col"><div class="name-main">{{ b.name }}</div><div class="board-code">{{ b.boardCode }}</div><span class="board-detail-hint"><i class="fa fa-chevron-circle-right"></i> 成分股</span></td>
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

    <!-- 板块成分股弹层(2026-08-17 主人需求: 板块强度点开看成分股) -->
    <div v-if="stocksOpen" class="mrk-modal-mask" @click.self="closeBoardStocks">
      <div class="mrk-modal">
        <div class="mrk-modal-head">
          <div class="mrk-modal-title"><i class="fa fa-th-large"></i> {{ currentBoard?.name }} <span class="mrk-modal-code">{{ currentBoard?.boardCode }}</span><span class="mrk-modal-sub">成分股 Top{{ boardStocks.length }}</span></div>
          <button class="mrk-modal-close" @click="closeBoardStocks"><i class="fa fa-times"></i></button>
        </div>
        <div v-if="stocksLoading" class="loading-placeholder"><div class="spinner"></div><div>加载成分股...</div></div>
        <div v-else-if="!boardStocks.length" class="empty-state">该板块暂无成分股数据（午休时段/接口暂不可用，交易时段或收盘后重试）</div>
        <div v-else class="mrk-modal-body">
          <table class="stock-table mrk-modal-table">
            <thead>
              <tr>
                <th>代码</th>
                <th>名称</th>
                <th>最新价</th>
                <th class="sortable" :class="{ active: stockSort.keyOf('change') }" @click="stockSort.onSort('change')">涨幅%<span class="sort-ind">{{ stockSort.ind('change') }}</span></th>
                <th class="sortable" :class="{ active: stockSort.keyOf('turnover') }" @click="stockSort.onSort('turnover')">换手%<span class="sort-ind">{{ stockSort.ind('turnover') }}</span></th>
                <th class="sortable" :class="{ active: stockSort.keyOf('amount') }" @click="stockSort.onSort('amount')">成交额(亿)<span class="sort-ind">{{ stockSort.ind('amount') }}</span></th>
                <th class="sortable" :class="{ active: stockSort.keyOf('mainNet') }" @click="stockSort.onSort('mainNet')">主力净额(亿)<span class="sort-ind">{{ stockSort.ind('mainNet') }}</span></th>
                <th class="sortable" :class="{ active: stockSort.keyOf('floatMv') }" @click="stockSort.onSort('floatMv')">流通(亿)<span class="sort-ind">{{ stockSort.ind('floatMv') }}</span></th>
                <th>涨停标识</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in stockSort.sorted(boardStocks)" :key="s.code">
                <td class="code-click" @click="linkToSoftware(s.code)">{{ s.code }}</td>
                <td class="name-col"><div class="name-main">{{ s.name }}</div></td>
                <td>{{ s.price ? s.price.toFixed(2) : '-' }}</td>
                <td :class="s.change > 0 ? 'up' : s.change < 0 ? 'down' : 'dim'">{{ s.change ? signed(s.change) + '%' : '-' }}</td>
                <td>{{ s.turnover ? s.turnover.toFixed(2) : '-' }}</td>
                <td>{{ s.amount ? yi(s.amount) : '-' }}</td>
                <td :class="s.mainNet > 0 ? 'up' : s.mainNet < 0 ? 'down' : 'dim'">{{ s.mainNet ? yi(s.mainNet) : '-' }}</td>
                <td>{{ s.floatMv ? yi(s.floatMv) : '-' }}</td>
                <td><span v-if="s.limitTag || s.ladder" class="lb-badge">{{ s.limitTag || s.ladder }}</span><span v-else class="dim">-</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- 板块轮动历史 -->
    <div v-else-if="tab === 'history'" class="mrk-panel">
      <div class="rot-toolbar">
        <span class="rot-tip"><i class="fa fa-info-circle"></i> 工作日 15:30 后自动保存当日 Top10；近期数据积累后展示趋势</span>
        <div class="rot-source">
          <button
v-for="s in sourceOptions" :key="s.key"
                  :class="{ active: rotSource === s.key }"
                  class="rot-source-btn"
                  @click="switchSource(s.key)"
>
            <i :class="s.icon"></i> {{ s.label }}
          </button>
        </div>
        <select v-model.number="rotDays" class="rot-select" @change="loadHistory">
          <option :value="10">近 10 日</option>
          <option :value="20">近 20 日</option>
          <option :value="30">近 30 日</option>
          <option :value="50">近 50 日</option>
        </select>
        <button class="rot-reset-btn" title="刷新" @click="loadHistory"><i class="fa fa-refresh"></i></button>
      </div>
      <div v-if="rotLoading" class="loading-placeholder"><div class="spinner"></div></div>
      <div v-else-if="!rot.dates.length" class="empty-state">
        暂无历史数据(每日 15:30 后调度器抓取积累)
      </div>
      <template v-else>
        <!-- 顶部表格: 行=排名, 列=日期 -->
        <div class="rot-table-scroll">
          <table class="rot-table">
            <thead>
              <tr>
                <th class="rot-rownum">排名</th>
                <th v-for="d in rot.dates" :key="d" class="rot-date">{{ d.slice(5) }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="rank in 10" :key="rank">
                <td class="rot-rownum">{{ rank }}</td>
                <td v-for="d in rot.dates" :key="d+rank" class="rot-cell">
                  <template v-for="b in boardAt(d, rank)" :key="b.name">
                    <div :class="['rot-board', 'rot-c-' + (colorMap[b.name] || 0)]">{{ b.name }}</div>
                    <div class="rot-strength">{{ Math.round(b.strength) }}</div>
                  </template>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <!-- 强度趋势线 + 量能柱状 + 多窗口排名(独立组件) -->
        <RotCharts :dates="rot.dates" :rot-map="rotMap" :windows="rot.windows" :common-names="rot.common_names" />
      </template>
    </div>

    <!-- 人气热榜 -->
    <div v-else-if="tab === 'hot'" class="mrk-panel">
      <div class="rot-toolbar">
        <span class="rot-tip"><i class="fa fa-info-circle"></i> 人气热榜；选日期可回看历史</span>
        <div class="rot-source">
          <button
v-for="s in sourceOptions" :key="s.key"
                  :class="{ active: hotSource === s.key }"
                  class="rot-source-btn"
                  @click="switchHotSource(s.key)"
>
            <i :class="s.icon"></i> {{ s.label }}
          </button>
        </div>
        <input v-model="datePicker" type="date" class="rot-date" @change="loadHot">
        <button class="rot-reset-btn" title="回到实时" @click="clearDate('hot')"><i class="fa fa-bolt"></i></button>
        <span v-if="hotDataDate && datePicker" class="rot-data-date"><i class="fa fa-calendar"></i> 数据日期 {{ hotDataDate }}<template v-if="hotDataDate !== datePicker">（{{ datePicker }} 非交易日，自动对齐）</template></span>
      </div>
      <div v-if="hotLoading" class="loading-placeholder"><div class="spinner"></div><div>加载人气热榜...</div></div>
      <div v-else-if="!hotList.length" class="empty-state">暂无热榜数据</div>
      <table v-else class="stock-table">
        <thead>
          <tr>
            <th class="sortable" :class="{ active: hotSort.keyOf('rank') }" @click="hotSort.onSort('rank')">人气排名<span class="sort-ind">{{ hotSort.ind('rank') }}</span></th>
            <th class="sortable" :class="{ active: hotSort.keyOf('code') }" @click="hotSort.onSort('code', 'string')">代码<span class="sort-ind">{{ hotSort.ind('code') }}</span></th>
            <th class="sortable" :class="{ active: hotSort.keyOf('name') }" @click="hotSort.onSort('name', 'string')">名称<span class="sort-ind">{{ hotSort.ind('name') }}</span></th>
            <th class="sortable" :class="{ active: hotSort.keyOf('change') }" @click="hotSort.onSort('change')">涨跌幅%<span class="sort-ind">{{ hotSort.ind('change') }}</span></th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="h in hotSort.sorted(hotList)" :key="h.code">
            <td class="rank-col">{{ h.rank }}</td>
            <td class="code-click" @click="linkToSoftware(h.code)">{{ h.code }}</td>
            <td>{{ h.name }}</td>
            <td :class="h.change > 0 ? 'up' : h.change < 0 ? 'down' : 'dim'">{{ signed(h.change) }}%</td>
            <td><button class="pool-add-btn" :class="{ added: inPool(h.code) }" @click.stop="addToPool(h)">{{ inPool(h.code) ? '已加自选' : '＋自选' }}</button></td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 龙虎榜 -->
    <div v-else class="mrk-panel">
      <div class="rot-toolbar">
        <span class="rot-tip"><i class="fa fa-info-circle"></i> 龙虎榜当日/历史；选日期可回看</span>
        <input v-model="datePicker" type="date" class="rot-date" @change="loadLhb">
        <button class="rot-reset-btn" title="回到实时" @click="clearDate('lhb')"><i class="fa fa-bolt"></i></button>
        <span v-if="lhbDataDate && datePicker" class="rot-data-date"><i class="fa fa-calendar"></i> 数据日期 {{ lhbDataDate }}<template v-if="lhbDataDate !== datePicker">（{{ datePicker }} 非交易日，自动对齐）</template></span>
      </div>
      <div v-if="lhbLoading" class="loading-placeholder"><div class="spinner"></div><div>加载龙虎榜...</div></div>
      <div v-else-if="!lhbList.length" class="empty-state">暂无龙虎榜数据</div>
      <table v-else class="stock-table">
        <thead>
          <tr>
            <th class="sortable" :class="{ active: lhbSort.keyOf('code') }" @click="lhbSort.onSort('code', 'string')">代码<span class="sort-ind">{{ lhbSort.ind('code') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('name') }" @click="lhbSort.onSort('name', 'string')">名称<span class="sort-ind">{{ lhbSort.ind('name') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('change') }" @click="lhbSort.onSort('change')">涨跌幅%<span class="sort-ind">{{ lhbSort.ind('change') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('limitBoards') }" @click="lhbSort.onSort('limitBoards')">连板<span class="sort-ind">{{ lhbSort.ind('limitBoards') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('buyIn') }" @click="lhbSort.onSort('buyIn')">买入(亿)<span class="sort-ind">{{ lhbSort.ind('buyIn') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('amount') }" @click="lhbSort.onSort('amount')">成交额(亿)<span class="sort-ind">{{ lhbSort.ind('amount') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('turnover') }" @click="lhbSort.onSort('turnover')">换手%<span class="sort-ind">{{ lhbSort.ind('turnover') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('amplitude') }" @click="lhbSort.onSort('amplitude')">振幅%<span class="sort-ind">{{ lhbSort.ind('amplitude') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('floatMv') }" @click="lhbSort.onSort('floatMv')">流通市值(亿)<span class="sort-ind">{{ lhbSort.ind('floatMv') }}</span></th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="l in lhbSort.sorted(lhbList)" :key="l.code">
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
              <button class="pool-add-btn" :class="{ added: inPool(l.code) }" @click.stop="addToPool(l)">{{ inPool(l.code) ? '已加自选' : '＋自选' }}</button>
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
import { computed, onMounted, ref, reactive } from 'vue'
import { usePolling } from '../composables/usePolling'
import { kplBoardRank, kplBoardStocks, kplHotRank, kplLhb, kplLhbDetail, sectorRotation } from '../api/kpl'
import { linkToSoftware } from '../utils/tdx'
import { bjTimeStr } from '../utils/time'
import { usePoolStore } from '../stores/pool'
import { showToast } from '../utils/toast'
import { useSortable } from '../composables/useSortable'
import { yi, signed } from '../utils/format'
import RotCharts from '../components/RotCharts.vue'

const pool = usePoolStore()
const tab = ref('board')
const boardList = ref([])
const hotList = ref([])
const lhbList = ref([])
const boardLoading = ref(true)
const hotLoading = ref(true)
const lhbLoading = ref(true)
const hotSource = ref(localStorage.getItem('kuaixuan_hot_source') || 'kpl')
const datePicker = ref('')
const boardDataDate = ref('')
const hotDataDate = ref('')
const lhbDataDate = ref('')
const bjTime = ref('--:--:--')

const lhbModal = reactive({ show: false, code: '', detail: { name: '', buyList: [], sellList: [], buyTotal: 0, sellTotal: 0, upReason: '', turnover: 0 } })

// 各表独立排序实例
const boardSort = useSortable()
// 板块成分股弹层(2026-08-17 主人需求)
const stocksOpen = ref(false)
const stocksLoading = ref(false)
const currentBoard = ref(null)
const boardStocks = ref([])
const stockSort = useSortable()
const hotSort = useSortable()
const lhbSort = useSortable()

// 切 Tab 清排序
function switchTab(t) {
  tab.value = t
  boardSort.clear(); hotSort.clear(); lhbSort.clear()
}

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

// 是否已在股票池(与主页面 StockTable 一致: 已入池按钮变绿禁用)
function inPool(code) {
  return pool.stockPool.some(x => x.code === code)
}

async function loadBoard() {
  try {
    const d = await kplBoardRank(datePicker.value)
    boardList.value = d.list || []
    boardDataDate.value = d.date || ''
  } catch (e) { /* 静默 */ } finally {
    boardLoading.value = false
  }
}

// 板块成分股弹层: 点击板块行打开
async function openBoardStocks(b) {
  currentBoard.value = b
  stocksOpen.value = true
  stocksLoading.value = true
  stockSort.clear()
  try {
    const d = await kplBoardStocks(b.boardCode, datePicker.value)
    boardStocks.value = d.list || []
  } catch (e) { boardStocks.value = [] } finally {
    stocksLoading.value = false
  }
}

function closeBoardStocks() {
  stocksOpen.value = false
  boardStocks.value = []
  currentBoard.value = null
}

function clearDate(which) {
  datePicker.value = ''
  if (which === 'board') { boardLoading.value = true; loadBoard() }
  else if (which === 'hot') { hotLoading.value = true; loadHot() }
  else { lhbLoading.value = true; loadLhb() }
}

async function loadHot() {
  try {
    const d = await kplHotRank(hotSource.value, datePicker.value)
    hotList.value = d.list || []
    hotDataDate.value = d.date || ''
  } catch (e) { /* 静默 */ } finally {
    hotLoading.value = false
  }
}

function switchHotSource(src) {
  if (src === hotSource.value) return
  hotSource.value = src
  localStorage.setItem('kuaixuan_hot_source', src)
  hotLoading.value = true
  loadHot()
}

async function loadLhb() {
  try {
    const d = await kplLhb(datePicker.value)
    lhbList.value = d.list || []
    lhbDataDate.value = d.date || ''
  } catch (e) { /* 静默 */ } finally {
    lhbLoading.value = false
  }
}

// ===================== 板块轮动历史 =====================
const rotDays = ref(10)
const rotSource = ref(localStorage.getItem('kuaixuan_sector_source') || 'kpl')
const sourceOptions = [
  // 切换按钮不暴露第三方厂商名(非官方数据源, 页面不体现来源)
  { key: 'kpl', label: '源1', icon: 'fa fa-bullseye' },
  { key: 'em',  label: '源2', icon: 'fa fa-bar-chart' },
  { key: 'ths', label: '源3', icon: 'fa fa-line-chart' },
]
const rotLoading = ref(false)
const rot = reactive({ dates: [], days: [], windows: [], common_names: [], source: 'kpl' })

const rotMap = computed(() => {
  const m = {}
  for (const day of rot.days) {
    m[day.date] = day.boards || []
  }
  return m
})
// 出现 >= 3 次的板块按频次降序分配 8 色(红/橙/黄/靛蓝/天蓝/深蓝/紫/粉, 无绿系),
// 同板块多日同色; 出现 < 3 次不配色(rot-c-0), 避免整板花花绿绿
const colorMap = computed(() => {
  const cnt = {}
  for (const day of rot.days) {
    for (const b of day.boards || []) {
      cnt[b.name] = (cnt[b.name] || 0) + 1
    }
  }
  const ranks = Object.entries(cnt)
    .filter(([_, c]) => c >= 3)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
  const m = {}
  ranks.forEach(([name], i) => { m[name] = (i % 8) + 1 })
  return m
})

function boardAt(date, rank) {
  const day = rotMap.value[date] || []
  return day.filter(b => b.rank === rank)
}

async function loadHistory() {
  rotLoading.value = true
  try {
    const d = await sectorRotation(rotDays.value, rotSource.value)
    rot.source = (d && d.source) || rotSource.value
    rot.dates = (d && d.dates) || []
    rot.days = (d && d.rotation && d.rotation.days) || []
    rot.windows = (d && d.windows && d.windows.windows) || []
    rot.common_names = (d && d.windows && d.windows.common_names) || []
  } catch (e) { /* ignore */ }
  finally { rotLoading.value = false }
}

function switchSource(src) {
  if (src === rotSource.value) return
  rotSource.value = src
  localStorage.setItem('kuaixuan_sector_source', src)
  loadHistory()
}

onMounted(() => {
  bjTime.value = bjTimeStr()
  usePolling(() => { bjTime.value = bjTimeStr() }, 1000, { immediate: false })
  loadBoard()
  loadHistory()
  loadHot()
  loadLhb()
  usePolling(() => { loadBoard(); loadHot(); loadLhb() }, 60000)
})
</script>

<style scoped>
/* 板块行点击态(2026-08-17 主人需求: 点板块看成分股) */
.board-row { cursor: pointer; }
.board-row:hover td { background: rgba(255, 180, 0, 0.06); }
.board-click { cursor: pointer; }
.board-click .name-main:hover { color: #ffb400; }
.board-detail-hint {
  display: inline-flex; align-items: center; gap: 3px;
  font-size: 11px; color: var(--accent); opacity: 0.85; margin-left: 6px;
  border: 1px solid rgba(var(--accent-rgb), 0.4); border-radius: 10px; padding: 0 6px;
}
/* 成分股弹层 */
.mrk-modal-mask {
  position: fixed; inset: 0; z-index: 2000;
  background: rgba(0, 0, 0, 0.55);
  display: flex; align-items: center; justify-content: center; padding: 20px;
}
.mrk-modal {
  background: var(--bg-panel);
  border: 1px solid rgba(var(--accent-rgb), 0.4);
  border-radius: 12px;
  max-width: 980px; width: 100%;
  max-height: 80vh;
  display: flex; flex-direction: column;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.45);
}
.mrk-modal-head {
  display: flex; align-items: center; gap: 10px;
  padding: 12px 16px; border-bottom: 1px solid var(--border-soft);
}
.mrk-modal-title { font-size: 17px; font-weight: 700; color: var(--text-main); }
.mrk-modal-title .fa { color: var(--accent); }
.mrk-modal-code { font-size: 13px; color: var(--text-muted); font-family: monospace; margin-left: 6px; }
.mrk-modal-sub { font-size: 12px; color: var(--text-muted); margin-left: 8px; }
.mrk-modal-close {
  margin-left: auto; background: transparent; border: none;
  color: var(--text-muted); font-size: 18px; cursor: pointer; padding: 4px 8px;
}
.mrk-modal-close:hover { color: var(--text-main); }
.mrk-modal-body { overflow-y: auto; padding: 10px 14px 14px; }
.mrk-modal-table { min-width: 640px; }
body[data-bg="light"] .mrk-modal-title { color: #1a1d26; }
body[data-bg="light"] .board-detail-hint { color: #a06a00; border-color: rgba(160, 106, 0, 0.4); }
body[data-bg="light"] .board-row:hover td { background: rgba(199, 145, 0, 0.08); }
.page-back { color: var(--text-muted); cursor: pointer; font-size: 13px; margin-bottom: 12px; display: inline-block; }
.page-back:hover { color: #ffb400; }
.mrk-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.mrk-title { font-size: 20px; font-weight: 700; color: #ffe0a0; }
.mrk-title .fa { color: #ffb400; }
.mrk-sub { color: var(--text-muted); font-size: 13px; }
.mrk-time { margin-left: auto; color: #aaa; font-size: 14px; font-family: monospace; }
.mrk-tabs { display: flex; gap: 8px; margin-bottom: 14px; }
.mrk-tab {
  padding: 8px 18px; border-radius: 8px; border: 1px solid var(--border-soft);
  background: var(--bg-hover); color: var(--text-secondary); font-size: 14px; cursor: pointer; transition: all 0.2s;
}
.mrk-tab:hover { border-color: #ffb400; color: #ffe0a0; }
.mrk-tab.active { background: rgba(255,180,0,0.15); border-color: #ffb400; color: #ffd700; font-weight: 600; }
.mrk-panel { background: var(--bg-hover); border: 1px solid var(--border-soft); border-radius: 10px; padding: 14px; }
.loading-placeholder { text-align: center; padding: 40px; color: var(--text-muted); }
.spinner { width: 28px; height: 28px; border: 3px solid rgba(255,180,0,0.3); border-top-color: #ffb400; border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 10px; }
@keyframes spin { to { transform: rotate(360deg); } }
.empty-state { text-align: center; padding: 40px; color: var(--text-muted); }
.board-code { font-size: 11px; color: var(--text-muted); }
.strength { color: #ffb400; font-weight: 700; }
.lb-badge { display: inline-block; color: #ff8a5c; border: 1px solid rgba(255,80,40,0.5); border-radius: 4px; padding: 0 5px; font-size: 11px; background: rgba(255,80,40,0.12); }
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,0.6); display: flex; align-items: center; justify-content: center; z-index: 1000; }
.reason-modal { background: var(--bg-panel-solid); border: 1px solid var(--border-soft); border-radius: 12px; width: 640px; max-width: 92vw; max-height: 76vh; overflow: auto; padding: 18px; }
.reason-head { display: flex; align-items: center; justify-content: space-between; color: #ffe0a0; font-size: 16px; margin-bottom: 14px; }
.close-btn { background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: 16px; }
.close-btn:hover { color: #ff6a6a; }
.reason-loading { color: var(--text-muted); padding: 20px; text-align: center; }
.lhb-reason { color: #ffb400; font-size: 13px; margin-bottom: 10px; line-height: 1.5; }
.lhb-total { display: flex; gap: 20px; color: #aaa; font-size: 13px; margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px solid var(--border-soft); }
.lhb-cols { display: flex; gap: 16px; }
.lhb-col { flex: 1; }
.lhb-col-title { font-size: 13px; margin-bottom: 8px; }
.lhb-col-title.buy { color: #ff8a8a; }
.lhb-col-title.sell { color: #8ae08a; }
.lhb-row { display: flex; align-items: center; gap: 6px; padding: 4px 0; font-size: 12px; border-bottom: 1px solid rgba(255,255,255,0.04); }
.lhb-idx { width: 16px; color: var(--text-muted); }
.lhb-name { flex: 1; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lhb-amt { font-family: monospace; }
.lhb-empty { color: #666; font-size: 12px; padding: 8px 0; }

/* 浅色主题覆盖 */
body[data-bg="light"] .page-back { color: #5a6b85; }
body[data-bg="light"] .page-back:hover { color: #c79100; }
body[data-bg="light"] .mrk-title { color: #8a5500; }
body[data-bg="light"] .mrk-title .fa { color: #c79100; }
body[data-bg="light"] .mrk-sub { color: #5a6b85; }
body[data-bg="light"] .mrk-time { color: #5a6b85; }
body[data-bg="light"] .mrk-tab { color: #5a6b85; border-color: var(--border-soft); background: rgba(255,255,255,0.6); }
body[data-bg="light"] .mrk-tab:hover { color: #5a4a3a; border-color: #c79100; }
body[data-bg="light"] .mrk-tab.active { color: #5a4a3a; background: rgba(255,180,0,0.15); border-color: #c79100; }
body[data-bg="light"] .mrk-panel { background: rgba(255,255,255,0.85); border-color: var(--border-soft); }
body[data-bg="light"] .strength { color: #8a5500; }
body[data-bg="light"] .lb-badge { color: #b83010; border-color: rgba(184,48,16,0.5); background: rgba(255,80,80,0.1); }
body[data-bg="light"] .reason-head { color: #5a4a3a; }
body[data-bg="light"] .close-btn:hover { color: #b83010; }
body[data-bg="light"] .lhb-reason { color: #8a5500; }
body[data-bg="light"] .lhb-col-title { color: #5a4a3a; }
body[data-bg="light"] .lhb-col-title.buy { color: #b83010; }
body[data-bg="light"] .lhb-name { color: #1a1d26; }
body[data-bg="light"] .lhb-row { border-bottom-color: rgba(0,0,0,0.08); }
body[data-bg="light"] .lhb-empty { color: #6a7a90; }
body[data-bg="light"] .board-code { color: #1a1d26; }
body[data-bg="light"] .reason-modal { background: rgba(255,255,255,0.98); border-color: var(--border-soft); }
/* ===================== 板块轮动历史视图 ===================== */
.rot-toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
.rot-tip { color: var(--text-muted, #aaa); font-size: 12px; flex: 1; min-width: 0; }

/* 注: rot-date / rot-select / rot-reset-btn / rot-data-date 为全局通用样式,
   定义在 src/styles/main.css(市场雷达 & 连板天梯共用) */
.rot-source { display: flex; gap: 0; border-radius: 6px; overflow: hidden; border: 1px solid var(--border-soft, #444); }
.rot-source-btn { background: var(--bg-input, #1a1a1a); color: var(--text-secondary, #aaa); border: none; padding: 5px 12px; font-size: 12px; cursor: pointer; transition: background 0.15s; }
.rot-source-btn:hover { background: var(--bg-card, #222); }
.rot-source-btn.active { background: var(--accent-warm, #ffb400); color: #1a1a1a; font-weight: 600; }
body[data-bg="light"] .rot-source { border-color: #d0d0d0; }
body[data-bg="light"] .rot-source-btn { background: #f5f5f5; color: #555; }
body[data-bg="light"] .rot-source-btn:hover { background: #eaeaea; }
body[data-bg="light"] .rot-source-btn.active { background: #d97b00; color: #fff; }
.rot-table-scroll { overflow-x: auto; border: 1px solid var(--border-soft); border-radius: 6px; }
.rot-table { border-collapse: collapse; min-width: 100%; font-size: 12px; }
.rot-table th, .rot-table td { padding: 5px 8px; text-align: center; border-bottom: 1px solid var(--border-soft); white-space: nowrap; }
.rot-table th { background: var(--bg-hover); color: var(--text-secondary); font-weight: 500; position: sticky; top: 0; }
.rot-rownum { color: var(--text-muted); font-size: 11px; min-width: 40px; }
.rot-date { color: var(--text-secondary); font-size: 11px; min-width: 70px; }
.rot-cell { min-width: 80px; padding: 3px 4px !important; vertical-align: middle; }
/* 同名板块(出现 >= 2 次)按独立颜色高亮区分: 8 色循环, 暗/亮主题各一套 */
.rot-board { font-size: 12px; color: var(--text-main); border-radius: 4px; padding: 1px 6px; display: inline-block; }
.rot-board.rot-c-0 { /* 仅出现 1 次: 不高亮 */ color: var(--text-main); background: transparent; }
.rot-c-1 { color: #fff; background: #E24B4A; } .rot-c-2 { color: #fff; background: #F08C3F; }
.rot-c-3 { color: #222; background: #E6BE2A; } .rot-c-4 { color: #fff; background: #5C6BC0; }
.rot-c-5 { color: #fff; background: #38A6DF; } .rot-c-6 { color: #fff; background: #2851A8; }
.rot-c-7 { color: #fff; background: #9A57C9; } .rot-c-8 { color: #fff; background: #D45B92; }
body[data-bg="light"] .rot-c-1 { background: #C32D2C; }
body[data-bg="light"] .rot-c-2 { background: #D86A1B; }
body[data-bg="light"] .rot-c-3 { color: #4a3a00; background: #F0CB3F; }
body[data-bg="light"] .rot-c-4 { background: #3F51B5; }
body[data-bg="light"] .rot-c-5 { background: #1E7FB5; }
body[data-bg="light"] .rot-c-6 { background: #1D3F8C; }
body[data-bg="light"] .rot-c-7 { background: #6B2B9A; }
body[data-bg="light"] .rot-c-8 { background: #A82C6C; }
.rot-strength { font-size: 10px; color: var(--text-muted); margin-top: 1px; }
.rot-charts { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 16px; }
.rot-chart-block { background: var(--bg-card); border: 1px solid var(--border-soft); border-radius: 8px; padding: 12px; }
.rot-chart-title { font-size: 12px; color: var(--text-muted); margin-bottom: 6px; }
.rot-svg { width: 100%; height: auto; }
.rot-windows { margin-top: 16px; background: var(--bg-card); border: 1px solid var(--border-soft); border-radius: 8px; padding: 12px; }
.rot-window-legend { display: flex; gap: 16px; justify-content: center; margin-top: 6px; font-size: 12px; }
.rot-window-legend i { margin-right: 4px; }
/* 移动端适配(<=768px): 表格横滑 + Tab 横滑 + 布局紧凑 */
@media (max-width: 768px) {
  .rot-charts { grid-template-columns: 1fr; }
  /* 宽表格横向滚动(板块强度/人气热榜/龙虎榜) */
  .mrk-panel { overflow-x: auto; -webkit-overflow-scrolling: touch; padding: 10px 8px; }
  .mrk-panel .stock-table { min-width: 880px; }
  /* 历史轮动表横滑内容完整 */
  .rot-table-scroll .rot-table { min-width: 680px; }
  /* Tab 横向滑动(4 个 tab 一排滑, 不换行占纵向空间) */
  .mrk-tabs { flex-wrap: nowrap; overflow-x: auto; -webkit-overflow-scrolling: touch; scrollbar-width: none; padding-bottom: 4px; }
  .mrk-tabs::-webkit-scrollbar { display: none; }
  .mrk-tab { flex-shrink: 0; white-space: nowrap; padding: 7px 12px; font-size: 13px; }
  /* 头部紧凑 */
  .mrk-head { gap: 6px; }
  .mrk-title { font-size: 17px; }
  .mrk-sub { font-size: 11px; width: 100%; }
  .mrk-time { margin-left: 0; font-size: 12px; }
  /* 表格字号压缩 */
  .mrk-panel .stock-table th { padding: 7px 4px; font-size: 11px; }
  .mrk-panel .stock-table td { padding: 6px 4px; font-size: 11px; }
  /* 龙虎榜弹窗: 买卖盘双列改单列(手机宽不足, 双列挤) */
  .lhb-cols { flex-direction: column; gap: 8px; }
  /* 涨停原因弹窗近全屏 */
  .reason-modal { width: 96vw; padding: 12px 10px; }
}

</style>
