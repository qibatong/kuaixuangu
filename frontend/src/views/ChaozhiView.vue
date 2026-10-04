<template>
  <!--
    超智研判（聚合页，2026-10-01 新增；原「AI预测」升级）
    数据 = 后端 `/api/chaozhi/overview`（只读聚合，零新增上游出网；见 backend/app/services/chaozhi.py）
    口径 = docs/超智研判-聚合页开发方案-20261001.md §四 A 案；一期已做：得分卡 + 两条 10 日序列 + 双模型个股 + 标签
    ⚠️ 降级如实展示：`meta.notes` 里有什么就显示什么（例如「火眼 LGB 当日无预测文件」），
       分数缺失显示 `—` 而**不是 0**（0 会被误读成"模型给了 0 分"）。
  -->
  <!-- 2026-10-03 主人指令: 本页内容**常驻**电脑端竞价左栏顶部(≥1100px 双栏), 切任何 tab 都能看到
       ⇒ 新增 `embedded` 模式: ① 不带 h1(宿主 StockView 已有自己的 h1, 一页两个 h1 破坏大纲)
          ② 去掉整页容器的 max-width / 底部安全区 ③ 卡头补「全文 /chaozhi」入口 + 折叠开关
          ④ 个股/影子列表限高内滚(避免 60 行名单把下方竞价名单顶出视野) -->
  <div class="page-shell cz-root" :class="{ 'cz-embed': embedded }">
    <h1 v-if="!embedded" class="visually-hidden">超智研判</h1>

    <header class="cz-head" :class="{ 'cz-head-embed': embedded }">
      <span class="cz-logo" aria-hidden="true">智</span>
      <div class="cz-head-txt">
        <div class="cz-h1">超智研判</div>
        <div class="cz-sub">双模型 金睛 + 火眼 · 资金 &amp; 情绪预判</div>
      </div>
      <span class="cz-date">{{ date || '—' }}</span>
      <!-- 嵌入模式下给两个出口：跳完整页 + 就地折叠(状态记 localStorage, 刷新保持) -->
      <button v-if="embedded" class="cz-more" title="打开超智研判完整页" @click="goFull">全文 ›</button>
      <button
        v-if="embedded" class="cz-fold" :aria-expanded="String(open)" :title="open ? '收起' : '展开'"
        :aria-label="open ? '收起超智研判' : '展开超智研判'" @click="toggleOpen"
      >{{ open ? '▾' : '▸' }}</button>
    </header>

    <div v-show="open" class="cz-body">
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
        <!-- 2026-10-03 主人指令：说明文字不在前端展示（原「柱高/阶段判定」图例已移除） -->
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
        <!-- 2026-10-03 主人指令：说明文字不在前端展示（原「竞价额/承接强弱」图例已移除） -->
      </section>

      <!-- ④ 个股研判（双模型分数 金睛/火眼 + 风险 + 标签） -->
      <section class="cz-card">
        <div class="cz-card-h">
          <span class="cz-card-t">个股研判</span>
          <span class="cz-cnt">共 {{ picks.length }} 只</span>
          <!-- ★ 2026-10-02 主人指令: 宫格「超智研判」改为直跳本页后, 金睛/火眼两页**没有入口了**
               ⇒ 在本卡（双模型分数列表）头部补两个直达入口 —— 就近原则: 用户看到「金睛94 · 火眼—」
               时最想点进去看整页名单。路径与宫格旧子项/路由表一致: /aipick(金睛) /aipick-lgb(火眼)。 -->
          <span class="cz-tabs">
            <button class="cz-mini" data-cz-go="jing" title="看金睛(XGB)完整名单"
                    @click="goModel('xgb')">金睛名单 ›</button>
            <button class="cz-mini" data-cz-go="huo" title="看火眼(LightGBM)完整名单"
                    @click="goModel('lgb')">火眼名单 ›</button>
          </span>
        </div>
        <!-- 嵌入左栏时限高内滚：后端 top=60，60 行会把下方的竞价名单顶出视野 -->
        <div v-if="picks.length" class="cz-list" :class="{ 'cz-list-scroll': embedded }">
          <div v-for="p in picks" :key="p.code" class="cz-row" :data-cz="p.code">
            <span class="cz-line1">
              <span class="cz-name">{{ p.name }}</span>
              <span class="cz-code">{{ p.code }}</span>
            </span>
            <span class="cz-line2">
              <!-- ★ 2026-10-03 主人指令（信息精简）：只留 1 个主分（综合分）+ 竞价涨幅 + 封板结果；
                   去掉 风险档位/可买性标签/双模型概率/融合概率/共识/分歧（噪声，用户不看）。 -->
              <span class="cz-fused">综合<b>{{ p.scoreFused ?? '—' }}</b></span>
              <span class="cz-chg" :class="(p.change || 0) >= 0 ? 'up' : 'down'"><i class="cz-chg-kind">{{ chgKindText(p) }}</i>{{ signed(p.change) }}%</span>
              <span v-if="p.concept" class="cz-concept">{{ p.concept }}</span>
              <span v-if="p.isLimitUp === 1" class="cz-pk cz-pk-zt">已封板</span>
              <span v-else-if="p.isLimitUp === 0" class="cz-pk cz-pk-no">未封板</span>
            </span>
          </div>
        </div>
        <div v-else class="cz-empty">暂无个股研判数据</div>
        <!-- 2026-10-03 主人指令：说明文字不在前端展示（原「综合分/名单口径」图例已移除，
             且其内容已过期：竞价额≥3000万 / 涨幅≤10% 均已改） -->
      </section>

      <!-- ⑤ 影子模型（2026-10-03 上线；主人要求展示在超智内）—— 内部验证中，⚠️ 不对外发布 -->
      <section v-if="shadow.enabled" class="cz-card cz-shadow">
        <div class="cz-card-h">
          <span class="cz-card-t">影子模型 · 内部验证</span>
          <span class="cz-cnt">不对外</span>
        </div>
        <div class="cz-note">
          候选 <b>{{ shadow.candidate || '—' }}</b>（{{ (shadow.candidateTrainedAt || '—').slice(0, 16) }} 训练）·
          数据日 {{ shadow.date }} · 当日 top10 封板
          <b>{{ shadow.hit?.hit }}/{{ shadow.hit?.total }}</b>
          <b v-if="shadow.hit?.rate != null">（{{ shadow.hit.rate }}%）</b>
        </div>
        <div class="cz-list" :class="{ 'cz-list-scroll': embedded }">
          <div v-for="(p, i) in shadow.picks" :key="p.code" class="cz-row">
            <span class="cz-line1">
              <span class="cz-name">{{ i + 1 }}. {{ p.name || '—' }}</span>
              <span class="cz-code">{{ p.code }}</span>
              <span v-if="p.isLimitUp === 1" class="cz-pk cz-pk-zt">已封板</span>
              <span v-else-if="p.isLimitUp === 0" class="cz-pk cz-pk-no">未封板</span>
              <span v-else class="cz-pk">待回填</span>
            </span>
            <span class="cz-line2">
              <span class="cz-fused">分数<b>{{ p.score }}</b></span>
              <span class="cz-models">模型 {{ p.modelVer || '—' }}</span>
            </span>
          </div>
        </div>
        <div v-if="shadow.gate" class="cz-note">
          闸门（{{ shadow.gate.nDays }} 交易日同日对拍）：top3 {{ signed(shadow.gate.top3Pp) }}pp ·
          top5 {{ signed(shadow.gate.top5Pp) }}pp · top10 {{ signed(shadow.gate.top10Pp) }}pp ·
          top30 {{ signed(shadow.gate.top30Pp) }}pp ⇒ <b>{{ shadow.gate.decisionText }}</b>
        </div>
        <div v-if="shadow.frozen" class="cz-note">⚠️ 已冻结：{{ shadow.freezeReason }}</div>
        <!-- 2026-10-03 主人指令：说明文字不在前端展示（原「闸门说明/免责声明」已移除） -->
      </section>

      <!-- ⑥ 底部按钮 -->
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
    </div><!-- /.cz-body  ← 2026-10-03: 嵌入模式折叠容器 -->
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { chaozhiOverview } from '../api/kpl'
import { usePolling } from '../composables/usePolling'
import { usePoolStore } from '../stores/pool'
import { isIntradayNow } from '../utils/time'
import { showToast } from '../utils/toast'

