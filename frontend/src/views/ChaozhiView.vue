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
    <!-- 整页模式：主站**原生渲染**超智首页看板（2026-10-06 起取代原先 iframe 套独立 Flask 页）。
         数据全部来自主站已有接口（无写死数据），盘中 30s 轮询；逐块独立降级。
         取代关系：老实现是 <iframe src="/chaozhi/?embed=1">，指向旁路 Flask 服务(8020)，数据另取一份。 -->
    <ChaozhiHome v-if="!embedded" />
    <template v-else>
    <div v-if="loading" class="loading-placeholder"><div class="spinner"></div><div>加载研判数据...</div></div>
    <div v-else-if="failed" class="empty-state">研判数据暂不可用，稍后重试</div>

    <template v-else>
      <!-- ① Hero：今日结论（2026-10-06 三层重构：结论 → 证据 → 标的）
           金融口径：把分散的分数合成「一句裁决 + 一句依据」，不让用户自己拼。
           🔴 裁决只吃 0~100 的三项（情绪/资金/晋级率）；承接强弱 support 是**比值**
              （实测 0.09 量级，尺度不同）⇒ 不参与加权，只在下方单独列。
           🔴 晋级率**没有历史序列**（当日单值）⇒ 不给环比 Δ；有 10 日序列的两项才有 Δ。 -->
      <section class="cz-hero" :class="'v-' + verdict.key">
        <div class="cz-hero-top">
          <span class="cz-verdict"><i aria-hidden="true"></i>{{ verdict.text }}</span>
          <span class="cz-kpi">
            <b>{{ fmt(scores.promote) }}<em>%</em></b>
            <span>综合晋级率</span>
          </span>
        </div>
        <div class="cz-bars">
          <div v-for="b in heroBars" :key="b.key" class="cz-bar">
            <span class="cz-bar-l">{{ b.label }}</span>
            <i class="cz-bar-track"><b :class="b.cls" :style="{ width: barW(b.v) }"></b></i>
            <span class="cz-bar-v">{{ fmt(b.v) }}</span>
            <span v-if="b.delta !== null" class="cz-delta" :class="b.delta >= 0 ? 'up' : 'down'"
            >{{ b.delta >= 0 ? '▲' : '▼' }}{{ Math.abs(b.delta) }}</span>
          </div>
          <div class="cz-bar cz-bar-sup">
            <span class="cz-bar-l">承接强弱</span>
            <i class="cz-bar-track"><b class="mid" :style="{ width: supportW }"></b></i>
            <span class="cz-bar-v">{{ fmt(scores.support) }}</span>
          </div>
        </div>
        <p v-if="whyText" class="cz-why">{{ whyText }}</p>
      </section>

      <!-- ①b 战绩回看（与结论一体：告诉你这个判词过去准不准 / 有没有数据支撑）
           🔴 后端只给**有真值**的交易日；一天都没有 ⇒ 整条不渲染（不许显示 0%） -->
      <section v-if="hitSummary.total" class="cz-hits">
        <span class="cz-hits-t">近 {{ hitSummary.days }} 日 Top10 实封</span>
        <b class="cz-hits-r">{{ hitSummary.rate }}%</b>
        <span class="cz-hits-n">{{ hitSummary.hit }}/{{ hitSummary.total }} 只</span>
        <span class="cz-spark" aria-hidden="true">
          <i
            v-for="d in hitSeries" :key="d.date" :class="{ hi: d.rate >= 50 }"
            :style="{ height: sparkH(d.rate) }" :title="d.date + ' 封板 ' + d.rate + '%'"
          ></i>
        </span>
      </section>

      <!-- ②③ 证据层（**仅整页**；嵌入左栏时主人定「只留结论 + 标的」）
           2026-10-06: 两张图都补了 10 日均值虚线 + 今日环比 —— 单看十根柱子读不出"今天是高还是低" -->
      <template v-if="!embedded">
      <section class="cz-card">
        <div class="cz-card-h">
          <span class="cz-card-t">情绪预判</span>
          <span v-if="emoDelta !== null" class="cz-delta lg" :class="emoDelta >= 0 ? 'up' : 'down'"
          >今日 {{ emoDelta >= 0 ? '▲' : '▼' }}{{ Math.abs(emoDelta) }}</span>
          <span class="cz-tabs">
            <button
              v-for="p in phaseTabs" :key="p" class="cz-tab"
              :class="{ on: phase === p }" @click="phase = phase === p ? '' : p"
            >{{ p }}</button>
          </span>
        </div>
        <div v-if="emotion.series && emotion.series.length" class="cz-chart">
          <!-- 均值虚线（横跨整图，"今天算高还是算低"全靠它） -->
          <span v-if="emoAvg !== null" class="cz-mean" :style="{ bottom: (emoAvg / (maxZt || 1)) * 100 + '%' }"></span>
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

      <section class="cz-card">
        <div class="cz-card-h">
          <span class="cz-card-t">资金预判</span>
          <span v-if="capDelta !== null" class="cz-delta lg" :class="capDelta >= 0 ? 'up' : 'down'"
          >今日 {{ capDelta >= 0 ? '▲' : '▼' }}{{ capDeltaFmt }}</span>
          <span class="cz-tabs">
            <button
              v-for="m in capMetrics" :key="m.key" class="cz-tab"
              :class="{ on: metric === m.key }" @click="metric = m.key"
            >{{ m.label }}</button>
          </span>
        </div>
        <div v-if="capital.series && capital.series.length" class="cz-chart">
          <!-- 🔴 2026-10-06 修: 此前用 Math.abs ⇒ **净流出和净流入画得一样**, 方向信息全丢。
               现改为: 零轴按正负区间定位置, 正向上(红=流入) 负向下(绿=流出)。 -->
          <span class="cz-zero" :style="{ bottom: capZeroPct + '%' }"></span>
          <div v-for="d in capital.series" :key="d.date" class="cz-col">
            <span class="cz-col-v">{{ capVal(d) }}</span>
            <span class="cz-col-slot">
              <i
                v-if="d[metric] !== null && d[metric] !== undefined"
                class="cz-col-bar"
                :class="Number(d[metric]) >= 0 ? 'b-up' : 'b-down'"
                :style="capSegStyle(d[metric])"
              ></i>
            </span>
            <span class="cz-col-x">{{ d.date.slice(5) }}</span>
          </div>
        </div>
        <div v-else class="cz-empty">资金序列暂无数据（需 `snapshot_bid` 9:25 行落库）</div>
        <!-- 2026-10-03 主人指令：说明文字不在前端展示（原「竞价额/承接强弱」图例已移除） -->
      </section>
      </template>

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
        <!-- 2026-10-06: 恢复四个**决策字段**（后端已经算好，10-03 精简时被砍）：
             成交概率(fillGrade) / 双模型共识(consensus) / 风险(ST·high) / 竞价额(bidAmount)。
             它们不是噪声 —— 看完名单还要去别页验证"能不能买到"的话，这一页的决策闭环就没合上。
             默认只放 Top10，其余折叠（手机端从 8 屏压到 2 屏）；嵌入左栏 Top5。 -->
        <div v-if="picks.length" class="cz-list" :class="{ 'cz-list-scroll': embedded }">
          <div v-for="(p, i) in picksShown" :key="p.code" class="cz-row" :data-cz="p.code">
            <span class="cz-line1">
              <span class="cz-rank">{{ i + 1 }}</span>
              <span class="cz-name">{{ p.name }}</span>
              <span class="cz-code">{{ p.code }}</span>
              <span v-if="p.st" class="cz-pk cz-pk-st">ST</span>
              <span v-else-if="p.risk === 'high'" class="cz-pk cz-pk-risk">高风险</span>
              <span v-if="p.consensus" class="cz-pk cz-pk-cons">双✓</span>
              <span v-if="p.fillGrade" class="cz-fill" :class="fillCls(p.fillGrade)">{{ fillText(p.fillGrade) }}</span>
            </span>
            <span class="cz-line2">
              <!-- 分色阶条：横向长度即分数，扫视成本远低于读两位数 -->
              <i class="cz-fbar"><b :class="'fb-' + fusedCls(p.scoreFused)" :style="{ width: (p.scoreFused || 0) + '%' }"></b></i>
              <span class="cz-fused">综合<b>{{ p.scoreFused ?? '—' }}</b></span>
              <span class="cz-chg" :class="(p.change || 0) >= 0 ? 'up' : 'down'"><i class="cz-chg-kind">{{ chgKindText(p) }}</i>{{ signed(p.change) }}%</span>
              <span v-if="p.bidAmount" class="cz-amt">{{ amtText(p.bidAmount) }}</span>
              <span v-if="p.concept" class="cz-concept">{{ p.concept }}</span>
              <span v-if="p.isLimitUp === 1" class="cz-pk cz-pk-zt">已封板</span>
              <span v-else-if="p.isLimitUp === 0" class="cz-pk cz-pk-no">未封板</span>
            </span>
          </div>
          <button v-if="picks.length > pickShow" class="cz-more-row" @click="picksOpen = !picksOpen"
          >{{ picksOpen ? '收起' : '展开全部 ' + picks.length + ' 只' }}</button>
        </div>
        <div v-else class="cz-empty">暂无个股研判数据</div>
        <!-- 2026-10-03 主人指令：说明文字不在前端展示（原「综合分/名单口径」图例已移除，
             且其内容已过期：竞价额≥3000万 / 涨幅≤10% 均已改） -->
      </section>

      <!-- ⑤⑥ 影子 / 底部按钮 / 降级说明 —— **仅整页**（2026-10-06: 嵌入左栏只留 Hero + Top5） -->
      <template v-if="!embedded">
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
        <!-- 2026-10-06: 文案改「完整评分榜单」—— 落点是 /aipick 全榜(45 只+筛选)，
             叫"详情"容易让人以为是个股详情；且 /aipick 已归入超智组，跳转后高亮不切走 -->
        <button class="cz-btn ghost" @click="goDetail">查看完整评分榜单</button>
        <button class="cz-btn main" :disabled="!picks.length" @click="addPool">加入自选</button>
      </div>

      <!-- 降级说明（如实展示） -->
      <div v-if="notes.length" class="cz-notes">
        <div class="cz-notes-t"><i class="fa fa-info-circle"></i> 数据说明</div>
        <div v-for="(n, i) in notes" :key="i" class="cz-note">· {{ n }}</div>
        <div v-if="meta.pickDate" class="cz-note">· 模型数据日期：{{ meta.pickDate }}</div>
      </div>
      </template>
    </template>
    </template>
    </div><!-- /.cz-body  ← 2026-10-03: 嵌入模式折叠容器 -->
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { chaozhiOverview } from '../api/kpl'
import ChaozhiHome from './ChaozhiHome.vue'
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
// 2026-10-06 三层重构新增：战绩回看 + 标的折叠
const hitSeries = ref([])
const hitSummary = ref({ days: 0, total: 0, hit: 0, rate: null })
const picksOpen = ref(false)

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

