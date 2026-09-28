<template>
  <div class="dev-detail">
    <div v-if="loading" class="dev-ph">
      <div class="spinner"></div><div>正在计算 {{ code }} 的偏离值…</div>
    </div>

    <!-- ★ 接口失败 与「票算不出」是两件事，文案必须不同 -->
    <div v-else-if="failed" class="dev-ph dev-ph-fail">
      <div class="dev-ph-t"><i class="fa fa-exclamation-triangle"></i> 计算接口调用失败</div>
      <div class="dev-ph-s">网络或服务异常（不是「这只票安全」）—— 请稍后重试</div>
    </div>

    <div v-else-if="!code" class="dev-ph">
      <div class="dev-ph-t">输入 6 位股票代码开始计算</div>
      <div class="dev-ph-s">按交易所口径（区间首尾相减）实时计算 10 / 30 日涨跌幅偏离值</div>
    </div>

    <div v-else-if="!data" class="dev-ph">
      <div class="dev-ph-t">尚未计算</div>
      <div class="dev-ph-s">点击「计算」查看 {{ code }} 的两条偏离线与明日触发空间</div>
    </div>

    <div v-else-if="!data.ok" class="dev-ph dev-ph-fail">
      <div class="dev-ph-t"><i class="fa fa-question-circle"></i> 算不出这只票</div>
      <div class="dev-ph-s">{{ reasonText }}</div>
    </div>

    <template v-else>
      <div class="dd-head">
        <div class="dd-title">
          <span class="dd-name">{{ data.name || '—' }}</span>
          <span class="dd-code">{{ data.code }}</span>
        </div>
        <span class="dd-tag">{{ data.board }}</span>
        <span class="dd-tag dd-tag-idx">{{ data.index_name }} {{ data.index }}</span>
        <span class="dd-tag">涨停 {{ fmtNum(data.limit_up_pct, 0, '%') }}</span>
        <span class="dd-price">现价 <b>{{ fmtNum(data.price, 2) }}</b></span>
        <span class="dd-date">{{ data.date }}</span>
      </div>

      <!-- 两条偏离线（🔴 2026-09-28 主人拍板移除 3 日维度：原为三条，卡片由 WINDOWS 驱动） -->
      <div class="dd-lines">
        <div v-for="n in WINDOWS" :key="n" class="dd-card" :class="'dd-' + stClsOf(n)">
          <div class="dd-hd">
            <span class="dd-tt">{{ n }} 日偏离值</span>
            <span class="dd-badge" :class="'dd-badge-' + stClsOf(n)">{{ statusOf(n) }}</span>
          </div>
          <div class="dd-val">
            {{ signedPct(lineOf(n).value) }}
            <span class="dd-thresh">/ 阈值 {{ fmtNum(lineOf(n).thresh, 0, '%') }}</span>
          </div>
          <div class="dd-bar"><i :style="{ width: barPct(n) }"></i></div>
          <div class="dd-meta">
            <div>个股区间 {{ signedPct(lineOf(n).stock_pct) }}</div>
            <div>指数区间 {{ signedPct(lineOf(n).idx_pct) }}</div>
            <div class="dd-meta-w">{{ windowOf(n) }}</div>
          </div>
        </div>
      </div>

      <!-- 明日触发空间 -->
      <div class="dd-room">
        <template v-if="hitList.length">
          <span class="dd-room-t"><i class="fa fa-bolt"></i> 明日涨停即触发</span>
          <span class="dd-room-r">{{ hitList.join('、') }}</span>
        </template>
        <template v-else-if="data.room && data.room.next_trigger_pct != null">
          <span class="dd-room-t"><i class="fa fa-clock-o"></i> 明日触发空间</span>
          <span class="dd-room-r">
            再涨 <b>{{ fmtNum(data.room.next_trigger_pct, 2, '%') }}</b>（价 {{ fmtNum(data.room.trigger_price, 2) }}）
            触发 {{ data.room.rule }}
          </span>
          <span class="dd-room-w">超过一个涨停幅度，明日单日不可能触发</span>
        </template>
        <template v-else>
          <span class="dd-room-t"><i class="fa fa-check-circle"></i> 两条线均已触发</span>
          <span class="dd-room-r">无「下一条」可算</span>
        </template>
      </div>

      <!-- ★ 未来十日推演（v4.11.71 起改为**实基倒推**）：
           旧版是「假设个股天天涨停、指数持平」的**虚值**（与今日真实偏离无关，
           同板块任何票都长得一样）。现版用**真实日涨幅链**（含除权免疫）做基轴：
           每行回答「若想在第 k 天首次触发，需要从今天起日均涨多少」，
           再由该幅度推出当天的 10/30 日偏离值。**不预测未来行情**，只做条件推演。 -->
      <div class="dd-proj">
        <div class="dd-proj-h">
          未来十日推演
          <span class="dd-proj-s">
            自今日真实偏离倒推「该日触发需日均涨 X%」（指数按今日持平推演，历史段用真实指数）
          </span>
        </div>
        <div v-if="!projectRows.length" class="dd-proj-empty">
          推演算不出 —— 交易日历缺口、个股日线不足（<b>需覆盖 30 日窗口 + 期初前收盘</b>）或缺真实涨跌幅
        </div>
        <table v-else class="stock-table dd-table">
          <thead>
            <tr>
              <th>交易日</th>
              <th>需日均涨</th>
              <th>触发规则</th>
              <th>10日偏离</th>
              <th>30日偏离</th>
              <th>涨停</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in projectRows" :key="r.day">
              <td class="dd-day">
                <span class="dd-day-n">第{{ r.day }}天</span>
                <span class="dd-day-d">{{ r.date }}</span>
              </td>
              <td class="dd-cell">
                <span class="dd-v col-up">{{ signedPct(r.safe_gain_pct) }}</span>
                <span class="dd-p">{{ fmtNum(r.price, 2) }}</span>
              </td>
              <td class="dd-cell dd-rule" :class="r.zt_trigger ? 'col-up' : 'col-dim'">
                <span class="dd-v">{{ r.trigger_rule }}</span>
                <span v-if="r.trigger !== r.trigger_rule && r.trigger !== '不触发'" class="dd-p dd-p-dim">{{ r.trigger }}</span>
                <span v-else class="dd-p dd-p-dim">—</span>
              </td>
              <td class="dd-cell">
                <span class="dd-v" :class="cls(r.dev10)">{{ signedPct(r.dev10) }}</span>
                <span class="dd-p dd-p-dim">{{ leftText(r.left10) }}</span>
              </td>
              <td class="dd-cell">
                <span class="dd-v" :class="cls(r.dev30)">{{ signedPct(r.dev30) }}</span>
                <span class="dd-p dd-p-dim">{{ leftText(r.left30) }}</span>
              </td>
              <td class="dd-cell" :class="r.zt_trigger ? 'col-up' : 'col-dim'">
                <span class="dd-v">{{ r.zt_trigger ? '会触发' : '不触发' }}</span>
                <span class="dd-p dd-p-dim">{{ ztText(r) }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<script setup>
/**
 * 个股异动风险详情（`/yidong` tab 2「异动计算器」）—— **纯展示**，数据由父级注入。
 *
 * `data` 的形状 = `GET /api/dev/risk` 的返回体（见 backend/app/api/dev.py）。
 * ★ 关键：`ok:false` 时**不能**渲染成一堆 0 —— 后端刻意用「ok:false + reason」表达
 *   「算不出」，前端必须把 reason 翻成人话，绝不能让用户以为「偏离值 0 = 安全」。
 */
import { computed } from 'vue'
import { fmtNum } from '../utils/format'

const props = defineProps({
  data: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  failed: { type: Boolean, default: false },
  code: { type: String, default: '' },
})

const REASON = {
  missing_code: '未填代码',
  bad_code: '代码必须是 6 位数字',
  unknown_board: '无法识别板块 —— 仅支持沪深主板 / 创业板 / 科创板 / 北交所',
  index_unavailable: '对应指数数据取不到（指数源不可用），弃权而不猜',
  stock_unavailable: '该股日线取不到（可能长期停牌或代码不存在）',
  window_incomplete: '30 日窗口数据不完整，无法计算',
  // 2026-09-28 新增：交易所口径「新股上市后前 5 个交易日不设涨跌幅限制」⇒ 异动从第 6 个
  //   交易日起算；窗口仍含上市初期时整体弃权（不再输出失真偏离值，见 services/dev_risk.py）
  new_stock: '新股上市未满 6 个交易日（前 5 个交易日不设涨跌幅限制，不纳入异动计算）',
}

const reasonText = computed(() => {
  const d = props.data || {}
  return REASON[d.reason] || d.msg || '原因未知'
})

const projectRows = computed(() => (props.data && props.data.project10) || [])
const hitList = computed(() => {
  const r = (props.data && props.data.room) || {}
  return Array.isArray(r.hit) ? r.hit : []
})

// 两个观察窗口（10/30 个交易日）—— 提到模块常量，避免模板里写字面量数组每次渲染都新建。
// 🔴 2026-09-28 主人拍板：**移除 3 日维度**（原为 [3, 10, 30]，三张卡）。后端仍会返回
//    `dev.d3`/`detail[3]`（数据链路刻意不动），但本页不再展示这条线。
const WINDOWS = [10, 30]

function lineOf(n) {
  const d = (props.data && props.data.detail) || {}
  const l = d[n] || {}
  // ★ 2026-09-28 修复（既有缺陷）：**阈值属于「规则」层** —— 后端把它放在 `dev.dN.thresh`，
  //   而 `detail[n]` 只带区间细节（stock_pct / idx_pct / window / base_date）。
  //   原实现只读 `detail[n].thresh` ⇒ 卡片上「/ 阈值」恒显示「—」、进度条恒 0%（barPct 读同一字段）。
  //   这里把两处数据合并（与同文件 `statusOf()` 读 `dev.dN.status` 的做法保持一致）。
  const rule = ((props.data && props.data.dev) || {})['d' + n] || {}
  return Object.assign({}, l, { thresh: rule.thresh === undefined ? l.thresh : rule.thresh })
}
function statusOf(n) {
  const l = (props.data && props.data.dev) || {}
  return (l['d' + n] || {}).status || '安全'
}
// ★ 状态 → ascii 类名（**不要**把中文直接拼进 class：
//    `dd-触发` 这种选择器能跑但极度脆弱，改名/别名映射时必踩）
const ST_CLS = { 触发: 'hit', 临近: 'near', 安全: 'safe' }
function stClsOf(n) {
  return ST_CLS[statusOf(n)] || 'safe'
}
function windowOf(n) {
  const w = (props.data && props.data.window) || {}
  return w[n] || ''
}

// 进度条：|偏离值| / |阈值|，截断到 100%（越线后条是满的，值本身仍如实显示）
function barPct(n) {
  const l = lineOf(n)
  const v = Number(l.value)
  const t = Number(l.thresh)
  if (!Number.isFinite(v) || !Number.isFinite(t) || t === 0) return '0%'
  return Math.min(100, Math.abs(v) / Math.abs(t) * 100).toFixed(1) + '%'
}

function signedPct(v) {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  return (n > 0 ? '+' : '') + fmtNum(n, 2, '%')
}

function cls(v) {
  if (v === null || v === undefined) return 'col-dim'
  return Number(v) > 0 ? 'col-up' : 'col-dim'
}

// ★ 「剩余交易日」文案（v4.11.69 参照「异动了么」版式新增）：
//   后端 `left10` / `left30` 的语义 —— 0 = 该窗口当日已触发，N>0 = 还需 N 个交易日触发，
//   null/undefined = 10 天投影内不会触发。**三者必须显示成不同文案**：
//   若把 null 也渲染成「剩 0 日」，用户会误读成「今天就触发」，与事实相反。
//   🔴 v4.11.71 起「能否触发」的判据是 **日均涨幅 ≤ 一个涨停**（可达成），
//      不是「二分求解器返回了值」（后者会给出日均 +300% 这种人类做不到的答案）。
function leftText(v) {
  if (v === null || v === undefined) return '10日内不触发'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  if (n <= 0) return '已触发'
  return '剩 ' + n + ' 日'
}

// 涨停列副行：该日涨停后哪些线会越线（无则明示「不触发」，不留空白）
function ztText(r) {
  if (!r || !r.zt_trigger) return '无越线'
  return String(r.trigger || '').replace(/\s*\/\s*/g, ' + ')
}

// ★ 「需日均涨」列的显示规则（v4.11.71 实基倒推）：
//   后端 `safe_gain_pct` = 从今天起**日均**涨幅（%），可为负（今天已越线 ⇒ 当日就触发，0 也行）；
//   `price` = 按该日均涨幅算出的当天收盘价。两者在 `hit` 为 false 时均为 null ——
//   含义是「10 个交易日内即便天天涨停也触发不了」，此时必须显示「—」而**不是 0%**
//   （显示 0% 会被读成「不涨就触发」，与事实相反）。
//   注：旧版此列是「按涨停累计算的安全涨幅」，语义完全不同，勿复用到别处。
</script>

<style scoped>
.dd-head {
  display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
  padding-bottom: 10px; margin-bottom: 12px; border-bottom: 1px solid var(--border-soft);
}
.dd-title { display: flex; align-items: baseline; gap: 6px; }
.dd-name { font-size: 1.0625rem; font-weight: 700; color: var(--text-main); }
.dd-code { color: var(--text-muted); font-size: 0.8125rem; letter-spacing: 0.5px; }
.dd-tag {
  padding: 1px 8px; border-radius: 10px; font-size: 0.75rem;
  color: #ffd700; background: rgba(255, 180, 0, 0.12); border: 1px solid rgba(255, 180, 0, 0.35);
}
.dd-tag-idx { color: #9cf; background: rgba(120, 190, 255, 0.1); border-color: rgba(120, 190, 255, 0.35); }
.dd-price { margin-left: auto; color: var(--text-secondary); font-size: 0.8125rem; }
.dd-price b { color: #ffd700; }
.dd-date { color: var(--text-muted); font-size: 0.75rem; }

.dd-lines { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-bottom: 12px; }
.dd-card {
  border: 1px solid var(--border-soft); border-radius: 8px; padding: 10px 12px;
  background: var(--bg-hover);
}
.dd-card.dd-hit { border-color: rgba(255, 77, 79, 0.55); background: rgba(255, 77, 79, 0.08); }
.dd-card.dd-near { border-color: rgba(255, 197, 61, 0.5); background: rgba(255, 197, 61, 0.07); }
.dd-hd { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.dd-tt { font-size: 0.8125rem; color: var(--text-secondary); }
.dd-badge { font-size: 0.6875rem; padding: 0 6px; border-radius: 3px; font-weight: 600; }
.dd-badge-hit { color: #fff; background: #b3261e; }
.dd-badge-near { color: #3a2a00; background: #ffc53d; }
.dd-badge-safe { color: var(--text-muted); background: rgba(255, 255, 255, 0.07); }
.dd-val { font-size: 1.25rem; font-weight: 700; color: var(--text-main); font-variant-numeric: tabular-nums; }
.dd-thresh { font-size: 0.75rem; font-weight: 400; color: var(--text-muted); margin-left: 6px; }
.dd-bar { height: 4px; border-radius: 2px; background: rgba(255, 255, 255, 0.08); margin: 8px 0; overflow: hidden; }
.dd-bar i { display: block; height: 100%; background: #ffb400; }
.dd-hit .dd-bar i { background: #ff4d4f; }
.dd-meta { font-size: 0.6875rem; color: var(--text-muted); line-height: 1.7; }
.dd-meta-w { color: var(--text-muted); opacity: 0.8; }

.dd-room {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 10px 12px; border-radius: 8px; margin-bottom: 12px;
  background: rgba(255, 180, 0, 0.08); border: 1px solid rgba(255, 180, 0, 0.28);
  font-size: 0.8125rem;
}
.dd-room-t { color: #ffd700; font-weight: 600; }
.dd-room-r { color: var(--text-main); }
.dd-room-r b { color: #ff6b6b; }
.dd-room-w { color: var(--text-muted); font-size: 0.75rem; }

.dd-proj-h { font-size: 0.875rem; color: var(--text-secondary); margin-bottom: 8px; }
.dd-proj-s { font-size: 0.6875rem; color: var(--text-muted); margin-left: 6px; font-weight: 400; }
.dd-proj-empty { padding: 20px; text-align: center; color: var(--text-muted); font-size: 0.8125rem; line-height: 1.7; }
.dd-proj-empty b { color: var(--text-secondary); font-weight: 600; }
.dd-table { width: 100%; table-layout: fixed; }
.dd-table th, .dd-table td { vertical-align: middle !important; text-align: center !important; }

/* ★ 双行单元格（v4.11.69 参照「异动了么」版式）：
   上行 = 数值（大、着色），下行 = 单位/说明（小、灰）。两行高度固定，
   避免不同行单元格高度不齐把表格拉成锯齿。 */
.dd-cell { line-height: 1.35; }
.dd-v { display: block; font-size: 0.8125rem; font-weight: 600; font-variant-numeric: tabular-nums; }
.dd-p { display: block; font-size: 0.6875rem; color: var(--text-muted); font-variant-numeric: tabular-nums; }
.dd-p-dim { opacity: 0.75; }
.dd-day-n { display: block; font-size: 0.8125rem; color: var(--text-secondary); }
.dd-day-d { display: block; font-size: 0.6875rem; color: var(--text-muted); font-variant-numeric: tabular-nums; }
.dd-rule .dd-v { white-space: nowrap; }

.col-num { white-space: nowrap; font-variant-numeric: tabular-nums; }
.col-up { color: #ff6b6b; font-weight: 600; }
.col-dim { color: var(--text-muted); }

.dev-ph { text-align: center; padding: 36px 12px; color: var(--text-muted); }
.dev-ph-t { font-size: 0.9375rem; margin-bottom: 6px; color: var(--text-secondary); }
.dev-ph-s { font-size: 0.75rem; line-height: 1.6; }
.dev-ph-fail .dev-ph-t { color: #ff6b6b; }
.dev-ph-fail .dev-ph-s { color: #ff9a9a; }

.spinner {
  width: 28px; height: 28px; border: 3px solid rgba(255, 180, 0, 0.3);
  border-top-color: #ffb400; border-radius: 50%;
  animation: dd-spin 0.8s linear infinite; margin: 0 auto 10px;
}
@keyframes dd-spin { to { transform: rotate(360deg); } }

@media (max-width: 768px) {
  .dd-lines { grid-template-columns: 1fr; }
  .dd-price { margin-left: 0; }
  .dd-table { min-width: 480px; }
  .dd-proj { overflow-x: auto; -webkit-overflow-scrolling: touch; }
}

body[data-bg="light"] .dd-name { color: #3a2a00; }
body[data-bg="light"] .dd-val { color: #3a2a00; }
body[data-bg="light"] .dd-tag { color: #8a5500; background: #fff8e6; border-color: #e0b800; }
body[data-bg="light"] .dd-tag-idx { color: #0b4a80; background: #eaf4ff; border-color: #85b7eb; }
body[data-bg="light"] .dd-badge-safe { color: #6b6b6b; background: #f1efe8; }
body[data-bg="light"] .dd-room { background: #fffaf0; border-color: #e0b800; }
body[data-bg="light"] .dd-room-t { color: #8a5500; }
body[data-bg="light"] .dd-room-r { color: #3a2a00; }
body[data-bg="light"] .col-up { color: #d4380d; }
body[data-bg="light"] .dd-bar { background: #e8e6df; }
</style>
