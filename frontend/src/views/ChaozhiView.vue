<template>
  <!--
    超智研判（聚合页，2026-10-01 新增；原「AI预测」升级）
    数据 = 后端 `/api/chaozhi/overview`（只读聚合，零新增上游出网；见 backend/app/services/chaozhi.py）
    口径 = docs/超智研判-聚合页开发方案-20261001.md §四 A 案；一期已做：得分卡 + 两条 10 日序列 + 双模型个股 + 标签
    ⚠️ 降级如实展示：`meta.notes` 里有什么就显示什么（例如「火眼 LGB 当日无预测文件」），
       分数缺失显示 `—` 而**不是 0**（0 会被误读成"模型给了 0 分"）。
  -->
  <div class="page-shell cz-root">
    <h1 class="visually-hidden">超智研判</h1>

    <header class="cz-head">
      <span class="cz-logo" aria-hidden="true">智</span>
      <div class="cz-head-txt">
        <div class="cz-h1">超智研判</div>
        <div class="cz-sub">双模型 金睛 + 火眼 · 资金 &amp; 情绪预判</div>
      </div>
      <span class="cz-date">{{ date || '—' }}</span>
    </header>

    <div v-if="loading" class="loading-placeholder"><div class="spinner"></div><div>加载研判数据...</div></div>
    <div v-else-if="failed" class="empty-state">研判数据暂不可用，稍后重试</div>

    <template v-else>
      <!-- ① 三个得分（参考图顶部大数字） -->
      <div class="cz-score">
        <div class="cz-score-i s-red"><b>{{ fmt(scores.emotion) }}</b><span>情绪温度</span></div>
        <div class="cz-score-i s-gold"><b>{{ fmt(scores.capital) }}</b><span>资金强度</span></div>
        <div class="cz-score-i s-orange"><b>{{ fmt(scores.promote) }}</b><span>综合晋级率</span></div>
      </div>
      <div class="cz-chips">
        <span class="cz-chip">承接强弱 <b>{{ fmt(scores.support) }}</b></span>
        <span class="cz-chip">涨停 <b>{{ senti.ztCount ?? '—' }}</b> 家</span>
        <span class="cz-chip">连板高度 <b>{{ senti.lbgd ?? '—' }}</b></span>
        <span v-if="emotion.latest && emotion.latest.phase" class="cz-chip">最新阶段 <b>{{ emotion.latest.phase }}</b></span>
      </div>

      <!-- ② 情绪预判：柱=涨停家数，按所选阶段高亮 -->
      <section class="cz-card">
        <div class="cz-card-h">
          <span class="cz-card-t">情绪预判</span>
          <span class="cz-tabs">
            <button
              v-for="p in phaseTabs" :key="p" class="cz-tab"
              :class="{ on: phase === p }" @click="phase = phase === p ? '' : p"
            >{{ p }}</button>
          </span>
        </div>
        <div v-if="emotion.series && emotion.series.length" class="cz-chart">
          <div
            v-for="d in emotion.series" :key="d.date" class="cz-col"
            :class="{ dim: phase && d.phase !== phase }"
          >
            <span class="cz-col-v">{{ d.zt }}</span>
            <i class="cz-col-bar" :style="{ height: barH(d.zt, maxZt) }"></i>
            <span class="cz-col-x">{{ d.date.slice(5) }}</span>
          </div>
        </div>
        <div v-else class="cz-empty">情绪序列暂无数据（需 `limit_history` 有落库；二期起按日快照补齐）</div>
        <div class="cz-legend">
          柱高 = 当日涨停家数<template v-if="phase"> · 高亮 = <b>{{ phase }}</b> 阶段</template>
          · 阶段判定：转强(回封率≥40%) &gt; 升温(≥2 项改善) &gt; 分歧
        </div>
      </section>

      <!-- ③ 资金预判：三种口径可切 -->
      <section class="cz-card">
        <div class="cz-card-h">
          <span class="cz-card-t">资金预判</span>
          <span class="cz-tabs">
            <button
              v-for="m in capMetrics" :key="m.key" class="cz-tab"
              :class="{ on: metric === m.key }" @click="metric = m.key"
            >{{ m.label }}</button>
          </span>
        </div>
        <div v-if="capital.series && capital.series.length" class="cz-chart">
          <div v-for="d in capital.series" :key="d.date" class="cz-col">
            <span class="cz-col-v">{{ capVal(d) }}</span>
            <i class="cz-col-bar b-blue" :style="{ height: barH(Math.abs(capRaw(d)), maxCap) }"></i>
            <span class="cz-col-x">{{ d.date.slice(5) }}</span>
          </div>
        </div>
        <div v-else class="cz-empty">资金序列暂无数据（需 `snapshot_bid` 9:25 行落库）</div>
        <div class="cz-legend">
          {{ capHint }}<br>
          承接强弱 = 昨涨停股今日竞价平均涨幅 ÷ 今日炸板率（越高 = 承接越强；当日池未落库时回退库内最近交易日）
        </div>
      </section>

      <!-- ④ 个股研判（双模型分数 金睛/火眼 + 风险 + 标签） -->
      <section class="cz-card">
        <div class="cz-card-h">
          <span class="cz-card-t">个股研判</span>
          <span class="cz-cnt">共 {{ picks.length }} 只</span>
        </div>
        <div v-if="picks.length" class="cz-list">
          <div v-for="p in picks" :key="p.code" class="cz-row" :data-cz="p.code">
            <span class="cz-line1">
              <span class="cz-name">{{ p.name }}</span>
              <span class="cz-code">{{ p.code }}</span>
              <span class="cz-tag" :class="'t-' + p.tag">{{ p.tag }}</span>
              <span class="cz-risk" :class="'r-' + p.risk">{{ riskText(p.risk) }}</span>
            </span>
            <span class="cz-line2">
              <span class="cz-sc">金睛<b>{{ p.scoreXgb ?? '—' }}</b>·火眼<b>{{ p.scoreLgb ?? '—' }}</b></span>
              <span class="cz-chg" :class="(p.change || 0) >= 0 ? 'up' : 'down'">{{ signed(p.change) }}%</span>
              <span v-if="p.concept" class="cz-concept">{{ p.concept }}</span>
            </span>
          </div>
        </div>
        <div v-else class="cz-empty">暂无个股研判数据</div>
      </section>

      <!-- ⑤ 底部按钮 -->
      <div class="cz-foot">
        <button class="cz-btn ghost" @click="goDetail">查看评分详情</button>
        <button class="cz-btn main" :disabled="!picks.length" @click="addPool">加入自选</button>
      </div>

      <!-- 降级说明（如实展示） -->
      <div v-if="notes.length" class="cz-notes">
        <div class="cz-notes-t"><i class="fa fa-info-circle"></i> 数据说明</div>
        <div v-for="(n, i) in notes" :key="i" class="cz-note">· {{ n }}</div>
        <div v-if="meta.pickDate" class="cz-note">· 模型数据日期：{{ meta.pickDate }}</div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { chaozhiOverview } from '../api/kpl'