const router = useRouter()
const pool = usePoolStore()

// 2026-10-03: `embedded` = 常驻在电脑端竞价左栏顶部（StockView），同一份 UI，只是布局约束不同。
const props = defineProps({ embedded: { type: Boolean, default: false } })

const loading = ref(true)
const failed = ref(false)
const date = ref('')
const scores = ref({ emotion: null, capital: null, promote: null, support: null })
const emotion = ref({ series: [], latest: {} })
const capital = ref({ series: [], latest: {} })
const picks = ref([])
const shadow = ref({})            // 影子模型块（内部验证，不对外）
const senti = ref({})
const meta = ref({})

const route = useRoute()
const pickDate = computed(() => String((route.query.pickDate) || ''))

// 折叠状态（**仅嵌入模式**有开关，整页模式永远展开）：用 localStorage 记住，
// 否则用户每次刷新左栏都被整块超智内容顶住。隐私模式/无 storage 时恒展开。
const CZ_OPEN_KEY = 'kuaixuan.cz.embed.open'
const open = ref(true)
if (props.embedded) {
  try {
    if (localStorage.getItem(CZ_OPEN_KEY) === '0') open.value = false
  } catch (e) { /* 无 storage：默认展开 */ }
}
function toggleOpen() {
  open.value = !open.value
  try { localStorage.setItem(CZ_OPEN_KEY, open.value ? '1' : '0') } catch (e) { /* 忽略 */ }
}
function goFull() { router.push('/chaozhi') }

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
// 涨幅**口径**文字（后端 changeKind 给出）：
//   bid = 9:25 集合竞价涨幅（本页常态，取自当日预测文件）；day = 回看历史时该日**已实现**涨幅；
//   realtime = 最新交易日盘中实时涨幅。绝不裸显示数字（主人 2026-10-02："数据读不对，涨幅对不对？"）
function chgKindText(p) {
  const k = p && p.changeKind
  if (k === 'realtime') return '实时 '
  if (k === 'day') return '当日 '
  return '竞价 '
}
function signed(v) {
  const n = Number(v || 0)
  return (n >= 0 ? '+' : '') + n.toFixed(2)
}
function barH(v, max) { return Math.max(4, Math.round((Math.abs(Number(v) || 0) / (max || 1)) * 100)) + '%' }
function riskText(r) { return r === 'high' ? '高风险' : (r === 'mid' ? '中风险' : '低风险') }
/** 一致性标记：0.25 以下=一致；≥0.5=分歧；单模型=证据不足 */
function divCls(p) {
  if (p.divergence === null || p.divergence === undefined) return 'd-single'
  if (p.divergence >= 0.5) return 'd-split'
  if (p.divergence >= 0.25) return 'd-mid'
  return 'd-agree'
}
function divText(p) {
  if (p.divergence === null || p.divergence === undefined) return '单模型'
  if (p.divergence >= 0.5) return '分歧'
  if (p.divergence >= 0.25) return '略分歧'
  return '一致'
}
function capRaw(d) { return d[metric.value] || 0 }
/** 采集缺口的日子该列是 null ⇒ 显示 —（**不能显示 0**，会被读成"净额为 0"） */
function capVal(d) {
  const v = d[metric.value]
  if (v === null || v === undefined) return '—'
  if (metric.value === 'volRatio') return Number(v).toFixed(2)
  return (Number(v) / 1e8).toFixed(0) + '亿'   // 元 → 亿（整数，10 个点放得下）
}

