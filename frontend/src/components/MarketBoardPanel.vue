<template>
  <!--
    ⑤ 题材榜（2026-09-27 v4.11.62 · 工单 批次二 层⑤，合并原「市场雷达」+「题材异动」）
    —— 列：题材名 | 涨停数 | 涨幅 | 主力净额(亿)，默认按涨停数排（工单原话）。
    —— 顶部数据源切换：开盘啦榜 / 东财概念榜（对应原两个数据源的合流）。
    —— 点行 → emit('select', board)，由父级打开成分股弹层（两个源共用同一弹层）。
    —— 涨停数来自「涨停梯队聚合」的题材分组，与板块榜是两个上游 ⇒ 名称做归一化模糊匹配，
       匹配不上显示「—」（见 utils/boards.js 顶部说明，不拿 0 冒充）。
    —— 工单明确：小/中/大/超大单分层与 5 日主力占比属 Level-2 数据，**本期不做**，只做主力净额。
  -->
  <div class="mb">
    <div class="mb-head">
      <span class="mb-title"><i class="fa fa-th-large"></i> 题材榜</span>
      <span class="mb-sub">默认按涨停数排序 · 点行看成分股</span>
      <span class="mb-num">{{ rows.length }} 个</span>
    </div>

    <div class="mb-src" role="tablist" aria-label="题材榜数据源">
      <button
        type="button" class="mb-src-btn" :class="{ active: src === 'kpl' }"
        role="tab" :aria-selected="src === 'kpl'" @click="$emit('update:src', 'kpl')"
      >
        <i class="fa fa-signal"></i> 开盘啦榜
      </button>
      <button
        type="button" class="mb-src-btn" :class="{ active: src === 'em' }"
        role="tab" :aria-selected="src === 'em'" @click="$emit('update:src', 'em')"
      >
        <i class="fa fa-fire"></i> 东财概念榜
      </button>
    </div>

    <div v-if="loading && !rows.length" class="mb-empty">加载题材榜…</div>
    <div v-else-if="failed" class="mb-empty warn">
      <i class="fa fa-exclamation-triangle"></i> {{ failMsg || '数据源暂不可用，可切换另一个源查看' }}
    </div>
    <div v-else-if="!rows.length" class="mb-empty">暂无题材榜数据（非交易时段接口可能暂不可用）</div>

    <div v-else class="mb-scroll">
      <table class="stock-table mb-table">
        <thead>
          <tr>
            <th>#</th>
            <th class="sortable" :class="{ active: sortKey === 'name' }" @click="toggleSort('name')">
              题材<span class="sort-ind">{{ sortInd('name') }}</span>
            </th>
            <th class="sortable" :class="{ active: sortKey === 'limitCount' }" @click="toggleSort('limitCount')">
              涨停数<span class="sort-ind">{{ sortInd('limitCount') }}</span>
            </th>
            <th class="sortable" :class="{ active: sortKey === 'change' }" @click="toggleSort('change')">
              涨幅%<span class="sort-ind">{{ sortInd('change') }}</span>
            </th>
            <th class="sortable" :class="{ active: sortKey === 'mainNet' }" @click="toggleSort('mainNet')">
              主力净额(亿)<span class="sort-ind">{{ sortInd('mainNet') }}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(b, i) in rows" :key="b.boardCode || b.name"
            class="mb-row" :class="{ hot: isHot(b) }"
            @click="$emit('select', b)"
          >
            <td class="rank-col">{{ i + 1 }}</td>
            <td class="name-col">
              <div class="name-main">{{ b.name }}</div>
              <div v-if="b.leaderName" class="mb-leader" :class="dirCls(b.leaderChange)">
                领涨 {{ b.leaderName }} {{ signed(b.leaderChange) }}%
              </div>
            </td>
            <td class="mb-limit">{{ b.limitCount === null || b.limitCount === undefined ? '—' : b.limitCount }}</td>
            <td :class="dirCls(b.change)">{{ signed(b.change) }}%</td>
            <td :class="dirCls(b.mainNet)">{{ yi(b.mainNet) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { sortBoardsByLimit } from '../utils/boards'
import { yi, signed } from '../utils/format'

const props = defineProps({
  boards: { type: Array, default: () => [] },
  src: { type: String, default: 'kpl' },
  loading: { type: Boolean, default: false },
  failed: { type: Boolean, default: false },
  failMsg: { type: String, default: '' },
  hotName: { type: String, default: '' },     // 层③点卡联动高亮
  hotCode: { type: String, default: '' },
})

defineEmits(['select', 'update:src'])

const sortKey = ref('limitCount')
const sortDir = ref('desc')

function toggleSort(k) {
  if (sortKey.value === k) { sortDir.value = sortDir.value === 'desc' ? 'asc' : 'desc'; return }
  sortKey.value = k
  sortDir.value = k === 'name' ? 'asc' : 'desc'
}
function sortInd(k) { return sortKey.value === k ? (sortDir.value === 'desc' ? '▼' : '▲') : '' }

const rows = computed(() => {
  const base = sortKey.value === 'limitCount' && sortDir.value === 'desc'
    ? sortBoardsByLimit(props.boards)
    : [...props.boards]
  const k = sortKey.value
  const dir = sortDir.value === 'desc' ? -1 : 1
  if (k !== 'limitCount' || sortDir.value !== 'desc') {
    base.sort((a, b) => {
      let va = a && a[k]
      let vb = b && b[k]
      if (k === 'name') return String(va || '').localeCompare(String(vb || '')) * dir
      // null 恒排最后（无论升降序）—— 避免「缺失」被当成最小值排到首位
      const na = va === null || va === undefined
      const nb = vb === null || vb === undefined
      if (na !== nb) return na ? 1 : -1
      return ((Number(va) || 0) - (Number(vb) || 0)) * dir
    })
  }
  return base
})

function isHot(b) {
  if (props.hotCode && b.boardCode === props.hotCode) return true
  return !!(props.hotName && b.name === props.hotName)
}

function dirCls(v) {
  if (v === null || v === undefined) return 'dim'
  return v > 0 ? 'up' : v < 0 ? 'down' : 'dim'
}
</script>

<style scoped>
.mb {
  border-radius: 10px; padding: 10px 12px;
  background: var(--bg-hover); border: 1px solid var(--border-soft);
}
.mb-head { display: flex; align-items: baseline; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.mb-title { color: var(--text-main); font-size: 0.8125rem; font-weight: 700; }
.mb-title .fa { color: var(--accent); }
.mb-sub { color: var(--text-muted); font-size: 0.6875rem; }
.mb-num { margin-left: auto; color: var(--text-muted); font-size: 0.6875rem; }

.mb-src { display: inline-flex; border-radius: 8px; overflow: hidden; border: 1px solid var(--border-soft); margin-bottom: 10px; }
.mb-src-btn {
  display: inline-flex; align-items: center; gap: 6px;
  background: var(--bg-input); color: var(--text-secondary); border: none;
  padding: 6px 14px; font-size: 0.8125rem; cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.mb-src-btn:hover { background: var(--bg-card); color: var(--text-main); }
.mb-src-btn.active { background: rgba(255, 180, 0, 0.15); color: #ffd700; font-weight: 700; box-shadow: inset 0 -2px 0 var(--accent); }

.mb-empty { color: var(--text-muted); font-size: 0.75rem; padding: 12px 2px; }
.mb-empty.warn { color: #ffb400; }
.mb-empty.warn i { margin-right: 5px; }

.mb-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.mb-table { min-width: 520px; width: 100%; }
.mb-row { cursor: pointer; }
.mb-row:hover td { background: rgba(255, 180, 0, 0.06); }
.mb-row.hot td { background: rgba(255, 180, 0, 0.12); }
.mb-leader { font-size: 0.6875rem; color: var(--text-muted); margin-top: 1px; }
.mb-limit { color: #ff5252; font-weight: 700; font-variant-numeric: tabular-nums; }
.up { color: #ff5252; }
.down { color: #00c864; }
.dim { color: var(--text-muted); }

@media (max-width: 768px) {
  .mb-src { display: flex; width: 100%; }
  .mb-src-btn { flex: 1 1 0; justify-content: center; padding: 8px 6px; }
}

body[data-bg="light"] .mb { background: rgba(255, 255, 255, 0.85); }
body[data-bg="light"] .mb-src { border-color: #d0d0d0; }
body[data-bg="light"] .mb-src-btn { background: #f5f5f5; color: #555; }
body[data-bg="light"] .mb-src-btn.active { background: rgba(198, 40, 40, 0.10); color: #c62828; }
body[data-bg="light"] .mb-limit { color: #c62828; }
body[data-bg="light"] .up { color: #c62828; }
body[data-bg="light"] .down { color: #1a7a2a; }
</style>
