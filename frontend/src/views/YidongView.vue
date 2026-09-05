<template>
  <div class="page-shell">
    <div class="yd-head">
      <span class="yd-title"><i class="fa fa-bullhorn"></i> 异动监管</span>
      <span class="yd-sub">严重异动 · 热门股偏离值 · 重点监控 · 多次异动</span>
      <span class="yd-time">{{ bjTime }}</span>
    </div>

    <div class="yd-tabs">
      <button class="yd-tab" :class="{ active: tab === 'realtime' }" @click="switchTab('realtime')">
        <i class="fa fa-bolt"></i> 严重异动
      </button>
      <button class="yd-tab" :class="{ active: tab === 'hot' }" @click="switchTab('hot')">
        <i class="fa fa-flame"></i> 热门股偏离值
      </button>
      <button class="yd-tab" :class="{ active: tab === 'monitor' }" @click="switchTab('monitor')">
        <i class="fa fa-eye"></i> 重点监控
      </button>
      <button class="yd-tab" :class="{ active: tab === 'multi' }" @click="switchTab('multi')">
        <i class="fa fa-retweet"></i> 多次异动
      </button>
    </div>

    <!-- 异动实时 -->
    <div v-if="tab === 'realtime'" class="yd-panel">
      <div class="yd-toolbar">
        <span class="yd-tip"><i class="fa fa-info-circle"></i> 全市场严重异动个股（盘中持续刷新）</span>
        <span v-if="rtManyNum" class="yd-badge">异动家数: {{ rtManyNum }}</span>
        <button class="rot-reset-btn" title="刷新" @click="loadRealtime"><i class="fa fa-refresh"></i></button>
      </div>
      <div v-if="rtLoading" class="loading-placeholder"><div class="spinner"></div><div>加载严重异动...</div></div>
      <div v-else-if="!rtList.length" class="empty-state">暂无严重异动数据</div>
      <table v-else class="stock-table">
        <thead>
          <tr>
            <th>排名</th>
            <th class="sortable merged-col" :class="{ active: rtSort.keyOf('code') || rtSort.keyOf('name') }" @click="rtSort.onSort('code', 'string')">名称<span class="sort-ind">{{ rtSort.ind('code') }}</span></th>
            <th class="sortable" :class="{ active: rtSort.keyOf('type') }" @click="rtSort.onSort('type', 'string')">异动类型<span class="sort-ind">{{ rtSort.ind('type') }}</span></th>
            <th class="sortable" :class="{ active: rtSort.keyOf('deviation') }" @click="rtSort.onSort('deviation', 'number')">偏离值<span class="sort-ind">{{ rtSort.ind('deviation') }}</span></th>
            <th>触发条件</th>
            <th>是否触发</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(item, idx) in rtSort.sorted(rtList)" :key="item.code">
            <td class="rank-col">{{ idx + 1 }}</td>
            <td class="stock-info-cell" @click="linkToSoftware(item.code)">
              <div class="stock-name-row"><span class="stock-name">{{ item.name }}</span></div>
              <div class="stock-code-row"><span class="stock-code">{{ item.code }}</span></div>
            </td>
            <td class="type-col">{{ item.type }}</td>
            <td class="dev-col">
              <span v-if="item.deviation != null" class="dev-num">{{ fmtNum(item.deviation) }}%</span>
              <span v-if="item.days" class="dev-days">{{ item.days }}日</span>
              <span v-else-if="item.deviation == null">-</span>
            </td>
            <td class="trigger-col">{{ item.trigger }}</td>
            <td>
              <span class="trigger-status" :class="{ triggered: isTriggered(item.triggered) }">{{ item.triggered }}</span>
            </td>
            <td>
              <button class="pool-add-btn" :class="{ added: inPool(item.code) }" @click="addToPool(item)">
                {{ inPool(item.code) ? '已＋' : '＋自选' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 热门股偏离值 -->
    <div v-else-if="tab === 'hot'" class="yd-panel">
      <div class="yd-toolbar">
        <span class="yd-tip"><i class="fa fa-info-circle"></i> 热门股偏离值（热度严重异常，近10日/30日累计偏离超阈值）</span>
        <button class="rot-reset-btn" title="刷新" @click="loadHot"><i class="fa fa-refresh"></i></button>
      </div>
      <div v-if="hotLoading" class="loading-placeholder"><div class="spinner"></div><div>加载热门股偏离值...</div></div>
      <div v-else-if="!hotList.length" class="empty-state">暂无热门股偏离值数据</div>
      <table v-else class="stock-table">
        <thead>
          <tr>
            <th>排名</th>
            <th class="sortable merged-col" :class="{ active: hotSort.keyOf('code') || hotSort.keyOf('name') }" @click="hotSort.onSort('code', 'string')">名称<span class="sort-ind">{{ hotSort.ind('code') }}</span></th>
            <th class="sortable" :class="{ active: hotSort.keyOf('type') }" @click="hotSort.onSort('type', 'string')">偏离类型<span class="sort-ind">{{ hotSort.ind('type') }}</span></th>
            <th class="sortable" :class="{ active: hotSort.keyOf('deviation') }" @click="hotSort.onSort('deviation', 'number')">偏离值<span class="sort-ind">{{ hotSort.ind('deviation') }}</span></th>
            <th class="sortable" :class="{ active: hotSort.keyOf('change') }" @click="hotSort.onSort('change', 'number')">今日涨跌<span class="sort-ind">{{ hotSort.ind('change') }}</span></th>
            <th>连板/标签</th>
            <th class="sortable" :class="{ active: hotSort.keyOf('days') }" @click="hotSort.onSort('days', 'string')">偏离天数<span class="sort-ind">{{ hotSort.ind('days') }}</span></th>
            <th>概念</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(item, idx) in hotSort.sorted(hotList)" :key="item.code">
            <td class="rank-col">{{ idx + 1 }}</td>
            <td class="stock-info-cell" @click="linkToSoftware(item.code)">
              <div class="stock-name-row"><span class="stock-name">{{ item.name }}</span></div>
              <div class="stock-code-row"><span class="stock-code">{{ item.code }}</span></div>
            </td>
            <td class="type-col">{{ item.type }}</td>
            <td class="dev-col"><span class="dev-num">{{ fmtNum(item.deviation) }}%</span></td>
            <td>
              <span class="chg" :class="chgCls(item.change)">{{ fmtSign(item.change) }}</span>
            </td>
            <td><span v-if="item.flag" class="lb-badge">{{ item.flag }}</span></td>
            <td>{{ item.days }}</td>
            <td class="concept-col">{{ item.concept }}</td>
            <td>
              <button class="pool-add-btn" :class="{ added: inPool(item.code) }" @click="addToPool(item)">
                {{ inPool(item.code) ? '已＋' : '＋自选' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 重点监控 -->
    <div v-else-if="tab === 'monitor'" class="yd-panel">
      <div class="yd-toolbar">
        <span class="yd-tip"><i class="fa fa-info-circle"></i> 当日重点监控股票列表</span>
        <button class="rot-reset-btn" title="刷新" @click="loadMonitor"><i class="fa fa-refresh"></i></button>
      </div>
      <div v-if="monLoading" class="loading-placeholder"><div class="spinner"></div><div>加载重点监控...</div></div>
      <div v-else-if="!monList.length" class="empty-state">暂无重点监控数据</div>
      <table v-else class="stock-table">
        <thead>
          <tr>
            <th>排名</th>
            <th class="sortable merged-col" :class="{ active: monSort.keyOf('code') || monSort.keyOf('name') }" @click="monSort.onSort('code', 'string')">名称<span class="sort-ind">{{ monSort.ind('code') }}</span></th>
            <th class="sortable" :class="{ active: monSort.keyOf('startDate') }" @click="monSort.onSort('startDate', 'string')">开始日期<span class="sort-ind">{{ monSort.ind('startDate') }}</span></th>
            <th class="sortable" :class="{ active: monSort.keyOf('endDate') }" @click="monSort.onSort('endDate', 'string')">结束日期<span class="sort-ind">{{ monSort.ind('endDate') }}</span></th>
            <th class="sortable" :class="{ active: monSort.keyOf('times') }" @click="monSort.onSort('times')">次数<span class="sort-ind">{{ monSort.ind('times') }}</span></th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(item, idx) in monSort.sorted(monList)" :key="item.code">
            <td class="rank-col">{{ idx + 1 }}</td>
            <td class="stock-info-cell" @click="linkToSoftware(item.code)">
              <div class="stock-name-row"><span class="stock-name">{{ item.name }}</span></div>
              <div class="stock-code-row"><span class="stock-code">{{ item.code }}</span></div>
            </td>
            <td>{{ item.startDate }}</td>
            <td>{{ item.endDate }}</td>
            <td><span class="lb-badge">{{ item.times }}次</span></td>
            <td>
              <button class="pool-add-btn" :class="{ added: inPool(item.code) }" @click="addToPool(item)">
                {{ inPool(item.code) ? '已＋' : '＋自选' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 多次异动 -->
    <div v-else class="yd-panel">
      <div class="yd-toolbar">
        <span class="yd-tip"><i class="fa fa-info-circle"></i> 近10日内多次异动个股</span>
        <button class="rot-reset-btn" title="刷新" @click="loadMulti"><i class="fa fa-refresh"></i></button>
      </div>
      <div v-if="mulLoading" class="loading-placeholder"><div class="spinner"></div><div>加载多次异动...</div></div>
      <div v-else-if="!mulList.length" class="empty-state">暂无多次异动数据</div>
      <table v-else class="stock-table">
        <thead>
          <tr>
            <th>排名</th>
            <th class="sortable merged-col" :class="{ active: mulSort.keyOf('code') || mulSort.keyOf('name') }" @click="mulSort.onSort('code', 'string')">名称<span class="sort-ind">{{ mulSort.ind('code') }}</span></th>
            <th class="sortable" :class="{ active: mulSort.keyOf('times') }" @click="mulSort.onSort('times')">异动次数<span class="sort-ind">{{ mulSort.ind('times') }}</span></th>
            <th>异动描述</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(item, idx) in mulSort.sorted(mulList)" :key="item.code">
            <td class="rank-col">{{ idx + 1 }}</td>
            <td class="stock-info-cell" @click="linkToSoftware(item.code)">
              <div class="stock-name-row"><span class="stock-name">{{ item.name }}</span></div>
              <div class="stock-code-row"><span class="stock-code">{{ item.code }}</span></div>
            </td>
            <td><span class="lb-badge">{{ item.times }}次</span></td>
            <td class="desc-col">{{ item.desc }}</td>
            <td>
              <button class="pool-add-btn" :class="{ added: inPool(item.code) }" @click="addToPool(item)">
                {{ inPool(item.code) ? '已＋' : '＋自选' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { usePolling } from '../composables/usePolling'
import { useSortable } from '../composables/useSortable'
import { kplYidongRealtime, kplYidongHot, kplYidongMonitor, kplYidongMulti } from '../api/kpl'
import { linkToSoftware } from '../utils/tdx'
import { bjTimeStr } from '../utils/time'
import { usePoolStore } from '../stores/pool'
import { showToast } from '../utils/toast'

const pool = usePoolStore()
const tab = ref('realtime')
const bjTime = ref('--:--:--')

// 热门股偏离值
const hotList = ref([])
const hotLoading = ref(true)
const hotSort = useSortable()

// 异动实时
const rtList = ref([])
const rtLoading = ref(true)
const rtManyNum = ref(0)
const rtSort = useSortable()

// 重点监控
const monList = ref([])
const monLoading = ref(true)
const monSort = useSortable()

// 多次异动
const mulList = ref([])
const mulLoading = ref(true)
const mulSort = useSortable()

function switchTab(t) {
  tab.value = t
  rtSort.clear(); hotSort.clear(); monSort.clear(); mulSort.clear()
}

function isTriggered(status) {
  // 判断是否已触发异动
  return status && (status.includes('已触发') || status.includes('已停牌'))
}

function addToPool(item) {
  const n = pool.addStocks([{ code: item.code, name: item.name }])
  showToast(n ? `✅ ${item.code} ${item.name} 已加入自选` : `${item.code} 已在池中`, n ? 'success' : 'info')
}

function inPool(code) {
  return pool.stockPool.some(x => x.code === code)
}

function fmtNum(v) {
  if (v == null) return ''
  const n = Number(v)
  if (Number.isNaN(n)) return ''
  return Math.round(n * 100) / 100
}

function fmtSign(v) {
  if (v == null || v === '') return '-'
  const n = Number(v)
  if (Number.isNaN(n)) return '-'
  return (n > 0 ? '+' : '') + (Math.round(n * 100) / 100) + '%'
}

function chgCls(v) {
  const n = Number(v)
  if (Number.isNaN(n)) return ''
  return n > 0 ? 'chg-up' : (n < 0 ? 'chg-down' : '')
}

async function loadRealtime() {
  rtLoading.value = true
  try {
    const d = await kplYidongRealtime()
    rtList.value = d.list || []
    // 异动家数: 使用列表实际条数, 与下方表格保持一致
    rtManyNum.value = (rtList.value.length) || 0
  } catch (e) { rtList.value = [] }
  finally { rtLoading.value = false }
}

async function loadHot() {
  hotLoading.value = true
  try {
    const d = await kplYidongHot()
    // 默认按偏离值从大到小排列
    hotList.value = (d.list || []).sort((a, b) => (Number(b.deviation) || 0) - (Number(a.deviation) || 0))
  } catch (e) { hotList.value = [] }
  finally { hotLoading.value = false }
}

async function loadMonitor() {
  monLoading.value = true
  try {
    const d = await kplYidongMonitor()
    monList.value = d.list || []
  } catch (e) { monList.value = [] }
  finally { monLoading.value = false }
}

async function loadMulti() {
  mulLoading.value = true
  try {
    const d = await kplYidongMulti()
    mulList.value = d.list || []
  } catch (e) { mulList.value = [] }
  finally { mulLoading.value = false }
}

onMounted(() => {
  bjTime.value = bjTimeStr()
  usePolling(() => { bjTime.value = bjTimeStr() }, 1000, { immediate: false })
  loadRealtime()
  loadHot()
  loadMonitor()
  loadMulti()
  // 每30秒轮询刷新
  usePolling(() => { loadRealtime(); loadHot(); loadMonitor(); loadMulti() }, 30000)
})
</script>

<style scoped>
.yd-head {
  display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 12px;
}
.yd-title { font-size: 20px; font-weight: 700; color: #ffe0a0; }
.yd-title .fa { color: #ffb400; }
.yd-sub { color: var(--text-muted); font-size: 13px; }
.yd-time { margin-left: auto; color: #aaa; font-size: 14px; font-family: "LXGW WenKai Mono", monospace; }

.yd-tabs { display: flex; gap: 8px; margin-bottom: 14px; }
.yd-tab {
  padding: 8px 18px; border-radius: 8px; border: 1px solid var(--border-soft);
  background: var(--bg-hover); color: var(--text-secondary); font-size: 14px;
  cursor: pointer; transition: all 0.2s;
}
.yd-tab:hover { border-color: #ffb400; color: #ffe0a0; }
.yd-tab.active {
  background: rgba(255, 180, 0, 0.15); border-color: #ffb400;
  color: #ffd700; font-weight: 600;
}

.yd-panel {
  background: var(--bg-hover); border: 1px solid var(--border-soft);
  border-radius: 10px; padding: 14px;
}

.yd-toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
.yd-tip { color: var(--text-muted); font-size: 12px; flex: 1; min-width: 0; }
.yd-badge {
  display: inline-block; padding: 2px 10px; border-radius: 12px;
  background: rgba(255, 180, 0, 0.12); color: #ffb400;
  font-size: 12px; font-weight: 600;
}

.desc-col { max-width: 300px; }
.concept-col { max-width: 240px; color: #9cf; }

.chg { font-weight: 600; font-size: 13px; }
.chg.chg-up { color: #ff4d4f; }
.chg.chg-down { color: #33cc77; }

.type-col { max-width: 200px; color: #ffd700; white-space: normal; word-break: break-word; overflow-wrap: anywhere; }
.trigger-col { max-width: 180px; color: #aaa; font-size: 12px; white-space: normal; word-break: break-word; overflow-wrap: anywhere; }

.dev-col { text-align: right; white-space: nowrap; }
.dev-col .dev-num { color: #ff4d4f; font-weight: 600; font-size: 13px; }
.dev-col .dev-days { color: #999; font-size: 11px; margin-left: 4px; }

.trigger-status {
  display: inline-block; padding: 2px 8px; border-radius: 4px;
  font-size: 12px; font-weight: 500;
  color: #666; background: rgba(255,255,255,0.05);
  white-space: nowrap;
}
.trigger-status.triggered {
  color: #ff4d4f; background: rgba(255, 77, 79, 0.15);
  border: 1px solid rgba(255, 77, 79, 0.4);
}

/* 表格单元格居中对齐 */
.yd-panel .stock-table th,
.yd-panel .stock-table td {
  vertical-align: middle !important;
  text-align: center !important;
}

/* 合并列: 代码+名称 上下排布
   关键: td 必须是 table-cell + vertical-align:middle 才能自动撑满整行高度,
        不能设 display:flex(flex 高度由自身内容决定, 不会跟随行高, 导致偏上) */
.yd-panel .stock-table td.stock-info-cell {
  cursor: pointer;
  min-width: 80px !important;
  padding: 6px 4px !important;
  display: table-cell !important;
  vertical-align: middle !important;
  text-align: center !important;
}
.stock-info-cell .stock-name-row {
  display: block !important;
  line-height: 1.4 !important;
  text-align: center !important;
}
.stock-info-cell .stock-name {
  font-weight: 600 !important;
  color: var(--text-main);
  font-size: 13px !important;
}
.stock-info-cell .stock-code-row {
  display: block !important;
  line-height: 1.2 !important;
  text-align: center !important;
  margin-top: 2px !important;
}
.stock-info-cell .stock-code {
  font-family: "LXGW WenKai Mono", monospace;
  font-size: 11px !important;
  color: var(--text-muted);
  letter-spacing: 0.5px !important;
}
.stock-info-cell:hover .stock-name,
.stock-info-cell:hover .stock-code {
  color: var(--accent);
}

/* 媒体查询: 覆盖全局样式在小屏幕上的限制 */
@media (max-width: 768px) {
  .yd-panel .stock-table td.stock-info-cell { min-width: 70px !important; padding: 5px 3px !important; }
  .stock-info-cell .stock-name { font-size: 12px !important; }
  .stock-info-cell .stock-code { font-size: 10px !important; }
}
@media (max-width: 480px) {
  .yd-panel .stock-table td.stock-info-cell { min-width: 64px !important; padding: 4px 2px !important; }
  .stock-info-cell .stock-name { font-size: 11.5px !important; }
  .stock-info-cell .stock-code { font-size: 9.5px !important; }
}

.loading-placeholder { text-align: center; padding: 40px; color: var(--text-muted); }
.spinner {
  width: 28px; height: 28px; border: 3px solid rgba(255, 180, 0, 0.3);
  border-top-color: #ffb400; border-radius: 50%;
  animation: spin 0.8s linear infinite; margin: 0 auto 10px;
}
@keyframes spin { to { transform: rotate(360deg); } }
.empty-state { text-align: center; padding: 40px; color: var(--text-muted); }

.lb-badge {
  display: inline-block; color: #ff8a5c; border: 1px solid rgba(255, 80, 40, 0.5);
  border-radius: 4px; padding: 0 5px; font-size: 11px;
  background: rgba(255, 80, 40, 0.12);
}

/* 浅色主题覆盖 */
body[data-bg="light"] .yd-title { color: #8a5500; }
body[data-bg="light"] .yd-title .fa { color: #c79100; }
body[data-bg="light"] .yd-sub { color: #6b6b6b; }
body[data-bg="light"] .yd-time { color: #6b6b6b; }
body[data-bg="light"] .yd-tab { color: #6b6b6b; border-color: var(--border-soft); background: rgba(255, 255, 255, 0.6); }
body[data-bg="light"] .yd-tab:hover { color: #5a4a3a; border-color: #c79100; }
body[data-bg="light"] .yd-tab.active { color: #5a4a3a; background: rgba(255, 180, 0, 0.15); border-color: #c79100; }
body[data-bg="light"] .yd-panel { background: rgba(255, 255, 255, 0.85); border-color: var(--border-soft); }
body[data-bg="light"] .lb-badge { color: #b83010; border-color: rgba(184, 48, 16, 0.5); background: rgba(255, 80, 80, 0.1); }

/* 移动端适配 */
@media (max-width: 768px) {
  .yd-panel { overflow-x: auto; -webkit-overflow-scrolling: touch; padding: 10px 8px; }
  .yd-panel .stock-table { min-width: 680px; }
  .yd-tabs { flex-wrap: nowrap; overflow-x: auto; -webkit-overflow-scrolling: touch; scrollbar-width: none; padding-bottom: 4px; }
  .yd-tabs::-webkit-scrollbar { display: none; }
  .yd-tab { flex-shrink: 0; white-space: nowrap; padding: 7px 12px; font-size: 13px; }
  .yd-head { gap: 6px; }
  .yd-title { font-size: 17px; }
  .yd-sub { font-size: 11px; width: 100%; }
  .yd-time { margin-left: 0; font-size: 12px; }
  .yd-panel .stock-table th { padding: 7px 4px; font-size: 11px; }
  .yd-panel .stock-table td { padding: 6px 4px; font-size: 11px; }
  .desc-col { max-width: 180px; }
}
</style>