function goDetail() { router.push('/aipick') }
// 2026-10-02: 金睛(XGB)/火眼(LGB) 直达入口（宫格改直跳后这两页失去入口，在个股研判卡头补回）
function goModel(kind) { router.push(kind === 'lgb' ? '/aipick-lgb' : '/aipick') }
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
    const d = await chaozhiOverview(pickDate.value)
    if (!d || d.ok !== true) throw new Error((d && d.msg) || '接口未返回 ok')
    date.value = d.date || ''
    scores.value = d.scores || scores.value
    emotion.value = d.emotion || emotion.value
    capital.value = d.capital || capital.value
    picks.value = d.picks || []
    shadow.value = d.shadow || {}          // 影子模型块（内部验证，不对外）
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

/* 2026-10-03 嵌入左栏(StockView)：不再是整页容器 —— 去掉定宽/居中/底部安全区,
   字级整体收紧一档(左栏只有半屏宽, 整页字号放进来会显得空大)。 */
.cz-embed { max-width: none; margin: 0 0 var(--s2); padding: 0; padding-bottom: 0; }
/* 加载态/空态在左栏要收紧：整页那份 40px 留白会把下方 tab 条顶下一大块 */
.cz-embed .cz-body > .loading-placeholder,
.cz-embed .cz-body > .empty-state { padding: var(--s4); }

