<template>
  <div v-if="loaded" class="sd-wrap">
    <!-- ===== ① 头部 ===== -->
    <div class="sd-head">
      <div class="sd-hl">
        <span class="sd-name">{{ d.name || name || '—' }}</span>
        <span class="sd-code">{{ code }}</span>
        <span v-if="d.lb > 0" class="sd-lb">昨{{ d.lb }}板</span>
        <span
v-if="risk && risk.warn_level" class="sd-risk"
              :class="risk.warn_level"
>{{ risk.warn_level === 'red' ? '严重异动' : '异动风险' }}</span>
      </div>
      <div class="sd-hr">
        <span class="sd-chg" :class="chgCls">{{ chgText }}</span>
        <span v-if="d.bidChange != null" class="sd-bid" :class="d.bidChange >= 0 ? 'up' : 'down'">
          竞涨 {{ fmtPct(d.bidChange) }}</span>
      </div>
      <div class="sd-metrics">
        <div class="sd-m"><span class="sd-mk">竞额</span><b>{{ d.bidAmt != null ? fmtNum(d.bidAmt) + '万' : '—' }}</b></div>
        <div class="sd-m"><span class="sd-mk">自由流通</span><b>{{ d.freeCirculationMV != null ? fmtNum(d.freeCirculationMV) + '亿' : '—' }}</b></div>
        <div class="sd-m"><span class="sd-mk">可信</span><b>{{ score ? score.confidence + '%' : '—' }}</b></div>
      </div>
    </div>

    <!-- ===== ② 为什么选它 ===== -->
    <div v-if="score" class="sd-block">
      <div class="sd-title">为什么选它<span class="sd-hint">综合评分 {{ score.probability }}</span></div>
      <div class="sd-score">
        <div class="sd-ring" :style="ringStyle">
          <div class="sd-ring-inner">
            <b>{{ score.probability }}</b><span>评分</span>
          </div>
        </div>
        <div class="sd-factors">
          <div v-for="f in score.parts" :key="f.key" class="sd-f">
            <div class="sd-f-top">
              <span class="sd-f-lb">{{ f.label }}</span>
              <span class="sd-f-val" :title="factorValTip(f)">{{ factorValText(f) }}</span>
            </div>
            <div class="sd-f-bar">
              <div
class="sd-f-fill" :class="'lv' + Math.min(5, Math.max(1, Math.round((f.score || 0) * 5)))"
                   :style="{ width: pct(f.score) }"
></div>
            </div>
            <div class="sd-f-w">权重 {{ fmtW(f.weight) }}</div>
          </div>
        </div>
      </div>
    </div>

    <!-- ===== ③ 所属题材 ===== -->
    <div v-if="concepts.length" class="sd-block">
      <div class="sd-title">所属题材<span class="sd-hint">{{ concepts.length }} 个</span></div>
      <div class="sd-tags">
        <span v-for="(c, i) in concepts" :key="i" class="sd-tag">{{ c }}</span>
      </div>
    </div>

    <!-- ===== ④ 历史战绩 ===== -->
    <div v-if="history.length" class="sd-block">
      <div class="sd-title">历史战绩<span class="sd-hint">近 {{ history.length }} 次入选</span></div>
      <div class="sd-tl">
        <div v-for="(h, i) in history" :key="i" class="sd-tl-item">
          <span class="sd-tl-dot" :class="dotCls(h.realChange)"></span>
          <div class="sd-tl-body">
            <div class="sd-tl-date">{{ h.date }} <span class="sd-tl-act">{{ h.action === 'lock' ? '锁定' : '筛选' }}</span></div>
            <div class="sd-tl-txt">评分 {{ h.probability }} · 当日 <b :class="chgNumCls(h.realChange)">{{ fmtPctSigned(h.realChange) }}</b></div>
          </div>
        </div>
      </div>
    </div>

    <!-- ===== ⑤ 风险提示 ===== -->
    <div v-if="risk && (risk.warn_msg || risk.max_range)" class="sd-block">
      <div class="sd-title">风险提示</div>
      <div class="sd-riskbox" :class="risk.warn_level || 'yellow'">
        <span v-if="risk.warn_level === 'red'" class="sd-risk-ic">●</span>
        <span v-else class="sd-risk-ic">◐</span>
        <span>{{ risk.warn_msg || risk.max_range }}</span>
      </div>
    </div>

    <div class="sd-foot">数据来源：最近交易日 9:25 定格快照 · 评分构成已解除保密展示</div>
  </div>

  <!-- 加载 / 空态 -->
  <div v-else class="sd-state">
    <span v-if="loading" class="sd-loading"><i class="fa fa-spinner fa-spin"></i> 加载详情…</span>
    <span v-else class="sd-empty">{{ errMsg || '暂无详情数据' }}</span>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { stockDetail } from '../api/stocks'