import { usePolling } from '../composables/usePolling'
import { usePoolStore } from '../stores/pool'
import { isIntradayNow } from '../utils/time'
import { showToast } from '../utils/toast'

const router = useRouter()
const pool = usePoolStore()

const loading = ref(true)
const failed = ref(false)
const date = ref('')
const scores = ref({ emotion: null, capital: null, promote: null, support: null })
const emotion = ref({ series: [], latest: {} })
const capital = ref({ series: [], latest: {} })
const picks = ref([])
const senti = ref({})
const meta = ref({})

const phase = ref('')
const metric = ref('bidAmt')   // 默认看竞价额: 三列里唯一每天都有采集（主力净额/量比有整日缺口）
const phaseTabs = ['升温', '分歧', '转强']
const capMetrics = [
  { key: 'bidAmt', label: '竞价额' },
  { key: 'mainNet', label: '主力净额' },
  { key: 'volRatio', label: '竞价量比' },
]

const notes = computed(() => (meta.value.notes || []))
const maxZt = computed(() => Math.max(1, ...(emotion.value.series || []).map((d) => d.zt || 0)))
const maxCap = computed(() => Math.max(1, ...(capital.value.series || []).map((d) => Math.abs(capRaw(d)))))
const capHint = computed(() => {
  const m = { mainNet: '主力净额 = 当日 9:25 全市场竞价主力净额合计（本地库）',
              bidAmt: '竞价额 = 当日 9:25 全市场竞价成交额合计（本地库）',
              volRatio: '竞价量比 = 当日 9:25 全市场平均竞价量比（本地库）' }
  return m[metric.value] || ''
})

