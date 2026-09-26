<template>
  <!--
    龙虎榜面板（2026-09-27 v4.11.58）
    —— 原内嵌在 MarketView「市场雷达」的第 4 个 tab 里；因它 17 点后才有数据，
       盘中打开永远是空的，按工单 三.4「龙虎榜拆出」挪到「复盘」分组，成为独立页 /lhb。
    —— 拉数仍在 /api/kpl/lhb（kplLhb / kplLhbDetail），零后端改动。
    —— 拆成独立组件（而非留在 MarketView）后，/market 不再为它白拉一次接口。
  -->
  <div class="lhb-panel">
    <div class="rot-toolbar">
      <span class="rot-tip"><i class="fa fa-info-circle"></i> 龙虎榜当日 / 历史；选日期可回看。当日数据 17:00 后陆续披露。</span>
      <input v-model="datePicker" type="date" class="rot-date" @change="load">
      <button class="rot-reset-btn" title="回到实时" aria-label="回到实时" @click="clearDate"><i class="fa fa-bolt"></i></button>
      <button class="rot-reset-btn" title="刷新" aria-label="刷新" @click="load"><i class="fa fa-refresh"></i></button>
      <span v-if="dataDate && datePicker" class="rot-data-date">
        <i class="fa fa-calendar"></i> 数据日期 {{ dataDate }}<template v-if="dataDate !== datePicker">（{{ datePicker }} 非交易日，自动对齐）</template>
      </span>
    </div>

    <div v-if="loading" class="loading-placeholder"><div class="spinner"></div><div>加载龙虎榜...</div></div>
    <div v-else-if="!list.length" class="empty-state">暂无龙虎榜数据（当日 17:00 后陆续披露；非交易日可切换日期回看）</div>
    <div v-else class="lhb-table-scroll">
      <table class="stock-table">
        <thead>
          <tr>
            <th class="sortable" :class="{ active: sort.keyOf('code') }" @click="sort.onSort('code', 'string')">代码<span class="sort-ind">{{ sort.ind('code') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('name') }" @click="sort.onSort('name', 'string')">名称<span class="sort-ind">{{ sort.ind('name') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('change') }" @click="sort.onSort('change')">涨跌幅%<span class="sort-ind">{{ sort.ind('change') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('limitBoards') }" @click="sort.onSort('limitBoards')">连板<span class="sort-ind">{{ sort.ind('limitBoards') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('buyIn') }" @click="sort.onSort('buyIn')">买入(亿)<span class="sort-ind">{{ sort.ind('buyIn') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('amount') }" @click="sort.onSort('amount')">成交额(亿)<span class="sort-ind">{{ sort.ind('amount') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('turnover') }" @click="sort.onSort('turnover')">换手%<span class="sort-ind">{{ sort.ind('turnover') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('amplitude') }" @click="sort.onSort('amplitude')">振幅%<span class="sort-ind">{{ sort.ind('amplitude') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('floatMv') }" @click="sort.onSort('floatMv')">流通市值(亿)<span class="sort-ind">{{ sort.ind('floatMv') }}</span></th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="l in sort.sorted(list)" :key="l.code">
            <td class="code-click" @click="linkToSoftware(l.code)">{{ l.code }}</td>
            <td class="name-col"><span class="pool-hover-wrap">{{ l.name }}<PoolHoverBtn :item="l" /></span></td>
            <td :class="l.change > 0 ? 'up' : l.change < 0 ? 'down' : 'dim'">{{ signed(l.change) }}%</td>
            <td><span v-if="l.limitBoards > 0" class="lb-badge">{{ l.limitBoards }}板</span><span v-else class="dim">-</span></td>
            <td :class="l.buyIn > 0 ? 'up' : l.buyIn < 0 ? 'down' : 'dim'">{{ yi(l.buyIn) }}</td>
            <td>{{ yi(l.amount) }}</td>
            <td>{{ (l.turnover || 0).toFixed(2) }}</td>
            <td>{{ (l.amplitude || 0).toFixed(2) }}</td>
            <td>{{ yi(l.floatMv) }}</td>
            <td><button class="pool-add-btn" @click="viewDetail(l)">明细</button></td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 营业部明细弹窗 -->
    <div v-if="modal.show" class="modal-mask" @click.self="modal.show = false">
      <div class="reason-modal">
        <div class="reason-head">
          <span><i class="fa fa-list-alt lhb-head-icon"></i> {{ modal.detail.name }} {{ modal.code }} · 龙虎榜营业部</span>
          <button class="close-btn" @click="modal.show = false"><i class="fa fa-close"></i></button>
        </div>
        <div v-if="detailLoading" class="reason-loading">查询中...</div>
        <template v-else>
          <div v-if="modal.detail.upReason" class="lhb-reason">涨停原因：{{ modal.detail.upReason }}</div>
          <div class="lhb-total">
            <span>买入总计 <b class="up">{{ yi(modal.detail.buyTotal) }}亿</b></span>
            <span>卖出总计 <b class="down">{{ yi(modal.detail.sellTotal) }}亿</b></span>
            <span>换手 {{ (modal.detail.turnover || 0).toFixed(2) }}%</span>
          </div>
          <div class="lhb-cols">
            <div class="lhb-col">
              <div class="lhb-col-title buy">买入营业部</div>
              <div v-for="(b, i) in modal.detail.buyList" :key="i" class="lhb-row">
                <span class="lhb-idx">{{ i + 1 }}</span>
                <span class="lhb-name">{{ b.name }}</span>
                <span class="lhb-amt up">+{{ (b.buy / 1e8).toFixed(2) }}亿</span>
              </div>
              <div v-if="!modal.detail.buyList.length" class="lhb-empty">无</div>
            </div>
            <div class="lhb-col">
              <div class="lhb-col-title sell">卖出营业部</div>
              <div v-for="(s, i) in modal.detail.sellList" :key="i" class="lhb-row">
                <span class="lhb-idx">{{ i + 1 }}</span>
                <span class="lhb-name">{{ s.name }}</span>
                <span class="lhb-amt down">-{{ (s.sell / 1e8).toFixed(2) }}亿</span>
              </div>
              <div v-if="!modal.detail.sellList.length" class="lhb-empty">无</div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { kplLhb, kplLhbDetail } from '../api/kpl'
import { linkToSoftware } from '../utils/tdx'
import { useSortable } from '../composables/useSortable'
import { yi, signed } from '../utils/format'
import PoolHoverBtn from './PoolHoverBtn.vue'

const list = ref([])
const loading = ref(true)
const detailLoading = ref(false)
const datePicker = ref('')       // 空 = 实时（后端返回最近交易日）
const dataDate = ref('')
const sort = useSortable()

const modal = reactive({
  show: false,
  code: '',
  detail: { name: '', buyList: [], sellList: [], buyTotal: 0, sellTotal: 0, upReason: '', turnover: 0 },
})

async function load() {
  loading.value = true
  try {
    const d = await kplLhb(datePicker.value)
    list.value = d.list || []
    dataDate.value = d.date || ''
  } catch (e) { /* 静默: 保留旧值 */ } finally {
    loading.value = false
  }
}

function clearDate() {
  datePicker.value = ''
  load()
}

async function viewDetail(l) {
  modal.show = true
  modal.code = l.code
  modal.detail = { name: l.name, buyList: [], sellList: [], buyTotal: 0, sellTotal: 0, upReason: '', turnover: 0 }
  detailLoading.value = true
  try {
    const d = await kplLhbDetail(l.code)
    if (d && d.detail) modal.detail = d.detail
  } catch (e) { /* 静默 */ } finally {
    detailLoading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.lhb-table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.name-col { white-space: nowrap; }
.lb-badge {
  display: inline-block; color: #ff8a5c;
  border: 1px solid rgba(255, 80, 40, 0.5); border-radius: 4px;
  padding: 0 5px; font-size: 0.75rem; background: rgba(255, 80, 40, 0.12);
}
.lhb-head-icon { color: #ffb400; }

/* 营业部明细弹窗（自 MarketView 原样搬来，保持视觉一致） */
.modal-mask {
  position: fixed; inset: 0; background: rgba(0, 0, 0, 0.6);
  display: flex; align-items: center; justify-content: center; z-index: 1000;
}
.reason-modal {
  background: var(--bg-panel-solid); border: 1px solid var(--border-soft);
  border-radius: 12px; width: 640px; max-width: 92vw; max-height: 76vh;
  overflow: auto; padding: 18px;
}
.reason-head { display: flex; align-items: center; justify-content: space-between; color: #ffe0a0; font-size: 1rem; margin-bottom: 14px; }
.close-btn { background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: 1rem; }
.close-btn:hover { color: #ff6a6a; }
.reason-loading { color: var(--text-muted); padding: 20px; text-align: center; }
.lhb-reason { color: #ffb400; font-size: 0.8125rem; margin-bottom: 10px; line-height: 1.5; }
.lhb-total { display: flex; gap: 20px; color: #aaa; font-size: 0.8125rem; margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px solid var(--border-soft); }
.lhb-cols { display: flex; gap: 16px; }
.lhb-col { flex: 1; }
.lhb-col-title { font-size: 0.8125rem; margin-bottom: 8px; }
.lhb-col-title.buy { color: #ff8a8a; }
.lhb-col-title.sell { color: #8ae08a; }
.lhb-row { display: flex; align-items: center; gap: 6px; padding: 4px 0; font-size: 0.75rem; border-bottom: 1px solid rgba(255, 255, 255, 0.04); }
.lhb-idx { width: 16px; color: var(--text-muted); }
.lhb-name { flex: 1; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lhb-amt { font-family: inherit; }
.lhb-empty { color: #666; font-size: 0.75rem; padding: 8px 0; }

/* 浅色主题覆盖 */
body[data-bg="light"] .lb-badge { color: #b83010; border-color: rgba(184, 48, 16, 0.5); background: rgba(255, 80, 80, 0.1); }
body[data-bg="light"] .lhb-head-icon { color: #c79100; }
body[data-bg="light"] .reason-head { color: #5a4a3a; }
body[data-bg="light"] .close-btn:hover { color: #b83010; }
body[data-bg="light"] .lhb-reason { color: #8a5500; }
body[data-bg="light"] .lhb-col-title { color: #5a4a3a; }
body[data-bg="light"] .lhb-col-title.buy { color: #b83010; }
body[data-bg="light"] .lhb-name { color: #1a1d26; }
body[data-bg="light"] .lhb-row { border-bottom-color: rgba(0, 0, 0, 0.08); }
body[data-bg="light"] .lhb-empty { color: #8a8a8a; }
body[data-bg="light"] .reason-modal { background: rgba(255, 255, 255, 0.98); border-color: var(--border-soft); }

@media (max-width: 768px) {
  .stock-table { min-width: 900px; }
  .lhb-cols { flex-direction: column; gap: 8px; }
  .reason-modal { width: 96vw; padding: 12px 10px; }
}
</style>