// ==================== Hero 结论（2026-10-06） ====================
/** 三项 0~100 指标的均分 ⇒ 一句裁决。🔴 承接强弱是**比值**（实测 0.09 量级）⇒ 不参与加权。
 *  阈值65/40 是拍出来的起点，改这里一处即可调全站判词。 */
const verdict = computed(() => {
  const vals = [scores.value.emotion, scores.value.capital, scores.value.promote]
    .map(Number).filter((v) => Number.isFinite(v))
  if (!vals.length) return { key: 'na', text: '数据不足' }
  const avg = vals.reduce((a, b) => a + b, 0) / vals.length
  if (avg >= 65) return { key: 'atk', text: '进攻' }
  if (avg >= 40) return { key: 'bal', text: '均衡' }
  return { key: 'def', text: '防守' }
})
/** 环比只给**有 10 日序列**的两项；晋级率是当日单值 ⇒ 恒为 null（不编数字） */
const emoDelta = computed(() => {
  const s = emotion.value.series || []
  if (s.length < 2) return null
  const a = s[s.length - 1].zt; const b = s[s.length - 2].zt
  return (a === null || a === undefined || b === null || b === undefined) ? null : a - b
})
const capDelta = computed(() => {
  const s = capital.value.series || []
  if (s.length < 2) return null
  const a = s[s.length - 1][metric.value]; const b = s[s.length - 2][metric.value]
  return (a === null || a === undefined || b === null || b === undefined) ? null : a - b
})
const capDeltaFmt = computed(() => {
  const d = capDelta.value
  if (d === null) return ''
  return metric.value === 'volRatio' ? Math.abs(d).toFixed(2) : (Math.abs(d) / 1e8).toFixed(1) + '亿'
})
const emoAvg = computed(() => {
  const s = (emotion.value.series || []).map((d) => d.zt).filter((v) => Number.isFinite(v))
  return s.length ? Math.round(s.reduce((a, b) => a + b, 0) / s.length) : null
})
const barTier = (v) => (v === null || v === undefined) ? 'flat' : (v >= 65 ? 'hi' : (v >= 40 ? 'mid' : 'lo'))
const heroBars = computed(() => [
  { key: 'emotion', label: '情绪温度', v: scores.value.emotion, cls: barTier(scores.value.emotion), delta: emoDelta.value },
  { key: 'capital', label: '资金强度', v: scores.value.capital, cls: barTier(scores.value.capital), delta: capDelta.value },
  { key: 'promote', label: '晋级率', v: scores.value.promote, cls: barTier(scores.value.promote), delta: null },
])
/** 依据句：全部来自已有字段，缺哪段就不显示哪段 */
const whyText = computed(() => {
  const o = []
  if (senti.value.ztCount != null) o.push('涨停 ' + senti.value.ztCount + ' 家')
  if (senti.value.lbgd != null) o.push('最高 ' + senti.value.lbgd + ' 板')
  if (hitSummary.value.rate != null) o.push('Top10 实封 ' + hitSummary.value.rate + '%')
  const lv = capital.value.latest || {}
  if (lv[metric.value] != null && lv[metric.value] !== undefined) o.push(capMetricLabel.value + ' ' + capVal(lv))
  return o.join(' · ')
})
function barW(v) { return (Number.isFinite(Number(v)) ? Math.max(2, Math.min(100, Number(v))) : 0) + '%' }
/** 承接强弱是**比值**不是百分位 ⇒ 只做相对长度（按 0.3 封顶，非此极值时不误导） */
const supportW = computed(() => {
  const v = Number(scores.value.support)
  return Number.isFinite(v) ? Math.max(4, Math.min(100, (Math.abs(v) / 0.3) * 100)) + '%' : '4%'
})
function sparkH(rate) { return Math.max(10, Math.min(100, Number(rate) || 0)) + '%' }
/** 成交概率分级（后端 fill_grade，语义见 aipick/scripts/pick_daily.py 表头注释）：
 *   none 一字封死 / low ≥9.8% 排队 / mid 5~9.8% / high ≤5% ⇒ **值越大越好买到** */
