<template>
  <div class="lhb-panel">
    <div class="rot-toolbar">
      <span class="rot-tip"><i class="fa fa-info-circle"></i> 龙虎榜当日 / 历史；选日期可回看。当日数据 17:00 后陆续披露。</span>
      <input v-model="datePicker" type="date" class="rot-date" @change="load">
      <button class="rot-reset-btn" title="回到实时" aria-label="回到实时" @click="clearDate"><i class="fa fa-bolt"></i></button>
      <button class="rot-reset-btn" title="刷新" aria-label="刷新" @click="load"><i class="fa fa-refresh"></i></button>
    </div>

    <!-- 分类 tab -->
    <div class="lhb-tabs">
      <button v-for="t in tabs" :key="t.key" class="lhb-tab" :class="{active: curTab===t.key}" @click="curTab=t.key">
        {{ t.label }}
      </button>
    </div>

    <!-- 资金流向图 -->
    <LhbSankey v-if="curTab==='sankey'" :list="list" />

    <template v-else>
    <div v-if="loading" class="loading-placeholder"><div class="spinner"></div><div>加载龙虎榜...</div></div>
    <div v-else-if="!filteredList.length" class="empty-state">暂无数据</div>
    <div v-else class="lhb-table-scroll">
      <table class="stock-table">
        <thead>
          <tr>
            <th class="sortable" :class="{ active: sort.keyOf('code') }" @click="sort.onSort('code', 'string')">代码<span class="sort-ind">{{ sort.ind('code') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('name') }" @click="sort.onSort('name', 'string')">名称<span class="sort-ind">{{ sort.ind('name') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('change') }" @click="sort.onSort('change')">涨跌幅%<span class="sort-ind">{{ sort.ind('change') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('limitBoards') }" @click="sort.onSort('limitBoards')">连板<span class="sort-ind">{{ sort.ind('limitBoards') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('buyIn') }" @click="sort.onSort('buyIn')">净买入(亿)<span class="sort-ind">{{ sort.ind('buyIn') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('amount') }" @click="sort.onSort('amount')">成交额(亿)<span class="sort-ind">{{ sort.ind('amount') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('turnover') }" @click="sort.onSort('turnover')">换手%<span class="sort-ind">{{ sort.ind('turnover') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('floatMv') }" @click="sort.onSort('floatMv')">流通市值<span class="sort-ind">{{ sort.ind('floatMv') }}</span></th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="l in sort.sorted(filteredList)" :key="l.code">
            <td class="code-click" @click="linkToSoftware(l.code)">{{ l.code }}</td>
            <td class="name-col" @click="openDetail(l)" style="cursor:pointer">
              <span class="stock-name">{{ l.name }}</span>
              <span v-if="l.instFlag" class="tag tag-inst">机构</span>
              <span v-if="l.hotFlag" class="tag tag-hot">游资</span>
            </td>
            <td :class="l.change > 0 ? 'up' : l.change < 0 ? 'down' : 'dim'">{{ signed(l.change) }}%</td>
            <td><span v-if="l.limitBoards > 0" class="lb-badge">{{ l.limitBoards }}板</span><span v-else class="dim">-</span></td>
            <td class="buyin-cell" :class="buyinClass(l.buyIn)">{{ yi(l.buyIn) }}</td>
            <td>{{ yi(l.amount) }}</td>
            <td>{{ (l.turnover || 0).toFixed(2) }}</td>
            <td>{{ yi(l.floatMv) }}</td>
            <td><button class="pool-add-btn" @click="viewDetail(l)">明细</button></td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 个股详情弹层 -->
    <StockDetailPanel v-if="detailStock.code" :code="detailStock.code" :name="detailStock.name" @close="detailStock={code:'',name:''}" />

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
                <span class="lhb-name">{{ b.name }}<span v-if="isHotBroker(b.name)" class="tag tag-hot">热</span><span v-if="isInstBroker(b.name)" class="tag tag-inst">机构</span></span>
                <span class="lhb-amt up">+{{ (b.buy / 1e8).toFixed(2) }}亿</span>
              </div>
              <div v-if="!modal.detail.buyList.length" class="lhb-empty">无</div>
            </div>
            <div class="lhb-col">
              <div class="lhb-col-title sell">卖出营业部</div>
              <div v-for="(s, i) in modal.detail.sellList" :key="i" class="lhb-row">
                <span class="lhb-idx">{{ i + 1 }}</span>
                <span class="lhb-name">{{ s.name }}<span v-if="isHotBroker(s.name)" class="tag tag-hot">热</span><span v-if="isInstBroker(s.name)" class="tag tag-inst">机构</span></span>
                <span class="lhb-amt down">-{{ (s.sell / 1e8).toFixed(2) }}亿</span>
              </div>
              <div v-if="!modal.detail.sellList.length" class="lhb-empty">无</div>
            </div>
          </div>
        </template>
      </div>
    </div>
    </template>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref, computed } from 'vue'
import { kplLhb, kplLhbDetail } from '../api/kpl'
import { linkToSoftware } from '../utils/tdx'
import { useSortable } from '../composables/useSortable'
import { yi, signed } from '../utils/format'
import StockDetailPanel from './StockDetailPanel.vue'
import LhbSankey from './LhbSankey.vue'

const list = ref([])
const loading = ref(true)
const detailLoading = ref(false)
const datePicker = ref('')
const dataDate = ref('')
const sort = useSortable()
const curTab = ref('all')

const tabs = [
  { key: 'all', label: '净买入排行' },
      { key: 'sankey', label: '资金流向图' },
  { key: 'inst', label: '机构席位' },
  { key: 'hot', label: '知名游资' },
]

// 知名游资营业部关键词
const HOT_KEYS = ['章盟主','方新侠','炒股养家','作手新一','桑田路','呼家楼','上塘路','柯桥','解放北','溧阳路','江苏路','益田路','银河绍兴','宁波','台州','温州','深圳分公司','上海分公司','量化','机构专用']

function isHotBroker(name) {
  return HOT_KEYS.some(k => name && name.includes(k)) && !name.includes('机构专用')
}
function isInstBroker(name) {
  return name && name.includes('机构专用')
}

const filteredList = computed(() => {
  // 给每条打上标签
  const tagged = list.value.map(l => ({
    ...l,
    instFlag: l.hasInst || false,
    hotFlag: l.hasHot || false,
  }))
  if (curTab.value === 'inst') return tagged.filter(l => l.instFlag)
  if (curTab.value === 'hot') return tagged.filter(l => l.hotFlag)
  return tagged
})

function buyinClass(v) {
  if (!v) return ''
  if (v >= 5) return 'buyin-big'
  if (v >= 1) return 'buyin-mid'
  return ''
}

const modal = reactive({
  show: false,
  code: '',
  detail: { name: '', buyList: [], sellList: [], buyTotal: 0, sellTotal: 0, upReason: '', turnover: 0 },
})

const detailStock = reactive({ code: '', name: '' })

function openDetail(l) {
  detailStock.code = l.code
  detailStock.name = l.name
}

async function load() {
  loading.value = true
  try {
    const d = await kplLhb(datePicker.value)
    list.value = d.list || []
    dataDate.value = d.date || ''
  } catch (e) { } finally {
    loading.value = false
  }
}

function clearDate() { datePicker.value = ''; load() }

async function viewDetail(l) {
  modal.show = true
  modal.code = l.code
  modal.detail = { name: l.name, buyList: [], sellList: [], buyTotal: 0, sellTotal: 0, upReason: '', turnover: 0 }
  detailLoading.value = true
  try {
    const d = await kplLhbDetail(l.code)
    if (d && d.detail) modal.detail = d.detail
  } catch (e) { } finally { detailLoading.value = false }
}

onMounted(load)
</script>

<style scoped>
.lhb-tabs { display: flex; gap: 8px; margin-bottom: 12px; }
.lhb-tab {
  padding: 6px 14px; border-radius: 999px; border: 1px solid var(--border-soft);
  background: transparent; color: var(--text-secondary); cursor: pointer; font-size: 0.8125rem;
}
.lhb-tab.active { background: var(--accent); color: #fff; border-color: var(--accent); }
.lhb-table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.name-col { white-space: nowrap; }
.stock-name { font-weight: 600; }
.tag {
  display: inline-block; font-size: 0.65rem; padding: 0 4px; border-radius: 3px;
  margin-left: 4px; vertical-align: middle;
}
.tag-inst { background: rgba(255,180,0,0.15); color: #ffb400; border: 1px solid rgba(255,180,0,0.3); }
.tag-hot { background: rgba(255,80,40,0.15); color: #ff6a3c; border: 1px solid rgba(255,80,40,0.3); }
.buyin-mid { background: rgba(255,80,40,0.12); }
.buyin-big { background: rgba(255,40,20,0.25); font-weight: 700; }
.lb-badge {
  display: inline-block; color: #ff8a5c;
  border: 1px solid rgba(255, 80, 40, 0.5); border-radius: 4px;
  padding: 0 5px; font-size: 0.75rem; background: rgba(255, 80, 40, 0.12);
}
.lhb-head-icon { color: #ffb400; }
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
body[data-bg="light"] .lhb-row { border-bottom-color: rgba(0,0,0,0.08); }
@media (max-width: 768px) {
  .stock-table { min-width: 800px; }
  .lhb-cols { flex-direction: column; gap: 8px; }
  .reason-modal { width: 96vw; padding: 12px 10px; }
}
</style>