.cz-head { display: flex; align-items: center; gap: var(--s2); margin-bottom: var(--s2); }
.cz-head-embed { margin-bottom: var(--s2); }
/* 折叠/全文两个出口的小按钮（嵌左栏专用；用文字符号不引 Font Awesome 新字形） */
.cz-head-embed .cz-more,
.cz-head-embed .cz-fold {
  flex: 0 0 auto;
  border: 1px solid var(--border-soft);
  background: var(--bg-input);
  color: var(--text-muted);
  border-radius: var(--r-pill);
  padding: 2px var(--s2);
  font-size: var(--fs-xs);
  cursor: pointer;
  white-space: nowrap;
}
.cz-head-embed .cz-fold { padding: 2px var(--s2); font-size: var(--fs-xs); line-height: 1.2; }
.cz-head-embed .cz-more:hover,
.cz-head-embed .cz-fold:hover { color: var(--text-main); border-color: var(--qg-orange-a); }
/* 限高内滚：列表自己在块内滚，不把下方竞价名单推走 */
.cz-list-scroll { max-height: 260px; overflow-y: auto; overscroll-behavior: contain; }
.cz-logo {
  width: 30px; height: 30px; border-radius: var(--r-md); flex: 0 0 auto;
  background: linear-gradient(145deg, var(--qg-purple-a), var(--qg-purple-b));
  display: flex; align-items: center; justify-content: center;
  color: var(--qg-on); font-size: var(--fs-base); font-weight: 700;
}
.cz-head-txt { flex: 1 1 auto; min-width: 0; }
.cz-h1 { font-size: var(--fs-lg); font-weight: 700; color: var(--text-main); }
.cz-sub { font-size: var(--fs-xs); color: var(--text-muted); margin-top: 1px; }
.cz-date { font-size: var(--fs-xs); color: var(--text-dim); }

/* 得分卡：三色大数字（参考图顶部） */
.cz-score { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--s2); }
.cz-score-i {
  background: linear-gradient(160deg, var(--bg-card), var(--bg-panel));
  border: 1px solid var(--border-soft); border-left-width: 3px;
  border-radius: var(--r-lg); padding: var(--s2) var(--s2); display: flex; flex-direction: column; gap: 2px;
}
.cz-score-i b { font-size: var(--fs-display); line-height: 1.1; font-weight: 700; }
.cz-score-i span { font-size: var(--fs-xs); color: var(--text-muted); }
.s-red { border-left-color: var(--qg-red-a); }
.s-red b { color: var(--qg-red-a); }
.s-gold { border-left-color: var(--qg-gold-a); }
.s-gold b { color: var(--qg-gold-a); }
.s-orange { border-left-color: var(--qg-orange-a); }
.s-orange b { color: var(--qg-orange-a); }

