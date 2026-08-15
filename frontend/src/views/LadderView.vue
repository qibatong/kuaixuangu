<template>
  <div class="page-shell">
    <div class="page-back" @click="$router.push('/')"><i class="fa fa-arrow-left"></i> 返回选股</div>

    <div class="ladder-head">
      <span class="ladder-title"><i class="fa fa-sitemap"></i> 连板天梯</span>
      <span class="ladder-sub">实时连板梯队（盘中持续刷新）</span>
      <span class="ladder-time">{{ bjTime }}</span>
    </div>

    <!-- 日期选择: 回看历史连板梯队 -->
    <div class="ladder-toolbar">
      <span class="ladder-tip"><i class="fa fa-info-circle"></i> 实时连板梯队；选日期可回看历史(每日 15:30 落库)</span>
      <input v-model="datePicker" type="date" class="rot-date" @change="load">
      <button class="rot-reset-btn" title="回到实时" @click="clearDate"><i class="fa fa-bolt"></i></button>
      <span v-if="dataDate && datePicker" class="rot-data-date"><i class="fa fa-calendar"></i> 数据日期 {{ dataDate }}<template v-if="dataDate !== datePicker">（{{ datePicker }} 非交易日，自动对齐）</template></span>
    </div>

    <!-- 梯队 Tab -->
    <div class="ladder-tabs">
      <button
v-for="pid in [1, 2, 3, 4, 5]" :key="pid" class="ladder-tab"
        :class="{ active: active === pid }" @click="active = pid"
>
        {{ labelOf(pid) }} <span class="tab-count">{{ (ladder[pid] || []).length }}</span>
      </button>
    </div>

    <div class="ladder-panel">
      <div v-if="loading" class="loading-placeholder"><div class="spinner"></div><div>加载连板梯队...</div></div>

      <div v-else-if="(ladder[active] || []).length === 0" class="empty-state">
        {{ emptyText }}
      </div>

      <table v-else class="stock-table">
        <thead>
          <tr>
            <th>排名</th>
            <th class="sortable" :class="{ active: ladderSort.keyOf('code') }" @click="ladderSort.onSort('code', 'string')">代码<span class="sort-ind">{{ ladderSort.ind('code') }}</span></th>
            <th class="sortable" :class="{ active: ladderSort.keyOf('name') }" @click="ladderSort.onSort('name', 'string')">名称<span class="sort-ind">{{ ladderSort.ind('name') }}</span></th>
            <th class="sortable" :class="{ active: ladderSort.keyOf('limitTime') }" @click="ladderSort.onSort('limitTime')">涨停时间<span class="sort-ind">{{ ladderSort.ind('limitTime') }}</span></th>
            <th class="sortable" :class="{ active: ladderSort.keyOf('reason') }" @click="ladderSort.onSort('reason', 'string')">涨停原因<span class="sort-ind">{{ ladderSort.ind('reason') }}</span></th>
            <th class="sortable" :class="{ active: ladderSort.keyOf('seal') }" @click="ladderSort.onSort('seal')">封单(亿)<span class="sort-ind">{{ ladderSort.ind('seal') }}</span></th>
            <th class="sortable" :class="{ active: ladderSort.keyOf('mainNet') }" @click="ladderSort.onSort('mainNet')">主力净额(亿)<span class="sort-ind">{{ ladderSort.ind('mainNet') }}</span></th>
            <th class="sortable" :class="{ active: ladderSort.keyOf('turnover') }" @click="ladderSort.onSort('turnover')">换手%<span class="sort-ind">{{ ladderSort.ind('turnover') }}</span></th>
            <th class="sortable" :class="{ active: ladderSort.keyOf('amplitude') }" @click="ladderSort.onSort('amplitude')">振幅%<span class="sort-ind">{{ ladderSort.ind('amplitude') }}</span></th>
            <th class="sortable" :class="{ active: ladderSort.keyOf('boardName') }" @click="ladderSort.onSort('boardName', 'string')">板块<span class="sort-ind">{{ ladderSort.ind('boardName') }}</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(it, idx) in ladderSort.sorted(ladder[active] || [])" :key="it.code">
            <td class="rank-col">{{ idx + 1 }}</td>
            <td class="code-click" @click="linkToSoftware(it.code)">{{ it.code }}</td>
            <td class="name-col"><div class="name-main">{{ it.name }}</div></td>
            <td class="dim">{{ fmtLimitTime(it.limitTime) }}</td>
            <td style="max-width:260px;white-space:pre-wrap;font-size:12px;">
              <span v-if="it.reason">{{ it.reason }}</span>
              <span v-else class="reason-link" @click="viewReason(it)"><i class="fa fa-search"></i> 查原因</span>
            </td>
            <td :class="it.seal > 0 ? 'up' : 'dim'">{{ yi(it.seal) }}</td>
            <td :class="it.mainNet > 0 ? 'up' : it.mainNet < 0 ? 'down' : 'dim'">{{ yi(it.mainNet) }}</td>
            <td>{{ it.turnover.toFixed(2) }}</td>
            <td>{{ it.amplitude.toFixed(2) }}</td>
            <td class="dim">{{ it.boardName || it.concept }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 涨停原因弹窗 -->
    <div v-if="reasonModal.show" class="modal-mask" @click.self="reasonModal.show = false">
      <div class="reason-modal">
        <div class="reason-head">
          <span><i class="fa fa-fire" style="color:#ff5028;"></i> {{ reasonModal.name }} {{ reasonModal.code }} · 涨停原因</span>
          <button class="close-btn" @click="reasonModal.show = false"><i class="fa fa-close"></i></button>
        </div>
        <div v-if="reasonLoading" class="reason-loading">查询中...</div>
        <template v-else-if="reasonModal.list.length">
          <div v-for="(r, i) in reasonModal.list" :key="i" class="reason-item">
            <div class="reason-date">{{ r.date }} <span v-if="r.sclt" class="sclt">{{ r.sclt }}</span></div>
            <div class="reason-text">{{ r.reason }}</div>
            <div v-if="r.boom" class="reason-boom">{{ r.boom }}</div>
          </div>
        </template>
        <div v-else class="reason-loading">暂无涨停原因记录</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref, reactive } from 'vue'
