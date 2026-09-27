<template>
  <div class="dev-warn">
    <!-- 汇总条：日期 + 红/黄计数 + 口径说明（★ 日期必须显式给出：
         盘后 15:45 才生成，非交易日/盘中看到的是**上一交易日**的结果） -->
    <div class="dev-bar">
      <span class="dev-bar-date">结果日期 <b>{{ date || '—' }}</b></span>
      <span class="dev-chip dev-chip-red">红级 {{ redCount }}</span>
      <span class="dev-chip dev-chip-yellow">黄级 {{ yellowCount }}</span>
      <span class="dev-bar-tip">红 = 今日已越线 · 黄 = 明日涨停即越线或已临近</span>
    </div>

    <div v-if="loading" class="dev-ph">
      <div class="spinner"></div><div>加载异动风险名单…</div>
    </div>

    <!-- ★ 失败与空必须文案不同：都长成空列表就是本项目最忌讳的「静默」 -->
    <div v-else-if="failed" class="dev-ph dev-ph-fail">
      <div class="dev-ph-t"><i class="fa fa-exclamation-triangle"></i> 异动风险名单读取失败</div>
      <div class="dev-ph-s">接口未返回数据（不是「今天没有风险」）—— 请稍后重试</div>
    </div>

    <div v-else-if="!rows.length" class="dev-ph">
      <div class="dev-ph-t">当前没有触发或临近异动线的个股</div>
      <div class="dev-ph-s">名单由盘后 15:45 全市场扫描生成；盘中/非交易日显示上一交易日结果</div>
    </div>

    <table v-else class="stock-table dev-table">
      <thead>
        <tr>
          <th>级别</th>
          <th class="sortable merged-col" :class="{ active: sort.keyOf('code') }" @click="sort.onSort('code', 'string')">名称<span class="sort-ind">{{ sort.ind('code') }}</span></th>
          <th>板块</th>
          <th>现价</th>
          <th class="sortable" :class="{ active: sort.keyOf('today_dev') }" @click="sort.onSort('today_dev')">今日偏离<span class="sort-ind">{{ sort.ind('today_dev') }}</span></th>
          <th class="sortable" :class="{ active: sort.keyOf('d3') }" @click="sort.onSort('d3')">3日<span class="sort-ind">{{ sort.ind('d3') }}</span></th>
          <th class="sortable" :class="{ active: sort.keyOf('d10') }" @click="sort.onSort('d10')">10日<span class="sort-ind">{{ sort.ind('d10') }}</span></th>
          <th class="sortable" :class="{ active: sort.keyOf('d30') }" @click="sort.onSort('d30')">30日<span class="sort-ind">{{ sort.ind('d30') }}</span></th>
          <th class="sortable" :class="{ active: sort.keyOf('next_trigger_pct') }" @click="sort.onSort('next_trigger_pct')">明日触发涨幅<span class="sort-ind">{{ sort.ind('next_trigger_pct') }}</span></th>
          <th>触发价</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="it in sort.sorted(rows)" :key="it.code">
          <td class="col-lv">
            <span class="dev-lv" :class="'dev-lv-' + (it.warn_level || 'safe')" :title="it.warn_msg || ''">
              {{ lvText(it.warn_level) }}
            </span>
          </td>
          <td
            class="stock-info-cell"
            role="button"
            tabindex="0"
            :aria-label="'查看 ' + (it.name || it.code) + ' 分时图'"
            @click="linkToSoftware(it.code)"
            @keydown.enter.prevent="linkToSoftware(it.code)"
          >
            <div class="stock-name-row">
              <span class="pool-hover-wrap"><span class="stock-name">{{ it.name || '—' }}</span></span>
            </div>
            <div class="stock-code-row"><span class="stock-code">{{ it.code }}</span></div>
          </td>
          <td class="col-board">{{ it.board || '—' }}</td>
          <td class="col-num">{{ money(it.price) }}</td>
          <td class="col-num" :class="chgCls(it.today_dev)">{{ signedPct(it.today_dev) }}</td>
          <td class="col-num" :class="lvCls(it.d3_status)">{{ signedPct(it.d3) }}</td>
          <td class="col-num" :class="lvCls(it.d10_status)">{{ signedPct(it.d10) }}</td>
          <td class="col-num" :class="lvCls(it.d30_status)">{{ signedPct(it.d30) }}</td>
          <td class="col-num" :class="it.reachable ? 'col-up' : ''">
            <template v-if="it.next_trigger_pct == null">—</template>
            <template v-else>{{ fmtNum(it.next_trigger_pct, 2, '%') }}</template>
            <span v-if="it.rule" class="col-rule" :title="'下一道未触发的线：' + it.rule">{{ it.rule }}</span>
          </td>
          <td class="col-num">{{ money(it.trigger_price) }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
/**
 * 严重异动名单（`/yidong` tab 1）—— **纯展示**，数据由父级注入。
 *
 * ★ 之所以做成纯展示组件：本项目两次事故（v4.11.58 的 `void NAV_GROUPS`、
 *   v4.11.62 的 `MarketBoardPanel` 模板用了未定义的 `list`）都是「模板里自由变量错拼」
 *   —— vite build 与 eslint 都看不见，只有**真渲染**才暴露。
 *   纯展示 = 可以在 SSR 冒烟测试里用夹具把四态（loading/failed/empty/ok）各渲染一遍。
 */
import { computed } from 'vue'
import { linkToSoftware } from '../utils/tdx'
import { fmtNum } from '../utils/format'
import { useSortable } from '../composables/useSortable'

const props = defineProps({
  rows: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  failed: { type: Boolean, default: false },
  date: { type: String, default: '' },
})

const sort = useSortable()

const redCount = computed(() => props.rows.filter((r) => r.warn_level === 'red').length)
const yellowCount = computed(() => props.rows.filter((r) => r.warn_level === 'yellow').length)

function lvText(level) {
  if (level === 'red') return '红'
  if (level === 'yellow') return '黄'
  return '—'
}

function lvCls(status) {
  if (status === '触发') return 'col-up'
  if (status === '临近') return 'col-near'
  return 'col-dim'
}

function chgCls(v) {
  const n = Number(v)
  if (v === null || v === undefined || Number.isNaN(n) || n === 0) return 'col-dim'
  return n > 0 ? 'col-up' : 'col-down'
}

function signedPct(v) {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  return (n > 0 ? '+' : '') + fmtNum(n, 2, '%')
}

function money(v) {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  return fmtNum(n, 2)
}
</script>

<style scoped>
.dev-bar {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin-bottom: 10px; font-size: 0.8125rem;
}
.dev-bar-date { color: var(--text-secondary); }
.dev-bar-date b { color: #ffd700; font-family: inherit; letter-spacing: 0.5px; }
.dev-bar-tip { color: var(--text-muted); font-size: 0.75rem; }
.dev-chip {
  display: inline-block; padding: 1px 8px; border-radius: 10px;
  font-size: 0.75rem; font-weight: 600;
}
.dev-chip-red { color: #ff6b6b; background: rgba(255, 77, 79, 0.14); border: 1px solid rgba(255, 77, 79, 0.45); }
.dev-chip-yellow { color: #ffc53d; background: rgba(255, 197, 61, 0.12); border: 1px solid rgba(255, 197, 61, 0.42); }

.dev-ph { text-align: center; padding: 36px 12px; color: var(--text-muted); }
.dev-ph-t { font-size: 0.9375rem; margin-bottom: 6px; color: var(--text-secondary); }
.dev-ph-s { font-size: 0.75rem; line-height: 1.6; }
.dev-ph-fail .dev-ph-t { color: #ff6b6b; }
.dev-ph-fail .dev-ph-s { color: #ff9a9a; }

.dev-table { width: 100%; }
.dev-table th, .dev-table td { vertical-align: middle !important; text-align: center !important; }

.col-lv { width: 48px; }
.dev-lv {
  display: inline-block; width: 20px; height: 20px; line-height: 20px;
  border-radius: 4px; font-size: 0.75rem; font-weight: 700; cursor: help;
}
.dev-lv-red { background: #b3261e; color: #fff; border: 1px solid #ff5c5c; }
.dev-lv-yellow { background: rgba(255, 197, 61, 0.2); color: #ffd666; border: 1px solid #ffc53d; }
.dev-lv-safe { background: rgba(255, 255, 255, 0.05); color: var(--text-muted); border: 1px solid var(--border-soft); }

.col-board { color: var(--text-muted); font-size: 0.75rem; white-space: nowrap; }
.col-num { white-space: nowrap; font-variant-numeric: tabular-nums; }
.col-up { color: #ff6b6b; font-weight: 600; }
.col-down { color: #33cc77; font-weight: 600; }
.col-near { color: #ffc53d; font-weight: 600; }
.col-dim { color: var(--text-muted); }
.col-rule { display: block; font-size: 0.6875rem; color: var(--text-muted); font-weight: 400; }

/* 股票名称格：与全站其它表格同构（名称在上、代码在下） */
.dev-table td.stock-info-cell {
  cursor: pointer; min-width: 80px !important; padding: 6px 4px !important;
  display: table-cell !important; vertical-align: middle !important; text-align: center !important;
}
.stock-info-cell .stock-name-row { display: block !important; line-height: 1.4 !important; }
.stock-info-cell .stock-name { font-weight: 600 !important; color: var(--text-main); font-size: 0.8125rem !important; }
.stock-info-cell .stock-code-row { display: block !important; line-height: 1.2 !important; margin-top: 2px !important; }
.stock-info-cell .stock-code {
  font-family: inherit; font-size: 0.75rem !important; color: var(--text-muted); letter-spacing: 0.5px !important;
}
.stock-info-cell:hover .stock-name, .stock-info-cell:hover .stock-code { color: var(--accent); }

.spinner {
  width: 28px; height: 28px; border: 3px solid rgba(255, 180, 0, 0.3);
  border-top-color: #ffb400; border-radius: 50%;
  animation: dev-spin 0.8s linear infinite; margin: 0 auto 10px;
}
@keyframes dev-spin { to { transform: rotate(360deg); } }

@media (max-width: 768px) {
  .dev-table { min-width: 720px; }
  .dev-bar { font-size: 0.75rem; }
}

/* 浅色主题 */
body[data-bg="light"] .dev-bar-date b { color: #8a5500; }
body[data-bg="light"] .dev-chip-red { color: #b3261e; }
body[data-bg="light"] .dev-chip-yellow { color: #8a5500; }
body[data-bg="light"] .dev-lv-red { color: #fff; }
body[data-bg="light"] .dev-lv-yellow { background: #fff3cd; color: #8a5500; border-color: #c79100; }
body[data-bg="light"] .dev-lv-safe { background: #f1efe8; color: #6b6b6b; }
body[data-bg="light"] .col-up { color: #d4380d; }
body[data-bg="light"] .col-down { color: #237804; }
body[data-bg="light"] .col-near { color: #8a5500; }
</style>