.cz-chips { display: flex; flex-wrap: wrap; gap: var(--s2); margin-top: var(--s2); }
.cz-chip {
  font-size: var(--fs-xs); color: var(--text-muted); background: var(--bg-input);
  border: 1px solid var(--border-soft); border-radius: var(--r-lg); padding: var(--s1) var(--s2);
}
.cz-chip b { color: var(--text-secondary); margin-left: 2px; }

/* 卡片 */
.cz-card { margin-top: var(--s2); background: var(--bg-card); border: 1px solid var(--border-soft); border-radius: 11px; padding: var(--s2) var(--s2) var(--s2); }
.cz-card-h { display: flex; align-items: center; gap: var(--s2); }
.cz-card-t { font-size: var(--fs-sm); font-weight: 700; color: var(--text-main); }
.cz-cnt { margin-left: auto; font-size: var(--fs-xs); color: var(--text-muted); }
.cz-tabs { margin-left: auto; display: flex; gap: var(--s1); }
/* 2026-10-02: 个股研判卡头的两个直达入口（金睛/火眼）—— 小胶囊，不抢标题视觉 */
.cz-mini {
  border: 1px solid var(--border-soft); background: var(--bg-input); color: var(--text-secondary);
  border-radius: var(--r-pill); padding: var(--s1) var(--s2); font-size: var(--fs-xs); font-weight: 600; cursor: pointer;
  white-space: nowrap;
}
.cz-mini:hover { color: var(--text-main); border-color: var(--qg-orange-a); }
.cz-tab {
  background: var(--bg-input); border: 1px solid var(--border-soft); border-radius: 11px;
  color: var(--text-muted); font-size: var(--fs-xs); padding: var(--s1) var(--s2); cursor: pointer;
}
.cz-tab.on { background: var(--accent); border-color: var(--accent); color: var(--qg-on); }

/* 柱状图（纯 CSS，10 个点，不引图表库） */
.cz-chart { display: flex; align-items: flex-end; gap: var(--s1); height: 96px; margin: var(--s2) 2px var(--s1); }
.cz-col { flex: 1 1 0; min-width: 0; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; height: 100%; gap: 2px; }
.cz-col.dim { opacity: 0.28; }
.cz-col-v { font-size: var(--fs-xs); color: var(--text-muted); }
.cz-col-bar { width: 100%; max-width: 26px; border-radius: 4px 4px 0 0; background: linear-gradient(180deg, var(--qg-red-a), var(--qg-red-b)); }
.b-blue { background: linear-gradient(180deg, var(--qg-blue-a), var(--qg-blue-b)); }
.cz-col-x { font-size: var(--fs-xs); color: var(--text-dim); }
.cz-legend { font-size: var(--fs-xs); color: var(--text-dim); line-height: 1.5; margin-top: var(--s1); }
.cz-empty { font-size: var(--fs-xs); color: var(--text-muted); padding: var(--s3) 0; text-align: center; }