function fmt(v) { return (v === null || v === undefined || v === '') ? '--' : v }
function signed(v) {
  const n = Number(v || 0)
  return (n >= 0 ? '+' : '') + n.toFixed(2)
}
function barH(v, max) { return Math.max(4, Math.round((Math.abs(Number(v) || 0) / (max || 1)) * 100)) + '%' }
function riskText(r) { return r === 'high' ? '高风险' : (r === 'mid' ? '中风险' : '低风险') }
function capRaw(d) { return d[metric.value] || 0 }
/** 采集缺口的日子该列是 null ⇒ 显示 —（**不能显示 0**，会被读成"净额为 0"） */
function capVal(d) {
  const v = d[metric.value]
  if (v === null || v === undefined) return '—'
  if (metric.value === 'volRatio') return Number(v).toFixed(2)
  return (Number(v) / 1e8).toFixed(0) + '亿'   // 元 → 亿（整数，10 个点放得下）
}

function goDetail() { router.push('/aipick') }
function addPool() {
  const list = picks.value.slice(0, 30).map((p) => ({
    code: p.code,
    name: p.name,
    bidChange: p.bidChange,
    probability: (p.scoreXgb ?? p.scoreLgb ?? 0) / 100,
  }))
  try {
    pool.addStocks(list)
    showToast('已加入自选 ' + list.length + ' 只', 'success')
  } catch (e) {
    showToast('加入自选失败：' + (e.message || '未知错误'), 'error')
  }
}

async function load() {
  try {
    const d = await chaozhiOverview()
    if (!d || d.ok !== true) throw new Error((d && d.msg) || '接口未返回 ok')
    date.value = d.date || ''
    scores.value = d.scores || scores.value
    emotion.value = d.emotion || emotion.value
    capital.value = d.capital || capital.value
    picks.value = d.picks || []
    senti.value = d.senti || {}
    meta.value = d.meta || {}
    failed.value = false
  } catch (e) { failed.value = picks.value.length === 0 } finally { loading.value = false }
}

onMounted(load)
// 60s、仅盘中（与后端 OVERVIEW_TTL=60 同频；开盘啦/AI 都是付费或配额敏感资源）
usePolling(() => { if (isIntradayNow()) load() }, 60000, { immediate: false })
</script>

<style scoped>
.cz-root { max-width: 980px; margin: 0 auto; padding-bottom: calc(70px + env(safe-area-inset-bottom)); }

.cz-head { display: flex; align-items: center; gap: 8px; margin-bottom: 9px; }
.cz-logo {
  width: 30px; height: 30px; border-radius: 9px; flex: 0 0 auto;
  background: linear-gradient(145deg, var(--qg-purple-a), var(--qg-purple-b));
  display: flex; align-items: center; justify-content: center;
  color: var(--qg-on); font-size: 0.9rem; font-weight: 800;
}
.cz-head-txt { flex: 1 1 auto; min-width: 0; }
.cz-h1 { font-size: 1.05rem; font-weight: 700; color: var(--text-main); }
.cz-sub { font-size: 0.62rem; color: var(--text-muted); margin-top: 1px; }
.cz-date { font-size: 0.64rem; color: var(--text-dim); }

/* 得分卡：三色大数字（参考图顶部） */
.cz-score { display: grid; grid-template-columns: repeat(3, 1fr); gap: 7px; }
.cz-score-i {
  background: linear-gradient(160deg, var(--bg-card), var(--bg-panel));
  border: 1px solid var(--border-soft); border-left-width: 3px;
  border-radius: 10px; padding: 8px 9px; display: flex; flex-direction: column; gap: 2px;
}
.cz-score-i b { font-size: 1.5rem; line-height: 1.1; font-weight: 800; }
.cz-score-i span { font-size: 0.62rem; color: var(--text-muted); }
.s-red { border-left-color: var(--qg-red-a); }
.s-red b { color: var(--qg-red-a); }
.s-gold { border-left-color: var(--qg-gold-a); }
.s-gold b { color: var(--qg-gold-a); }
.s-orange { border-left-color: var(--qg-orange-a); }
.s-orange b { color: var(--qg-orange-a); }

.cz-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 7px; }
.cz-chip {
  font-size: 0.64rem; color: var(--text-muted); background: var(--bg-input);
  border: 1px solid var(--border-soft); border-radius: 12px; padding: 3px 9px;
}
.cz-chip b { color: var(--text-secondary); margin-left: 2px; }