const FILL = { none: { t: '买不进', c: 'f-none' }, low: { t: '难成交', c: 'f-low' },
               mid: { t: '可成交', c: 'f-mid' }, high: { t: '易成交', c: 'f-high' } }
function fillText(g) { return (FILL[g] || {}).t || '' }
function fillCls(g) { return (FILL[g] || {}).c || '' }
/** 个股竞价额：`predictions_*.json` 的 bid_amount 单位是**万元**（实测 5303.1 = 5303 万），
 *  ⚠️ 与 `capital.series` 的全市场竞价额**不同源**（那份单位是**元**，走 capVal 折算成亿）。 */
function amtText(v) {
  const n = Number(v)
  if (!Number.isFinite(n) || n === 0) return ''
  return Math.abs(n) >= 10000 ? (n / 10000).toFixed(2) + '亿' : Math.round(n) + '万'
}
function fusedCls(v) { return v >= 80 ? 'hot' : (v >= 60 ? 'warm' : 'cool') }
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
const capMetricLabel = computed(() => (capMetrics.find((m) => m.key === metric.value) || {}).label || '')
/** 标的区：整页默认 Top10（可展开全部），嵌入左栏 Top5（主人定「不抢主视野」） */
const pickShow = computed(() => (props.embedded ? 5 : 10))
const picksShown = computed(() => (picksOpen.value ? picks.value : picks.value.slice(0, pickShow.value)))