import { usePolling } from '../composables/usePolling'
import { kplLadder, kplZtReason } from '../api/kpl'
import { linkToSoftware } from '../utils/tdx'
import { bjTimeStr } from '../utils/time'
import { useSortable } from '../composables/useSortable'

const ladder = ref({})
const active = ref(1)
const loading = ref(true)
const bjTime = ref('--:--:--')
const datePicker = ref('')
const dataDate = ref('')

const ladderSort = useSortable()

const reasonModal = reactive({ show: false, code: '', name: '', list: [] })
const reasonLoading = ref(false)

const LABELS = { 1: '首板', 2: '二板', 3: '三板', 4: '四板', 5: '五板+' }
function labelOf(pid) { return LABELS[pid] || pid + '板' }

const emptyText = '当前时段暂无该梯队涨停股（开盘后刷新）'

function yi(v) { return (v / 1e8).toFixed(2) }

function fmtLimitTime(ts) {
  if (!ts) return '-'
  const d = new Date((ts + 8 * 3600) * 1000)
  return d.toISOString().slice(11, 16)
}

async function viewReason(it) {
  reasonModal.show = true
  reasonModal.code = it.code
  reasonModal.name = it.name
  reasonModal.list = []
  reasonLoading.value = true
  try {
    const d = await kplZtReason(it.code)
    reasonModal.list = d.reason || []
  } catch (e) {
    reasonModal.list = []
  } finally {
    reasonLoading.value = false
  }
}

async function load() {
  try {
    const d = await kplLadder(datePicker.value)
    if (d && d.ladder) ladder.value = d.ladder
    dataDate.value = d.date || ''
  } catch (e) { /* 静默 */ } finally {
    loading.value = false
  }
}

function clearDate() {
  datePicker.value = ''
  loading.value = true
  load()
}

onMounted(() => {
  bjTime.value = bjTimeStr()
  usePolling(() => { bjTime.value = bjTimeStr() }, 1000, { immediate: false })
  load()
  usePolling(load, 60000)  // 每分钟刷新
})
</script>