/* 卡片 */
.cz-card { margin-top: 9px; background: var(--bg-card); border: 1px solid var(--border-soft); border-radius: 11px; padding: 9px 10px 10px; }
.cz-card-h { display: flex; align-items: center; gap: 8px; }
.cz-card-t { font-size: 0.78rem; font-weight: 700; color: var(--text-main); }
.cz-cnt { margin-left: auto; font-size: 0.62rem; color: var(--text-muted); }
.cz-tabs { margin-left: auto; display: flex; gap: 5px; }
.cz-tab {
  background: var(--bg-input); border: 1px solid var(--border-soft); border-radius: 11px;
  color: var(--text-muted); font-size: 0.64rem; padding: 3px 8px; cursor: pointer;
}
.cz-tab.on { background: var(--accent); border-color: var(--accent); color: var(--qg-on); }

/* 柱状图（纯 CSS，10 个点，不引图表库） */
.cz-chart { display: flex; align-items: flex-end; gap: 4px; height: 96px; margin: 10px 2px 4px; }
.cz-col { flex: 1 1 0; min-width: 0; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; height: 100%; gap: 2px; }
.cz-col.dim { opacity: 0.28; }
.cz-col-v { font-size: 0.55rem; color: var(--text-muted); }
.cz-col-bar { width: 100%; max-width: 26px; border-radius: 4px 4px 0 0; background: linear-gradient(180deg, var(--qg-red-a), var(--qg-red-b)); }
.b-blue { background: linear-gradient(180deg, var(--qg-blue-a), var(--qg-blue-b)); }
.cz-col-x { font-size: 0.52rem; color: var(--text-dim); }
.cz-legend { font-size: 0.58rem; color: var(--text-dim); line-height: 1.5; margin-top: 3px; }
.cz-empty { font-size: 0.7rem; color: var(--text-muted); padding: 12px 0; text-align: center; }

/* 个股列表 */
.cz-list { margin-top: 7px; display: flex; flex-direction: column; }
.cz-row { padding: 6px 2px; border-top: 1px solid var(--border-soft); }
.cz-line1 { display: flex; align-items: center; gap: 6px; }
.cz-name { font-size: 0.8rem; font-weight: 600; color: var(--text-main); }
.cz-code { font-size: 0.6rem; color: var(--text-dim); }
.cz-risk { margin-left: auto; font-size: 0.58rem; border-radius: 4px; padding: 1px 5px; }
.r-low { color: var(--qg-blue-a); border: 1px solid var(--qg-blue-a); }
.r-mid { color: var(--qg-orange-a); border: 1px solid var(--qg-orange-a); }
.r-high { color: var(--qg-red-a); border: 1px solid var(--qg-red-a); }
.cz-tag { font-size: 0.58rem; border-radius: 4px; padding: 1px 5px; color: var(--qg-on); background: var(--text-dim); }
.t-关注 { background: var(--qg-red-a); }
.t-观察 { background: var(--qg-orange-a); }
.t-待定 { background: var(--qg-purple-a); }
.t-谨慎 { background: var(--qg-blue-b); }
.cz-line2 { display: flex; align-items: baseline; gap: 8px; margin-top: 3px; font-size: 0.62rem; color: var(--text-muted); }
.cz-sc b { color: var(--qg-gold-a); font-weight: 700; margin: 0 1px; }
.cz-chg { font-weight: 700; }
.cz-chg.up { color: var(--qg-red-a); }
.cz-chg.down { color: var(--qg-blue-a); }
.cz-concept { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* 底部按钮 */
.cz-foot { display: flex; gap: 9px; margin-top: 12px; }
.cz-btn { flex: 1 1 0; border-radius: 10px; padding: 11px 0; font-size: 0.8rem; font-weight: 600; cursor: pointer; }
.cz-btn.ghost { background: var(--bg-input); border: 1px solid var(--border-soft); color: var(--text-secondary); }
.cz-btn.main { background: linear-gradient(135deg, var(--qg-orange-a), var(--qg-red-a)); border: none; color: var(--qg-on); }
.cz-btn:disabled { opacity: 0.5; }

.cz-notes { margin-top: 10px; border: 1px dashed var(--border-soft); border-radius: 9px; padding: 8px 10px; }
.cz-notes-t { font-size: 0.66rem; color: var(--qg-gold-a); margin-bottom: 3px; }
.cz-note { font-size: 0.6rem; color: var(--text-muted); line-height: 1.6; }
</style>