/** 资金图的正负区间（净额/主力净额有负值是常态，不能 abs 掉） */
const capRange = computed(() => {
  const vals = (capital.value.series || []).map((d) => d[metric.value])
    .filter((v) => v !== null && v !== undefined && Number.isFinite(Number(v))).map(Number)
  const pos = Math.max(0, ...vals.filter((v) => v > 0), 0)
  const neg = Math.max(0, ...vals.filter((v) => v < 0).map(Math.abs), 0)
  return { pos, neg, span: (pos + neg) || 1 }
})
/** 零轴在容器里的位置（自上而下 %）：没有负值 ⇒ 贴底（与普通柱图一致） */
const capZeroPct = computed(() => (capRange.value.pos / capRange.value.span) * 100)
/** 单根柱的定位与高度：正值从零轴向上生长，负值向下 */
function capSegStyle(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return null
  const { pos, neg, span } = capRange.value
  const zero = (pos / span) * 100
  if (n >= 0) return { bottom: zero + '%', height: (pos ? (n / span) * 100 : 0) + '%' }
  return { top: zero + '%', height: (neg ? (Math.abs(n) / span) * 100 : 0) + '%' }
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
    // 战绩回看（可能为空数组 ⇒ Hero 下的战绩条自动不渲染）
    hitSeries.value = d.hitSeries || []
    hitSummary.value = d.hitSummary || { days: 0, total: 0, hit: 0, rate: null }
    failed.value = false
  } catch (e) { failed.value = picks.value.length === 0 } finally { loading.value = false }
}