const props = defineProps({
  code: { type: String, default: '' },
  name: { type: String, default: '' },
})

const d = ref(null)         // 详情基础字段
const score = ref(null)     // 评分构成
const risk = ref(null)      // 异动风险
const history = ref([])     // 历史战绩
const loading = ref(false)
const errMsg = ref('')
const loaded = ref(false)

const ACCENT = getComputedStyle(document.documentElement).getPropertyValue('--accent') || '#ff7a5c'

function fmtPct(v) { return (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%' }
function fmtPctSigned(v) { return v == null ? '—' : fmtPct(v) }
function fmtNum(v) { return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 1 }) }
function pct(s) { return Math.max(0, Math.min(100, (s || 0) * 100)) + '%' }
function fmtW(w) { return (w == null ? 0 : Number(w) * 100).toFixed(0) + '%' }

const chgText = computed(() => {
  const v = d.value && (d.value.realChange ?? d.value.bidChange)
  return v == null ? '—' : fmtPctSigned(v)
})
const chgCls = computed(() => {
  const v = d.value && (d.value.realChange ?? d.value.bidChange)
  return v == null ? '' : (v >= 0 ? 'up' : 'down')
})

const ringStyle = computed(() => {
  const p = score.value ? score.value.probability : 0
  return { background: `conic-gradient(${ACCENT} ${p * 3.6}deg, rgba(255,255,255,0.08) 0deg)` }
})

const concepts = computed(() => {
  const raw = (d.value && (d.value.board || '')) || ''
  if (!raw) return []
  return raw.split(/[、,，]/).map(s => s.trim()).filter(Boolean).slice(0, 8)
})

function factorValText(f) {
  const v = f.value
  if (v == null) return '—'
  switch (f.key) {
    case 'bid': return fmtPctSigned(v)
    case 'activity': return Number(v).toFixed(2) + '%'
    case 'market': return Number(v).toFixed(1) + '亿'
    case 'warn': return '强度 ' + Number(v).toFixed(2)
    case 'yesterday': return fmtPctSigned(v)
    default: return String(v)
  }
}
function factorValTip(f) {
  const v = f.value
  if (v == null) return '数据缺失，按中性分处理'
  return f.label + ' 实际值：' + factorValText(f)
}
function dotCls(r) { return r == null ? '' : (r >= 0 ? 'up' : 'down') }
function chgNumCls(r) { return r == null ? '' : (r >= 0 ? 'up' : 'down') }

async function load() {
  if (!props.code) return
  loading.value = true
  errMsg.value = ''
  try {
    const r = await stockDetail(props.code)
    if (!r || !r.ok) { errMsg.value = (r && r.msg) || '详情加载失败'; loaded.value = true; return }
    d.value = r
    score.value = r.score || null
    risk.value = r.risk || null
    history.value = (r.history || []).slice(0, 8)
    loaded.value = true
  } catch (e) {
    errMsg.value = (e && e.message) || '请求异常'
    loaded.value = true
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => props.code, () => { if (props.code) { loaded.value = false; load() } })
</script>

<style scoped>
.sd-wrap { height: 100%; overflow-y: auto; padding: 14px 16px; }
.sd-wrap::-webkit-scrollbar { width: 6px; }
.sd-wrap::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.12); border-radius: 3px; }