/* 个股列表 */
.cz-list { margin-top: var(--s2); display: flex; flex-direction: column; }
.cz-row { padding: var(--s2) 2px; border-top: 1px solid var(--border-soft); }
.cz-line1 { display: flex; align-items: center; gap: var(--s2); }
.cz-name { font-size: var(--fs-sm); font-weight: 600; color: var(--text-main); }
.cz-code { font-size: var(--fs-xs); color: var(--text-dim); }
.cz-risk { margin-left: auto; font-size: var(--fs-xs); border-radius: var(--r-sm); padding: 1px var(--s1); }
.r-low { color: var(--qg-blue-a); border: 1px solid var(--qg-blue-a); }
.r-mid { color: var(--qg-orange-a); border: 1px solid var(--qg-orange-a); }
.r-high { color: var(--qg-red-a); border: 1px solid var(--qg-red-a); }
.cz-tag { font-size: var(--fs-xs); border-radius: var(--r-sm); padding: 1px var(--s1); color: var(--qg-on); background: var(--text-dim); }
.t-关注 { background: var(--qg-red-a); }
.t-观察 { background: var(--qg-orange-a); }
.t-待定 { background: var(--qg-purple-a); }
.t-谨慎 { background: var(--qg-blue-b); }
.cz-line2 { display: flex; align-items: baseline; gap: var(--s2); margin-top: var(--s1); font-size: var(--fs-xs); color: var(--text-muted); }
.cz-fused { font-size: var(--fs-xs); font-weight: 700; color: var(--text-main); }
.cz-fused b { font-size: var(--fs-md); color: var(--qg-orange-a); margin-left: 2px; }
.cz-models { font-size: var(--fs-xs); color: var(--text-muted); }
.cz-div { font-size: var(--fs-xs); border-radius: var(--r-sm); padding: 0 var(--s1); }
.d-agree { color: var(--qg-blue-a); border: 1px solid var(--qg-blue-a); }
.d-mid { color: var(--qg-gold-a); border: 1px solid var(--qg-gold-a); }
.d-split { color: var(--qg-red-a); border: 1px solid var(--qg-red-a); }
.d-single { color: var(--text-dim); border: 1px solid var(--border-soft); }
.cz-chg { font-weight: 700; }
/* 融合概率 / 共识 / 可买性 / 封板结果（2026-10-03：综合展示两模型预测能涨停的票） */
.cz-fused2 b, .cz-models b { color: #ffd166; }
.cz-consensus { padding: 0 var(--s1); border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 700;
  background: rgba(255, 99, 132, 0.18); color: #ff6384; }
.cz-pk { padding: 0 var(--s1); border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 600; }
.cz-pk-none { background: rgba(255, 82, 82, 0.16); color: var(--brand-soft); }
.cz-pk-low { background: rgba(255, 167, 38, 0.16); color: #ffa726; }
.cz-pk-ok { background: rgba(76, 175, 80, 0.14); color: #66bb6a; }
.cz-pk-zt { background: rgba(255, 82, 82, 0.22); color: var(--accent); }
.cz-pk-no { background: rgba(255, 255, 255, 0.08); color: rgba(255,255,255,.55); }
/* 涨幅的口径小字（竞价/当日/实时）—— 小、弱、不抢数字 */
.cz-chg-kind { margin-right: 2px; font-size: var(--fs-xs); font-style: normal; font-weight: 400; opacity: 0.62; }
.cz-chg.up { color: var(--qg-red-a); }
.cz-chg.down { color: var(--qg-blue-a); }
.cz-concept { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* 底部按钮 */
.cz-foot { display: flex; gap: var(--s2); margin-top: var(--s3); }
.cz-btn { flex: 1 1 0; border-radius: var(--r-lg); padding: var(--s3) 0; font-size: var(--fs-sm); font-weight: 600; cursor: pointer; }
.cz-btn.ghost { background: var(--bg-input); border: 1px solid var(--border-soft); color: var(--text-secondary); }
.cz-btn.main { background: linear-gradient(135deg, var(--qg-orange-a), var(--qg-red-a)); border: none; color: var(--qg-on); }
.cz-btn:disabled { opacity: 0.5; }

.cz-notes { margin-top: var(--s2); border: 1px dashed var(--border-soft); border-radius: var(--r-md); padding: var(--s2) var(--s2); }
.cz-notes-t { font-size: var(--fs-xs); color: var(--qg-gold-a); margin-bottom: var(--s1); }
.cz-note { font-size: var(--fs-xs); color: var(--text-muted); line-height: 1.6; }
</style>