onMounted(load)
// 60s、仅盘中（与后端 OVERVIEW_TTL=60 同频；开盘啦/AI 都是付费或配额敏感资源）
usePolling(() => { if (isIntradayNow()) load() }, 60000, { immediate: false })
</script>

<style scoped>
.cz-root { max-width: 980px; margin: 0 auto; padding-bottom: calc(70px + env(safe-area-inset-bottom)); }
/* 整页嵌入超智首页看板：铺满宽度，高度自适应内容 */
.cz-frame { width: 100%; height: calc(100vh - 120px); min-height: 600px; border: none; border-radius: var(--r-lg); background: var(--bg-body); }
@media (max-width: 640px) {
  .cz-frame { height: calc(100vh - 150px); min-height: 480px; border-radius: var(--r-md); }
}

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

/* ============ Hero 结论区（2026-10-06 三层重构） ============
   视觉纪律：整页**只有一个焦点** —— 左上判词 + 右侧主 KPI(晋级率)，
   下面三条 WITH COLOR bar 是副证据，再下一行是事实依据。层级依次减弱。 */
.cz-hero {
  background: linear-gradient(160deg, var(--bg-card), var(--bg-panel));
  border: 1px solid var(--border-soft); border-left-width: 3px;
  border-radius: var(--r-lg); padding: var(--s2) var(--s3);
}
/* 判词用**左侧竖条颜色**表态，不靠用户读说明文字 */
.cz-hero.v-atk { border-left-color: var(--qg-red-a); }
.cz-hero.v-bal { border-left-color: var(--qg-gold-a); }
.cz-hero.v-def { border-left-color: var(--qg-blue-a); }
.cz-hero.v-na  { border-left-color: var(--border-soft); }
.cz-hero-top { display: flex; align-items: flex-end; gap: var(--s3); }
.cz-verdict {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: var(--fs-lg); font-weight: 700; color: var(--text-main); letter-spacing: 1px;
}
.cz-verdict i { width: 8px; height: 8px; border-radius: 50%; background: var(--border-soft); }
.v-atk .cz-verdict i { background: var(--qg-red-a); }
.v-bal .cz-verdict i { background: var(--qg-gold-a); }
.v-def .cz-verdict i { background: var(--qg-blue-a); }
.cz-kpi { margin-left: auto; text-align: right; }
.cz-kpi b { font-size: var(--fs-display); font-weight: 700; line-height: 1; color: var(--text-main); }
.cz-kpi b em { font-size: var(--fs-sm); font-style: normal; color: var(--text-muted); margin-left: 1px; }
.cz-kpi span { display: block; font-size: var(--fs-xs); color: var(--text-muted); margin-top: 2px; }

