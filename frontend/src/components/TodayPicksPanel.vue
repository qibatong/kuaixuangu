<template>
  <!--
    ④ 今日票战报（2026-09-27 v4.11.62 · 工单 批次二 层④）
    —— 红色聚合大卡：今日选了N只 | 涨停X · 炸板X | 平均涨幅 | 红X绿X；下方逐票表
       （代码 | 名称 | 现价 | 涨幅 | 状态标签）。
    —— 数据由父级统一取：当日（或最近交易日）选股批次名单 + /api/quotes 实时价。
    —— 🔴 「炸板」只有数据源提供当日最高涨幅(peakChange)时才判定，否则显示「—」
       （判据见 utils/picks.js 顶部注释，不臆造数字）。
    —— 默认按状态排序：封板/炸板最前、翻绿最后（工单「二期体验 2」）。
  -->
  <div class="tp">
    <div class="tp-head">
      <span class="tp-title"><i class="fa fa-flag-checkered"></i> 今日票战报</span>
      <span v-if="date" class="tp-date" :class="{ stale: !isToday }">
        {{ isToday ? '今日名单' : date + ' 名单（非今日）' }}
      </span>
    </div>

    <!-- 聚合大卡 -->
    <div class="tp-agg">
      <div class="tp-agg-cell">
        <span class="tp-k">今日选了</span>
        <span class="tp-v">{{ summary.count }} 只</span>
      </div>
      <div class="tp-agg-cell">
        <span class="tp-k">涨停 / 炸板</span>
        <span class="tp-v">
          <span class="zt">{{ summary.limitCount }}</span>
          <span class="sep">·</span>
          <span class="bk">{{ summary.brokenKnown ? summary.brokenCount : '—' }}</span>
        </span>
      </div>
      <div class="tp-agg-cell">
        <span class="tp-k">平均涨幅</span>
        <span class="tp-v" :class="dirCls(summary.avgChange)">{{ pct(summary.avgChange) }}</span>
      </div>
      <div class="tp-agg-cell">
        <span class="tp-k">红 / 绿</span>
        <span class="tp-v"><span class="rise">{{ summary.upCount }}</span>/<span class="fall">{{ summary.downCount }}</span></span>
      </div>
    </div>

    <div v-if="loading && !rows.length" class="tp-empty">加载今日名单…</div>
    <div v-else-if="failed" class="tp-empty warn">
      <i class="fa fa-exclamation-triangle"></i> 名单读取失败，请稍后重试
    </div>
    <div v-else-if="!rows.length" class="tp-empty">{{ emptyMsg || '暂无今日名单（9:25 定格后产生）' }}</div>

    <div v-else class="tp-table-scroll">
      <table class="stock-table tp-table">
        <thead>
          <tr>
            <th>代码</th>
            <th>名称</th>
            <th>现价</th>
            <th>涨幅%</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in rows" :key="s.code">
            <td class="code-click" @click="go(s.code)">{{ s.code }}</td>
            <td class="name-col">{{ s.name }}</td>
            <td>{{ s.price === null || s.price === undefined ? '—' : s.price.toFixed(2) }}</td>
            <td :class="dirCls(s.change)">{{ pct(s.change) }}</td>
            <td><span class="tp-st" :class="'st-' + state(s).key">{{ state(s).label }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { pickState, sortByState } from '../utils/picks'
import { pct } from '../utils/format'
import { linkToSoftware } from '../utils/tdx'

const props = defineProps({
  stocks: { type: Array, default: () => [] },
  summary: { type: Object, default: () => ({ count: 0, limitCount: 0, brokenCount: 0, brokenKnown: false, upCount: 0, downCount: 0, avgChange: null }) },
  date: { type: String, default: '' },
  isToday: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  failed: { type: Boolean, default: false },
  emptyMsg: { type: String, default: '' },
})

const rows = computed(() => sortByState(props.stocks))

function state(s) { return pickState(s) }

function dirCls(v) {
  if (v === null || v === undefined) return 'dim'
  return v > 0 ? 'up' : v < 0 ? 'down' : 'dim'
}

function go(code) { linkToSoftware(code) }
</script>

<style scoped>
.tp {
  border-radius: 10px; padding: 10px 12px;
  background: rgba(198, 40, 40, 0.10);
  border: 1px solid rgba(198, 40, 40, 0.35);
}
.tp-head { display: flex; align-items: baseline; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.tp-title { color: #ff8a80; font-size: 0.8125rem; font-weight: 700; }
.tp-title .fa { color: #ff5252; }
.tp-date { margin-left: auto; color: var(--text-muted); font-size: 0.6875rem; }
.tp-date.stale { color: #ffb400; }   /* 非今日名单必须显眼，避免被当成当日名单 */

.tp-agg {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px;
  padding: 8px; border-radius: 8px; background: rgba(0, 0, 0, 0.18); margin-bottom: 10px;
}
.tp-agg-cell { display: flex; flex-direction: column; align-items: center; gap: 2px; }
.tp-k { color: var(--text-muted); font-size: 0.6875rem; white-space: nowrap; }
.tp-v { font-size: 1rem; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--text-main); white-space: nowrap; }
.tp-v .zt, .tp-v.zt { color: #ff5252; }
.tp-v .bk { color: #ff8a5c; }
.tp-v .sep { color: var(--text-muted); margin: 0 3px; }
.tp-v .rise { color: #ff5252; }
.tp-v .fall { color: #00c864; }
.tp-v.up { color: #ff5252; }
.tp-v.down { color: #00c864; }
.tp-v.dim { color: var(--text-muted); }

.tp-empty { color: var(--text-muted); font-size: 0.75rem; padding: 10px 2px; }
.tp-empty.warn { color: #ffb400; }
.tp-empty.warn i { margin-right: 5px; }
.tp-table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.tp-table { min-width: 420px; width: 100%; }
.tp-st {
  display: inline-block; font-size: 0.6875rem; padding: 1px 6px; border-radius: 4px; white-space: nowrap;
}
.st-limit { color: #fff; background: #c62828; }
.st-broken { color: #ffd76a; background: rgba(255, 180, 0, 0.2); border: 1px solid rgba(255, 180, 0, 0.45); }
.st-high { color: #ff8a80; border: 1px solid rgba(255, 82, 82, 0.45); }
.st-flat { color: var(--text-secondary); border: 1px solid var(--border-soft); }
.st-down { color: #00c864; border: 1px solid rgba(0, 200, 100, 0.45); }
.st-unknown { color: var(--text-muted); }

@media (max-width: 480px) {
  .tp-agg { grid-template-columns: repeat(2, 1fr); row-gap: 8px; }
}

body[data-bg="light"] .tp { background: rgba(198, 40, 40, 0.07); border-color: rgba(198, 40, 40, 0.3); }
body[data-bg="light"] .tp-title { color: #b83010; }
body[data-bg="light"] .tp-agg { background: rgba(0, 0, 0, 0.04); }
body[data-bg="light"] .st-limit { color: #fff; background: #c62828; }
body[data-bg="light"] .st-down { color: #1a7a2a; }
</style>
