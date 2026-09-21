<template>
  <div class="page-shell">
    <h1 class="visually-hidden">题材异动</h1>
    <div class="cpt-head">
      <span class="cpt-title"><i class="fa fa-fire"></i> 题材异动</span>
      <span class="cpt-sub">猫爪精选板块异动榜 · 点击左侧板块查看成分股</span>
      <span class="cpt-time">{{ bjTime }}</span>
    </div>

    <div class="cpt-layout">
      <!-- 左栏: 精选板块异动榜(猫爪 themedaily_jx 一级板块) -->
      <div class="cpt-left">
        <div class="cpt-panel-head">
          <span class="cpt-panel-title">精选板块</span>
          <span class="cpt-panel-count">{{ conceptList.length }} 个</span>
        </div>
        <div v-if="conceptLoading" class="loading-placeholder"><div class="spinner"></div><div>加载板块...</div></div>
        <div v-else-if="!conceptList.length" class="empty-state">暂无板块数据（非交易时段接口可能暂不可用）</div>
        <table v-else class="stock-table cpt-table">
          <thead>
            <tr>
              <th>#</th>
              <th class="sortable" :class="{ active: sortKey === 'name' }" @click="toggleSort('name')">板块</th>
              <th class="sortable" :class="{ active: sortKey === 'change' }" @click="toggleSort('change')">涨幅%<span class="sort-ind">{{ sortInd('change') }}</span></th>
              <th class="sortable" :class="{ active: sortKey === 'speed' }" @click="toggleSort('speed')">涨速%<span class="sort-ind">{{ sortInd('speed') }}</span></th>
              <th class="sortable" :class="{ active: sortKey === 'mainNet' }" @click="toggleSort('mainNet')">主力净额(亿)<span class="sort-ind">{{ sortInd('mainNet') }}</span></th>
              <th class="sortable" :class="{ active: sortKey === 'strength' }" @click="toggleSort('strength')">强度<span class="sort-ind">{{ sortInd('strength') }}</span></th>
              <th class="sortable" :class="{ active: sortKey === 'volRatio' }" @click="toggleSort('volRatio')">量比<span class="sort-ind">{{ sortInd('volRatio') }}</span></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(b, idx) in sortedConcepts" :key="b.boardCode"
                class="cpt-row" :class="{ active: currentBoard?.boardCode === b.boardCode }"
                @click="selectBoard(b)">
              <td class="rank-col">{{ idx + 1 }}</td>
              <td class="name-col">
                <div class="name-main">
                  {{ b.name }}
                  <span v-if="b.aucGroup" class="auc-badge" :class="aucBadgeClass(b.aucGroup)">{{ aucBadgeText(b.aucGroup) }}</span>
                </div>
                <div class="cpt-subinfo">
                  <span>{{ b.boardCode }}</span>
                  <span v-if="b.aucGroup" class="auc-net" :class="b.aucNet > 0 ? 'up' : b.aucNet < 0 ? 'down' : 'dim'">竞价 {{ yi(b.aucNet) }}</span>
                  <span v-if="b.leaderName" class="leader" :class="b.leaderChange > 0 ? 'up' : b.leaderChange < 0 ? 'down' : 'dim'">领涨 {{ b.leaderName }} {{ signed(b.leaderChange) }}%</span>
                </div>
              </td>
              <td :class="b.change > 0 ? 'up' : b.change < 0 ? 'down' : 'dim'">{{ signed(b.change) }}%</td>
              <td :class="b.speed > 0 ? 'up' : b.speed < 0 ? 'down' : 'dim'">{{ signed(b.speed) }}%</td>
              <td :class="b.mainNet > 0 ? 'up' : b.mainNet < 0 ? 'down' : 'dim'">{{ b.mainNet ? yi(b.mainNet) : '—' }}</td>
              <td>{{ b.strength ? Math.round(b.strength) : '—' }}</td>
              <td>{{ b.volRatio ? b.volRatio.toFixed(2) : '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 右栏: 选中题材的成分股 -->
      <div class="cpt-right">
        <div class="cpt-panel-head">
          <span class="cpt-panel-title">
            <template v-if="currentBoard">{{ currentBoard.name }}</template>
            <template v-else>成分股</template>
            <span v-if="currentBoard" class="cpt-board-code">{{ currentBoard.boardCode }}</span>
          </span>
          <span v-if="currentBoard" class="cpt-panel-count">{{ memberList.length }} 只</span>
        </div>
        <div v-if="!currentBoard" class="empty-state cpt-empty-hint"><i class="fa fa-hand-pointer-o"></i> 点击左侧精选板块查看成分股</div>
        <div v-else-if="memberLoading" class="loading-placeholder"><div class="spinner"></div><div>加载成分股...</div></div>
        <div v-else-if="!memberList.length" class="empty-state">该板块暂无成分股数据（非交易时段接口可能暂不可用）</div>
        <table v-else class="stock-table cpt-table">
          <thead>
            <tr>
              <th>代码</th>
              <th>名称</th>
              <th>现价</th>
              <th>涨幅%</th>
              <th>换手%</th>
              <th>成交额(亿)</th>
              <th>自由流通市值(亿)</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in memberList" :key="s.code">
              <td class="code-click" @click="linkToSoftware(s.code)">{{ s.code }}</td>
              <td class="name-col"><div class="name-main">{{ s.name }}</div></td>
              <td>{{ s.price ? s.price.toFixed(2) : '—' }}</td>
              <td :class="s.change > 0 ? 'up' : s.change < 0 ? 'down' : 'dim'">{{ signed(s.change) }}%</td>
              <td>{{ s.turnover ? s.turnover.toFixed(2) : '—' }}</td>
              <td>{{ s.amount ? yi(s.amount) : '—' }}</td>
              <td>{{ s.floatMv ? yi(s.floatMv) : '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
// 题材异动榜(2026-09-21 主人: 先换左栏为猫爪精选板块):
// 左栏 = 猫爪 themedaily_jx(level=parent 一级精选板块 267 个, 801xxxk, 含涨速/主力净额/强度/量比)
// 右栏 = 猫爪 thememembers_jx(股票池) + screening(行情: 现价/涨跌幅/换手/成交额/自由流通市值)
// 后端猫爪失败自动降级东财概念榜(响应 source 字段), 北交所成分股后端已过滤
import { ref, computed, onMounted } from 'vue'
import { usePolling } from '../composables/usePolling'
import { emConceptRank, emBoardMembers } from '../api/kpl'
import { linkToSoftware } from '../utils/tdx'
import { bjTimeStr } from '../utils/time'
import { yi, signed } from '../utils/format'

const bjTime = ref('--:--:--')
const conceptLoading = ref(true)
const memberLoading = ref(false)
const conceptList = ref([])
const memberList = ref([])
const currentBoard = ref(null)

// 左栏排序: 默认按涨速降序(异动榜语义), 点击列头切换
const sortKey = ref('speed')
const sortDir = ref(-1)  // -1 降序 / 1 升序

function toggleSort(k) {
  if (sortKey.value === k) {
    sortDir.value = -sortDir.value
  } else {
    sortKey.value = k
    sortDir.value = (k === 'name') ? 1 : -1   // 名称默认升序, 数值默认降序
  }
}
function sortInd(k) {
  if (sortKey.value !== k) return ''
  return sortDir.value === -1 ? '↓' : '↑'
}

// 竞价异动徽章: List1=今日新增(红) / List2=昨日延续(橙) / List3=其它(灰)
function aucBadgeText(g) {
  if (g === 'List1') return '竞价新增'
  if (g === 'List2') return '竞价延续'
  if (g === 'List3') return '竞价异动'
  return g
}
function aucBadgeClass(g) {
  if (g === 'List1') return 'auc-l1'
  if (g === 'List2') return 'auc-l2'
  return 'auc-l3'
}

const sortedConcepts = computed(() => {
  const arr = [...conceptList.value]
  const k = sortKey.value
  const d = sortDir.value
  arr.sort((a, b) => {
    if (k === 'name') return a.name.localeCompare(b.name) * d
    const av = a[k] ?? -Infinity
    const bv = b[k] ?? -Infinity
    return (av - bv) * d
  })
  return arr
})

async function loadConcepts() {
  try {
    const d = await emConceptRank()
    conceptList.value = d.list || []
  } catch (e) { /* 静默: 保留旧值 */ } finally {
    conceptLoading.value = false
  }
}

async function selectBoard(b) {
  currentBoard.value = b
  memberLoading.value = true
  try {
    const d = await emBoardMembers(b.boardCode)
    memberList.value = d.list || []
  } catch (e) { memberList.value = [] } finally {
    memberLoading.value = false
  }
}

onMounted(() => {
  bjTime.value = bjTimeStr()
  usePolling(() => { bjTime.value = bjTimeStr() }, 1000, { immediate: false })
  loadConcepts()
  // 分钟级异动榜: 板块榜 30s 刷新; 已选中板块的成分股同步刷新
  usePolling(() => {
    loadConcepts()
    if (currentBoard.value) selectBoard(currentBoard.value)
  }, 30000)
})
</script>

<style scoped>
.cpt-head {
  display: flex; align-items: center; gap: 12px;
  margin-bottom: 14px; flex-wrap: wrap;
}
.cpt-title { font-size: 1.1875rem; font-weight: 800; color: var(--text-main); }
.cpt-title .fa { color: var(--accent); }
.cpt-sub { font-size: 0.8125rem; color: var(--text-muted); }
.cpt-time { margin-left: auto; font-size: 0.8125rem; color: var(--text-muted); font-variant-numeric: tabular-nums; }

.cpt-layout {
  display: flex; gap: 14px; align-items: flex-start;
}
.cpt-left { flex: 0 0 460px; min-width: 0; }
.cpt-right { flex: 1 1 auto; min-width: 0; }

.cpt-panel-head {
  display: flex; align-items: center; gap: 8px;
  padding: 9px 12px; margin-bottom: 8px;
  background: var(--bg-card); border: 1px solid var(--border-soft);
  border-radius: 8px;
}
.cpt-panel-title { font-size: 0.9375rem; font-weight: 700; color: var(--text-main); }
.cpt-panel-title .fa { color: var(--accent); }
.cpt-board-code { font-size: 0.75rem; color: var(--text-muted); font-weight: 400; margin-left: 6px; }
.cpt-panel-count { margin-left: auto; font-size: 0.75rem; color: var(--text-muted); }

.cpt-table { width: 100%; }
.cpt-table th, .cpt-table td { white-space: nowrap; }

/* 左栏概念行: 可点击选中 */
.cpt-row { cursor: pointer; }
.cpt-row:hover td { background: rgba(255, 180, 0, 0.07); }
.cpt-row.active td { background: rgba(255, 180, 0, 0.13); }
.cpt-subinfo { font-size: 0.7188rem; color: var(--text-muted); margin-top: 2px; }
/* 领涨股(2026-09-21): 每板块正宗成分股里涨幅最高的一只, 与竞价净额同排展示 */
.cpt-subinfo .leader { margin-left: 6px; font-weight: 600; }

/* 竞价异动徽章(2026-09-21 融合 theme_auc_kp 进精选板块榜) */
.auc-badge {
  display: inline-block; margin-left: 6px; padding: 0 6px;
  font-size: 0.6563rem; font-weight: 700; line-height: 1.5;
  border-radius: 4px; vertical-align: middle;
}
.auc-l1 { background: rgba(255, 76, 76, 0.14); color: var(--up); }
.auc-l2 { background: rgba(255, 160, 0, 0.14); color: #ff9f0a; }
.auc-l3 { background: rgba(140, 150, 170, 0.16); color: var(--text-muted); }
.auc-net { margin-left: 6px; font-weight: 600; }

.cpt-empty-hint { padding: 60px 0; color: var(--text-muted); }

/* 移动端: 双栏改上下堆叠 */
@media (max-width: 960px) {
  .cpt-layout { flex-direction: column; }
  .cpt-left { flex: 1 1 auto; width: 100%; }
}
</style>