/* 副指标条 */
.cz-bars { margin-top: var(--s2); display: flex; flex-direction: column; gap: 5px; }
.cz-bar { display: flex; align-items: center; gap: var(--s2); font-size: var(--fs-xs); }
.cz-bar-l { flex: 0 0 56px; color: var(--text-muted); }
.cz-bar-track { flex: 1 1 auto; height: 6px; border-radius: 3px; background: var(--bg-input); overflow: hidden; }
.cz-bar-track b { display: block; height: 100%; border-radius: 3px; transition: width .35s ease; }
.cz-bar-track b.hi { background: linear-gradient(90deg, var(--qg-red-a), var(--qg-red-b)); }
.cz-bar-track b.mid { background: linear-gradient(90deg, var(--qg-gold-a), var(--qg-orange-a)); }
.cz-bar-track b.lo { background: linear-gradient(90deg, var(--qg-blue-a), var(--qg-blue-b)); }
.cz-bar-track b.flat { background: var(--border-soft); }
.cz-bar-v { flex: 0 0 30px; text-align: right; color: var(--text-secondary); font-weight: 600; }
.cz-bar-sup .cz-bar-track { opacity: .75; }   /* 承接强弱：尺度不同，弱化一档 */
.cz-delta { flex: 0 0 auto; font-size: var(--fs-xs); font-weight: 700; }
.cz-delta.up { color: var(--qg-red-a); }
.cz-delta.down { color: var(--qg-blue-a); }
.cz-delta.lg { font-size: var(--fs-sm); }
.cz-why { margin-top: var(--s2); font-size: var(--fs-xs); color: var(--text-muted); line-height: 1.5; }

/* 战绩条（近 n 日 Top10 实封）—— 与结论一体，告诉你这判词过去准不准 */
.cz-hits {
  display: flex; align-items: center; gap: var(--s2); margin-top: var(--s1);
  padding: var(--s1) var(--s3); background: var(--bg-input);
  border: 1px solid var(--border-soft); border-radius: var(--r-pill);
}
.cz-hits-t { font-size: var(--fs-xs); color: var(--text-muted); }
.cz-hits-r { font-size: var(--fs-md); font-weight: 700; color: var(--qg-gold-a); }
.cz-hits-n { font-size: var(--fs-xs); color: var(--text-dim); }
.cz-spark { margin-left: auto; display: flex; align-items: flex-end; gap: 2px; height: 16px; }
.cz-spark i { width: 5px; border-radius: 1px; background: var(--border-soft); }
.cz-spark i.hi { background: var(--qg-red-a); }

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
.cz-tab.on { background: var(--accent-solid); border-color: var(--accent); color: var(--qg-on); }