.sd-head { border-bottom: 1px solid var(--border-soft, #1f2937); padding-bottom: 12px; }
.sd-hl { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.sd-name { font-size: 1.0625rem; font-weight: 800; color: var(--text-main, #f3f4f6); }
.sd-code { font-size: 0.8125rem; color: var(--text-secondary, #9ca3af); }
.sd-lb { padding: 1px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 700; color: #fff; background: #b3261e; border: 1px solid rgba(255, 92, 92, 0.6); }
.sd-risk { padding: 1px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }
.sd-risk.red { color: #ff6b6b; background: rgba(255, 77, 79, 0.12); border: 1px solid rgba(255, 77, 79, 0.5); }
.sd-risk.yellow { color: #ffb020; background: rgba(255, 176, 32, 0.1); border: 1px solid rgba(255, 176, 32, 0.4); }

.sd-hr { margin-top: 8px; display: flex; align-items: baseline; gap: 10px; }
.sd-chg { font-size: 1.5rem; font-weight: 800; font-variant-numeric: tabular-nums; }
.sd-chg.up, .sd-bid.up { color: var(--up, #ff5c5c); }
.sd-chg.down, .sd-bid.down { color: var(--down, #3db97f); }
.sd-bid { font-size: 0.8125rem; }

.sd-metrics { margin-top: 10px; display: flex; gap: 20px; }
.sd-m { display: flex; flex-direction: column; gap: 2px; }
.sd-mk { font-size: 0.6875rem; color: var(--text-muted, #6b7280); }
.sd-m b { font-size: 0.875rem; color: var(--text-main, #f3f4f6); font-variant-numeric: tabular-nums; }

.sd-block { margin-top: 14px; }
.sd-title { display: flex; align-items: baseline; gap: 6px; font-size: 0.9375rem; font-weight: 700; color: var(--text-main, #f3f4f6); margin-bottom: 10px; }
.sd-title::before { content: ""; width: 3px; height: 14px; background: var(--accent, #ff7a5c); border-radius: 2px; align-self: center; }
.sd-hint { margin-left: auto; font-size: 0.6875rem; color: var(--text-muted, #6b7280); font-weight: 400; }

.sd-score { display: flex; gap: 16px; align-items: center; }
.sd-ring { position: relative; width: 88px; height: 88px; border-radius: 50%; flex-shrink: 0; display: flex; align-items: center; justify-content: center; }
.sd-ring-inner { width: 66px; height: 66px; border-radius: 50%; background: var(--bg-panel-solid, #0f172a); display: flex; flex-direction: column; align-items: center; justify-content: center; }
.sd-ring-inner b { font-size: 1.4rem; color: var(--text-main, #f3f4f6); line-height: 1.1; }
.sd-ring-inner span { font-size: 0.625rem; color: var(--text-muted, #6b7280); }

.sd-factors { flex: 1; min-width: 0; }
.sd-f { margin-bottom: 8px; }
.sd-f-top { display: flex; justify-content: space-between; font-size: 0.75rem; margin-bottom: 3px; }
.sd-f-lb { color: var(--text-secondary, #9ca3af); }
.sd-f-val { color: var(--text-main, #f3f4f6); font-variant-numeric: tabular-nums; }
.sd-f-bar { height: 5px; background: rgba(255,255,255,0.07); border-radius: 3px; overflow: hidden; }
.sd-f-fill { height: 100%; border-radius: 3px; }
.sd-f-fill.lv1 { background: #ff5c5c; }
.sd-f-fill.lv2 { background: #ff8a5c; }
.sd-f-fill.lv3 { background: #ffb020; }
.sd-f-fill.lv4 { background: #7aa2ff; }
.sd-f-fill.lv5 { background: #c58aff; }
.sd-f-w { font-size: 0.625rem; color: var(--text-muted, #6b7280); margin-top: 2px; }

.sd-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.sd-tag { padding: 3px 10px; border-radius: 6px; font-size: 0.75rem; color: var(--text-secondary, #9ca3af); background: rgba(255,255,255,0.05); border: 1px solid var(--border-soft, #1f2937); }

.sd-tl { position: relative; padding-left: 16px; }
.sd-tl::before { content: ""; position: absolute; left: 4px; top: 4px; bottom: 4px; width: 2px; background: var(--border-soft, #1f2937); }
.sd-tl-item { position: relative; padding: 5px 0; }
.sd-tl-dot { position: absolute; left: -15px; top: 10px; width: 9px; height: 9px; border-radius: 50%; background: var(--bg-panel-solid, #0f172a); border: 2px solid var(--text-muted, #6b7280); }
.sd-tl-dot.up { border-color: var(--up, #ff5c5c); }
.sd-tl-dot.down { border-color: var(--down, #3db97f); }
.sd-tl-date { font-size: 0.6875rem; color: var(--text-muted, #6b7280); }
.sd-tl-act { color: var(--accent, #ff7a5c); margin-left: 4px; }
.sd-tl-txt { font-size: 0.8125rem; color: var(--text-secondary, #9ca3af); }
.sd-tl-txt b { font-variant-numeric: tabular-nums; }
.up { color: var(--up, #ff5c5c) !important; }
.down { color: var(--down, #3db97f) !important; }

.sd-riskbox { display: flex; gap: 8px; padding: 10px 12px; border-radius: 8px; font-size: 0.8125rem; line-height: 1.6; color: var(--text-secondary, #9ca3af); }
.sd-riskbox.red { background: rgba(255, 77, 79, 0.08); border: 1px solid rgba(255, 77, 79, 0.35); }
.sd-riskbox.yellow { background: rgba(255, 176, 32, 0.08); border: 1px solid rgba(255, 176, 32, 0.35); }
.sd-risk-ic { flex-shrink: 0; color: var(--amber, #ffb020); font-size: 0.75rem; line-height: 1.6; }

.sd-foot { margin-top: 14px; padding-top: 8px; border-top: 1px dashed var(--border-soft, #1f2937); font-size: 0.625rem; color: var(--text-muted, #6b7280); }

.sd-state { height: 100%; display: flex; align-items: center; justify-content: center; color: var(--text-muted, #6b7280); font-size: 0.875rem; }
.sd-loading i { color: var(--accent, #ff7a5c); }
.sd-empty { font-size: 0.875rem; }
</style>