<style scoped>
.page-back { color: var(--text-muted); cursor: pointer; font-size: 13px; margin-bottom: 12px; display: inline-block; }
.page-back:hover { color: #ffb400; }
.ladder-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.ladder-toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
.ladder-tip { color: var(--text-muted, #aaa); font-size: 12px; flex: 1; min-width: 0; }
.ladder-title { font-size: 20px; font-weight: 700; color: #ffe0a0; }
.ladder-title .fa { color: #ffb400; }
.ladder-sub { color: var(--text-muted); font-size: 13px; }
.ladder-time { margin-left: auto; color: #aaa; font-size: 14px; font-family: monospace; }
.ladder-tabs { display: flex; gap: 8px; margin-bottom: 14px; flex-wrap: wrap; }
.ladder-tab {
  padding: 8px 18px; border-radius: 8px; border: 1px solid var(--border-soft);
  background: var(--bg-hover); color: var(--text-secondary); font-size: 14px; cursor: pointer;
  transition: all 0.2s;
}
.ladder-tab:hover { border-color: #ffb400; color: #ffe0a0; }
.ladder-tab.active { background: rgba(var(--accent-rgb), 0.15); border-color: var(--accent-deep); color: var(--accent); font-weight: 600; }
.tab-count { font-size: 12px; color: #ffb400; }
.ladder-panel { background: var(--bg-hover); border: 1px solid var(--border-soft); border-radius: 10px; padding: 14px; }
.loading-placeholder { text-align: center; padding: 40px; color: var(--text-muted); }
.spinner { width: 28px; height: 28px; border: 3px solid rgba(255,180,0,0.3); border-top-color: #ffb400; border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 10px; }
@keyframes spin { to { transform: rotate(360deg); } }
.empty-state { text-align: center; padding: 40px; color: var(--text-muted); }
.reason-link { color: #ffb400; cursor: pointer; font-size: 12px; }
.reason-link:hover { text-decoration: underline; }
.modal-mask {
  position: fixed; inset: 0; background: rgba(0,0,0,0.6);
  display: flex; align-items: center; justify-content: center; z-index: 1000;
}
.reason-modal {
  background: var(--bg-panel-solid); border: 1px solid var(--border-soft);
  border-radius: 12px; width: 560px; max-width: 92vw; max-height: 70vh;
  overflow: auto; padding: 18px;
}
.reason-head { display: flex; align-items: center; justify-content: space-between; color: #ffe0a0; font-size: 16px; margin-bottom: 14px; }
.close-btn { background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: 16px; }
.close-btn:hover { color: #ff6a6a; }
.reason-loading { color: var(--text-muted); padding: 20px; text-align: center; }
.reason-item { padding: 10px 0; border-bottom: 1px solid var(--border-soft); }
.reason-date { color: #ffb400; font-size: 13px; margin-bottom: 6px; }
.sclt { display: inline-block; margin-left: 8px; color: var(--accent-deep); font-size: 12px; border: 1px solid rgba(var(--accent-rgb), 0.5); border-radius: 4px; padding: 0 6px; }
.reason-text { color: var(--text-secondary); font-size: 13px; line-height: 1.6; white-space: pre-wrap; }
.reason-boom { color: #9ab; font-size: 12px; margin-top: 6px; }

/* 浅色主题覆盖 */
body[data-bg="light"] .ladder-title {  color: #8a5500;  }
body[data-bg="light"] .ladder-title .fa {  color: #c79100;  }
body[data-bg="light"] .ladder-tab {  color: #5a6b85;  }
body[data-bg="light"] .ladder-tab:hover {  color: #5a4a3a; border-color: #c79100;  }
body[data-bg="light"] .ladder-tab.active {  color: #5a4a3a; background: rgba(255,180,0,0.15); border-color: #c79100;  }
body[data-bg="light"] .tab-count {  color: #8a5500;  }
body[data-bg="light"] .reason-link {  color: #8a5500;  }
body[data-bg="light"] .reason-head {  color: #5a4a3a;  }
body[data-bg="light"] .close-btn:hover {  color: #b83010;  }
body[data-bg="light"] .reason-date {  color: #8a5500;  }
body[data-bg="light"] .sclt {  color: #b83010; border-color: rgba(184,48,16,0.5);  }
body[data-bg="light"] .reason-modal {  background: rgba(255,255,255,0.98); border-color: var(--border-soft);  }
body[data-bg="light"] .page-back { color: #5a6b85; }
body[data-bg="light"] .page-back:hover { color: #c79100; }
body[data-bg="light"] .ladder-panel { background: rgba(255,255,255,0.85); border-color: var(--border-soft); }
body[data-bg="light"] .ladder-row { border-bottom-color: rgba(0,0,0,0.08); }

/* 移动端: 宽表格横向滚动 */
@media (max-width: 768px) {
  .ladder-panel { overflow-x: auto; -webkit-overflow-scrolling: touch; padding: 10px 8px; }
  .ladder-panel .stock-table { min-width: 880px; }
}
</style>