/* 柱状图（纯 CSS，10 个点，不引图表库） */
.cz-chart { position: relative; display: flex; align-items: flex-end; gap: var(--s1); height: 96px; margin: var(--s2) 2px var(--s1); }
/* 10 日均值虚线 / 资金图零轴（2026-10-06） */
.cz-mean {
  position: absolute; left: 0; right: 0; height: 0; border-top: 1px dashed var(--text-dim);
  opacity: .5; pointer-events: none; z-index: 1;
}
.cz-zero {
  position: absolute; left: 0; right: 0; height: 0; border-top: 1px solid var(--border-soft);
  pointer-events: none; z-index: 1;
}
/* 资金柱的容器：零轴在中间 ⇒ 柱子要能向上/向下生长 */
.cz-col-slot { position: relative; width: 100%; max-width: 26px; height: 100%; display: block; }
.cz-col-slot .cz-col-bar { position: absolute; border-radius: 2px; }
.b-up { background: linear-gradient(0deg, var(--qg-red-a), var(--qg-red-b)); }
.b-down { background: linear-gradient(180deg, var(--qg-blue-a), var(--qg-blue-b)); }
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
.cz-line1 { display: flex; align-items: center; gap: var(--s2); flex-wrap: wrap; }
.cz-rank {
  flex: 0 0 auto; min-width: 18px; height: 18px; border-radius: var(--r-sm);
  background: var(--bg-input); border: 1px solid var(--border-soft);
  color: var(--text-muted); font-size: var(--fs-xs); font-weight: 700;
  display: inline-flex; align-items: center; justify-content: center;
}
/* 综合分**色阶条**：长度即分数，比读两位数快一截 */
.cz-fbar { display: block; width: 100%; height: 3px; border-radius: 2px; background: var(--bg-input); overflow: hidden; margin-bottom: 3px; }
.cz-fbar b { display: block; height: 100%; border-radius: 2px; }
.cz-fbar b.fb-hot { background: linear-gradient(90deg, var(--qg-red-a), var(--qg-red-b)); }
.cz-fbar b.fb-warm { background: linear-gradient(90deg, var(--qg-gold-a), var(--qg-orange-a)); }
.cz-fbar b.fb-cool { background: linear-gradient(90deg, var(--qg-blue-a), var(--qg-blue-b)); }
/* 成交概率分级（语义: none 一字买不进 → high ≤5% 易成交） */
.cz-fill { flex: 0 0 auto; font-size: var(--fs-xs); border-radius: var(--r-sm); padding: 0 var(--s1); }
.f-none { color: var(--text-dim); border: 1px solid var(--border-soft); }
.f-low { color: var(--qg-orange-a); border: 1px solid var(--qg-orange-a); }
.f-mid { color: var(--qg-gold-a); border: 1px solid var(--qg-gold-a); }
.f-high { color: var(--qg-red-a); border: 1px solid var(--qg-red-a); }
.cz-pk-cons { background: rgba(255, 99, 132, 0.18); color: #ff6384; font-weight: 700; }
.cz-pk-st { background: rgba(255, 82, 82, 0.22); color: var(--accent); font-weight: 700; }
.cz-pk-risk { background: rgba(255, 167, 38, 0.16); color: #ffa726; }
.cz-amt { color: var(--text-dim); }
.cz-more-row {
  margin-top: var(--s2); padding: var(--s1) 0; width: 100%;
  background: none; border: 1px dashed var(--border-soft); border-radius: var(--r-md);
  color: var(--text-muted); font-size: var(--fs-xs); cursor: pointer;
}
.cz-more-row:hover { color: var(--text-main); border-color: var(--qg-orange-a); }
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
