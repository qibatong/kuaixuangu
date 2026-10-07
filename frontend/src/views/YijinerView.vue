<template>
  <!--
    竞价一进二 —— 2026-09-28 新增
    ===========================================================================
    语义：昨日主板首板 → 今日竞价阶段评估**二连板潜力**，按综合评分降序。
    门禁：**严格 VIP/付费**（与「竞价异动」同强度，requiredLevel=1）—— 免费试用
          前端直接出 VipGate、后端也会 403（两道，前端那道只是省一次请求）。
    取数：全部走后端 /api/yijiner（后端复用 fetcher 涨停池 + 东财点查），
          **浏览器不直连东财**，因此门禁是真门禁、名单不会从前端泄露。
    评分：逐函数照搬主人提供的独立网页版（含其 f4(涨跌额)当昨收的缺陷，见
          backend/app/services/yijiner.py 头注释）。分数与网页版逐位一致。

    入口：**仅**首页左视图第 5 个 tab（StockView）。
    ⚠️ 刻意**未新增路由/导航项**：router 的 meta.group/meta.order 是导航栏渲染的数据源
    （2026-09-27 刚重构为 5 分组），新增一条路由会自动多出一个一级导航项 —— 属
    「扩大改动范围」，待主人明确要求再做。
  -->
  <div class="page-shell" :class="{ 'yj-embedded': embedded }">
    <h1 class="visually-hidden">竞价一进二</h1>

    <!-- 严格门禁：未登录/免费试用直接看到引导卡，不发请求 -->
    <VipGate v-if="!user.isVipOrPaid || needVip" title="竞价一进二" :required-level="1" />

    <template v-else>
      <div class="yj-panel">
        <div class="yj-head">
          <div class="yj-title">
            <i class="fa fa-level-up yj-icon"></i>
            竞价一进二
            <!-- 2026-09-29 主人: 回看日期不单独占一行 —— **点这里**选日期(今日/历史同一入口) -->
            <!-- 🔴 隐藏的日期控件必须**贴着 chip** 定位: 若作为 .yj-head(flex space-between)
                 的直接子元素, 绝对定位会按 flex 对齐规则被推到**最右端** ⇒ 日历弹在右边。 -->
            <span class="yj-picker">
              <button class="yj-chip yj-chip-date" :class="{ 'yj-chip-history': !!pickDate }"
                      :title="pickDate ? '正在回看历史：点击可换日期' : '点击选择回看日期'"
                      @click="openPicker">
                {{ pickDate ? '回看' : '今日' }} {{ pickDate || meta.dataDate || '' }}
                <i class="fa" :class="pickDate ? 'fa-history' : 'fa-caret-down'"></i>
              </button>
              <input ref="dateEl" v-model="pickDate" class="yj-date-hidden" type="date" :max="maxDate"
                     @change="load">
            </span>
            <span v-if="pickDate" class="yj-chip yj-chip-back" title="回到今天" @click="backToday">回今天</span>
            <span v-if="meta.approx" class="yj-chip yj-chip-approx"
                  title="历史回看：f26 上市日期 / f100 行业不可重建 ⇒ 次新过滤不生效、板块排名退化为单组">近似口径</span>
            <span v-if="list.length" class="yj-chip yj-chip-accent">{{ list.length }} 只</span>
          </div>
          <button class="yj-refresh" :disabled="loading" @click="load">
            <i class="fa" :class="loading ? 'fa-spinner fa-spin' : 'fa-refresh'"></i> 刷新
          </button>
        </div>

        <!--
          🔴 2026-09-28 主人拍板：去掉标题下的「口径说明」3 行（主板首板口径 / 评分公式 / 红线机制）
             与全部 tooltip（首板日、名次奖杯、"点击查看分时/日K/周K/月K"、概念列）。
             理由：这些提示词占版面且信息重复 —— 评分维度与红线在结果列里本就能看出。
             ⚠️ 刻意保留：状态类文案（加载中 / 失败重试 / 空态）+ 免责声明（合规），
             并保留首板日 chip 与「N 只」chip（它们是数据，不是说明）。
        -->
        <div v-if="loading && !list.length" class="loading-placeholder">
          <div class="spinner"></div>
          <div>正在扫描昨日首板、拉取今日行情…</div>
        </div>

        <div v-else-if="errMsg" class="yj-empty">
          <i class="fa fa-exclamation-circle"></i> {{ errMsg }}
          <button class="yj-retry" @click="load">重试</button>
        </div>

        <div v-else-if="!list.length" class="yj-empty">
          <i class="fa fa-filter"></i> 当前没有符合条件的一进二候选
          <div class="yj-empty-hint">{{ statsHint }}</div>
        </div>

        <template v-else>
          <!-- 2026-09-29 主人反馈"手机页面看不全": 手机不显示滚动条, 列多时在 390px 必然要横滑,
               用户根本看不出"右边还有列"。加一行可滑提示(仅窄屏显示, 桌面不占位)。
               🔴 列数随列增删改(去掉「行业」后为 11 列), 文案要与表头一致。 -->
          <div class="yj-swipe-hint"><i class="fa fa-arrows-h"></i> 左右滑动查看全部 11 列</div>
          <div class="yj-scroll">
            <table class="stock-table yj-table">
              <thead>
                <!-- 2026-09-29 主人: 表头可排序 —— 复用全站 useSortable(点击 无→降序→升序→无);
                     排名列是"当前顺序序号", 本身不可排 -->
                <tr>
                  <th class="yj-th-rank">排名</th>
                  <th class="sortable" :class="{ active: sort.keyOf('code') }"
                      @click="sort.onSort('code', 'string')">名称<span class="sort-ind">{{ sort.ind('code') }}</span></th>
                  <th class="sortable" :class="{ active: sort.keyOf('probability') }"
                      @click="sort.onSort('probability')">综合评分<span class="sort-ind">{{ sort.ind('probability') }}</span></th>
                  <th class="sortable" :class="{ active: sort.keyOf('confidence') }"
                      @click="sort.onSort('confidence')">可信<span class="sort-ind">{{ sort.ind('confidence') }}</span></th>
                  <th class="sortable" :class="{ active: sort.keyOf('bidChange') }"
                      @click="sort.onSort('bidChange')">竞价涨幅<span class="sort-ind">{{ sort.ind('bidChange') }}</span></th>
                  <th class="sortable" :class="{ active: sort.keyOf('realChange') }"
                      @click="sort.onSort('realChange')">实时涨幅<span class="sort-ind">{{ sort.ind('realChange') }}</span></th>
                  <th class="sortable" :class="{ active: sort.keyOf('entityChange') }"
                      @click="sort.onSort('entityChange')">实体涨幅<span class="sort-ind">{{ sort.ind('entityChange') }}</span></th>
                  <th class="sortable" :class="{ active: sort.keyOf('circulationMV') }"
                      @click="sort.onSort('circulationMV')">流通市值<span class="sort-ind">{{ sort.ind('circulationMV') }}</span></th>
                  <th class="sortable" :class="{ active: sort.keyOf('firstSealTime') }"
                      @click="sort.onSort('firstSealTime')">昨封板<span class="sort-ind">{{ sort.ind('firstSealTime') }}</span></th>
                  <th class="sortable" :class="{ active: sort.keyOf('breakCount') }"
                      @click="sort.onSort('breakCount')">昨炸板<span class="sort-ind">{{ sort.ind('breakCount') }}</span></th>
                  <!-- ★ 2026-09-29 主人要求: 「行业」列下线(后端 industry 照旧下发, 只是不展示) -->
                  <th class="sortable" :class="{ active: sort.keyOf('concept') }"
                      @click="sort.onSort('concept', 'string')">概念<span class="sort-ind">{{ sort.ind('concept') }}</span></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(r, i) in shown" :key="r.code">
                  <td class="yj-rank">
                    <i v-if="i < 3" class="fa fa-trophy" :class="'yj-trophy-' + (i + 1)"></i>
                    <span v-else>{{ i + 1 }}</span>
                  </td>
                  <td
                    class="name-col yj-name"
                    :data-stock-code="r.code"
                    :data-stock-name="r.name"
                  >
                    <!-- 2026-09-29 主人拍板：去掉本页「＋自选」按钮(鸡肋)。
                         注：本行原本**没有套 .pool-hover-wrap**，而该按钮的显隐规则是
                         main.css 的 `.pool-hover-wrap .pool-hover-btn{visibility:hidden}` +
                         `:hover{visible}` ⇒ 没有这层包裹 ⇒ 每行都**常驻**一个「＋自选」
                         （手机上更没有 hover 概念，等于永久挂着）。要加自选请走首页/自选页。 -->
                    <div class="yj-name-main">{{ r.name }}</div>
                    <div class="yj-name-sub">{{ r.code }}</div>
                  </td>
                  <td><span class="yj-score" :class="{ 'yj-score-low': r.redFlag }">{{ r.probability }}分</span></td>
                  <!-- 👇 2026-09-29: data-label 只在 ≤430px 的**卡片模式**用(纯 CSS ::before 生成字段名),
                       桌面/平板完全不用它; 标签用短词(市值/竞价/实时/实体)以适应卡片一行多格。 -->
                  <td class="yj-dim-cell" data-label="可信">{{ r.confidence }}%</td>
                  <td :class="chgCls(r.bidChange)" data-label="竞价">{{ fmtPct(r.bidChange) }}</td>
                  <td :class="chgCls(r.realChange)" data-label="实时">{{ fmtPct(r.realChange) }}</td>
                  <td :class="chgCls(r.entityChange)" data-label="实体">{{ fmtPct(r.entityChange) }}</td>
                  <td data-label="市值">{{ fmtMv(r.circulationMV) }}</td>
                  <td class="yj-time" data-label="昨封板">{{ fmtFbt(r.firstSealTime) }}</td>
                  <td :class="{ 'yj-warn': r.breakCount > 0 }" data-label="昨炸板">{{ r.breakCount }}次</td>
                  <!-- 只显示主要的 2 个概念(按本名单共鸣频次挑), 全串仍在接口返回里 —— 见 utils/conceptMain.js -->
                  <td class="yj-concept" data-label="概念">{{ pickMainConcept(r.concept, conceptFreq) }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- 🔴 2026-09-30 主人「清理提示」: 原此处为「底部口径与统计」条 ——
               上一行"昨日涨停池 N 只 → 主板首板 … → 候选 … → 入选 … · 剔除：… · 耗时 …ms"(剔除口径),
               下一行"数据仅供研究参考，不构成任何投资建议"(免责声明)。两行均按主人指令删除,
               连同 .yj-note / .yj-disclaimer 样式, 以及**仅供前者使用**的 droppedText / DROP_LABELS。
               ⚠️ 合规说明: 免责声明在**全站页脚**另有常驻一份(App.vue 的 .disclaimer, 每页可见),
                 故此处删掉不损失合规覆盖。 -->
        </template>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import VipGate from '../components/VipGate.vue'
// 2026-09-29: 去掉「＋自选」按钮后不再需要 import PoolHoverBtn(该组件仍被首页/竞价异动等页使用)
import { fetchYijiner } from '../api/yijiner'
// 2026-09-29: 「主要概念」挑选抽到 utils（纯函数, 可单测; 将来金睛/火眼若要同样口径可直接复用）
import { buildConceptFreq, pickMainConcept } from '../utils/conceptMain'
import { useUserStore } from '../stores/user'
import { showToast } from '../utils/toast'
import { todayBj } from '../utils/time'      // 2026-09-29: 回看日期上限(不选未来)
import { useSortable } from '../composables/useSortable'   // 2026-09-29 主人: 表头可排序

// 2026-09-28: 嵌入首页左视图时传 embedded=true（与 AipickView 同约定，收紧间距）
defineProps({
  embedded: { type: Boolean, default: false },
})

const user = useUserStore()
const list = ref([])
// 2026-09-29 主人要求: 回看日期不单独列窗口 —— 点标题上的「今日/首板日」chip 即可选
const pickDate = ref('')              // 空 = 今天
const maxDate = todayBj()             // 不允许选未来
const dateEl = ref(null)
const sort = useSortable()    // 2026-09-29 主人: 表头排序(全站同一交互)

// 名称列按**代码**排序(与全站「名称」合并列同范式);
// 概念列按**表格里实际显示的那 2 个主概念**排序(否则按 f103 原始串排, 用户看不出规律)
const shown = computed(() => sort.sorted(list.value, (it, k) => {
  if (k === 'code') return it.code || ''
  if (k === 'concept') return pickMainConcept(it.concept, conceptFreq.value)
  return it[k]
}))

function openPicker() {
  const el = dateEl.value
  if (!el) return
  // Chrome 支持 showPicker(); 其余浏览器降级为 focus+click(原生控件仍会弹出)
  if (typeof el.showPicker === 'function') {
    try { el.showPicker(); return } catch (e) { /* 降级 */ }
  }
  el.focus(); el.click()
}
function backToday() {
  pickDate.value = ''
  load()
}
const meta = ref({})           // dataDate / stats / filters / elapsedMs
const loading = ref(false)
const errMsg = ref('')
const needVip = ref(false)     // 后端 403 兜底（如会员刚过期）

// 2026-09-30 主人「清理提示」: 原此处有 DROP_LABELS(剔除原因中文化, 与后端 _passes_filters
//   的 key 一一对应) + droppedText 计算属性 —— 两者**仅供已被删除的底部统计条使用**
//   ⇒ 一并移除, 避免留下死代码。
//   注: meta.stats.dropped 后端照旧返回, 只是前端不再展示。

const statsHint = computed(() => {
  const s = meta.value.stats
  if (!s) return '可稍后点「刷新」再试'
  return `昨日涨停池 ${s.poolTotal} 只 / 主板首板 ${s.firstBoard} 只，全部被过滤条件剔除`
})

function fmtPct(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '-'
  return (n > 0 ? '+' : '') + n.toFixed(2) + '%'
}
// A 股惯例：红涨绿跌（走全局 --accent 变量，自动适配深浅主题）
function chgCls(v) {
  const n = Number(v)
  if (!Number.isFinite(n) || n === 0) return ''
  return n > 0 ? 'yj-up' : 'yj-down'
}
function fmtMv(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(1) + '亿' : '-'
}
// 后端 firstSealTime 与东财 fbt 同口径：HHMMSS 整数（93700 → 09:37:00）
function fmtFbt(t) {
  const n = Number(t)
  if (!Number.isFinite(n) || n <= 0) return '-'
  const s = String(Math.trunc(n)).padStart(6, '0')
  return `${s.slice(0, 2)}:${s.slice(2, 4)}:${s.slice(4, 6)}`
}
/* 2026-09-29 主人：「一进二显示的概念有点多，只显示主要的就可以」
   · 概念列只显示**主要的 2 个**（对齐全仓既有口径 N=2：concept_refresh.TRUNCATE_N=2 /
     kpl.apply_board_concept_db(truncate=2) / 金睛火眼 conceptText=slice(0,2)）；
   · 「哪 2 个」按**本名单内的概念共鸣频次**排序（当日板块效应天然体现在同批票的概念重叠上），
     而不是东财 f103 的原始顺序 —— 后者无语义，直接取前 2 个可能挑到冷门概念；
   · 以「整条概念」为单位，不做字符串截断（不会把 MiniLED 切成 Mini）；
   · 概念全串仍在接口返回里，只是不再全量渲染。
   实现见 utils/conceptMain.js（纯函数，已单测）。 */
const conceptFreq = computed(() => buildConceptFreq(list.value))

async function load() {
  if (!user.isVipOrPaid) return        // 前端预判，省一次必然 403 的请求
  loading.value = true
  errMsg.value = ''
  try {
    const d = await fetchYijiner(pickDate.value || '')
    if (d && d.ok) {
      list.value = d.list || []
      meta.value = {
        dataDate: d.dataDate, stats: d.stats, filters: d.filters, elapsedMs: d.elapsedMs,
        approx: !!d.approx    // 2026-09-29: 历史回看口径近似(f26/f100 不可重建)
      }
    } else {
      // 后端取数失败（涨停池/行情异常）→ 如实提示，不清空已有名单
      errMsg.value = (d && d.msg) || '取数失败'
      if (!list.value.length) meta.value = {}
    }
  } catch (e) {
    if (e && e.status === 403) { needVip.value = true; return }
    errMsg.value = (e && e.message) || '请求失败'
    showToast('❌ ' + errMsg.value, 'error')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  if (user.isVipOrPaid) load()
})

defineExpose({ load })
</script>

<style scoped>
.yj-panel { padding: 2px 0 var(--s2); }

/* ---- 头部 ---- */
.yj-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s2);
  flex-wrap: wrap;
  margin: 2px 0 var(--s1);
}
.yj-title {
  display: flex;
  align-items: center;
  gap: var(--s2);
  font-size: var(--fs-md);
  font-weight: 700;
  color: var(--text-main);
}
.yj-icon { color: var(--accent); }

/* 2026-09-29 主人: 回看日期并入标题 chip —— 可点击、无独立输入行 */
.yj-chip-date {
  cursor: pointer;
  background: none;
  font-family: inherit;
  line-height: 1.6;
}
.yj-chip-date:hover { border-color: var(--accent); color: var(--text-main); }
.yj-chip-history { color: var(--accent); border-color: var(--accent); }
.yj-chip-back { cursor: pointer; }
.yj-chip-back:hover { color: var(--accent); border-color: var(--accent); }
.yj-chip-approx { color: #e8b04b; border-color: rgba(232, 176, 75, .45); }
/* 隐藏的原生日期控件: 必须由 .yj-picker(贴着 chip)做定位上下文 ——
   若作为 .yj-head(flex space-between) 的直接子元素, 绝对定位会被推到最右端 ⇒ 日历弹在右边 */
.yj-picker { position: relative; display: inline-flex; }
.yj-date-hidden {
  position: absolute; left: 0; top: 100%;
  width: 1px; height: 1px; padding: 0; border: 0; opacity: 0;
  pointer-events: none;
}
.yj-chip {
  font-size: var(--fs-xs);
  font-weight: 500;
  padding: 1px var(--s2);
  border-radius: var(--r-lg);
  border: 1px solid var(--border-soft, #3a3f4b);
  color: var(--text-secondary);
}
.yj-chip-accent {
  border-color: rgba(var(--accent-rgb), 0.5);
  background: rgba(var(--accent-rgb), 0.12);
  color: var(--accent-text, var(--accent));
}
.yj-refresh {
  background: rgba(var(--accent-rgb), 0.12);
  border: 1px solid rgba(var(--accent-rgb), 0.45);
  color: var(--accent-text, var(--accent));
  border-radius: var(--r-md);
  padding: var(--s1) var(--s3);
  font-size: var(--fs-xs);
  cursor: pointer;
  white-space: nowrap;
}
.yj-refresh:hover:not(:disabled) { background: rgba(var(--accent-rgb), 0.22); }
.yj-refresh:disabled { opacity: 0.6; cursor: default; }

/* ---- 口径说明（.yj-desc / .yj-dim）已于 2026-09-28 随"去掉提示词"一并删除 ---- */

/* ---- 空态 / 错误态 ---- */
.yj-empty {
  text-align: center;
  padding: var(--s7) var(--s3);
  color: var(--text-secondary);
  font-size: var(--fs-sm);
}
.yj-empty .fa { color: var(--accent); margin-right: var(--s2); }
.yj-empty-hint { margin-top: var(--s2); font-size: var(--fs-xs); color: var(--text-muted); }
.yj-retry {
  margin-left: var(--s2);
  background: rgba(var(--accent-rgb), 0.15);
  border: 1px solid rgba(var(--accent-rgb), 0.45);
  color: var(--accent-text, var(--accent));
  border-radius: var(--r-md);
  padding: var(--s1) var(--s3);
  font-size: var(--fs-xs);
  cursor: pointer;
}

/* ---- 表格 ---- */
.yj-scroll { overflow-x: auto; }
.yj-table { min-width: 1020px; font-size: var(--fs-xs); }
.yj-table th { font-size: var(--fs-xs); white-space: nowrap; }
/*
  2026-09-28: 关闭本表的 sticky 表头（有意为之，勿"顺手恢复"）。
  全局 main.css:314-324 给 `.home-col-left .stock-table thead th` 设了
  position:sticky + top:var(--sticky-thead-top)，而该变量由首页 JS 按 `.home-filter`
  高度写入 —— 本 tab **没有 .home-filter** ⇒ 变量沿用别的 tab 的旧值（实测 56px）。
  再叠加本表需要横向滚动（容器 overflow:auto ⇒ 成为滚动容器），表头被顶到首行之下：
  实测 thead.top=391 vs 首行.top=367，视觉上遮住第一条。
  本名单通常仅数行，sticky 收益为零 ⇒ 关掉换取稳定布局。
  选择器多一层 .yj-panel 是必要的：与全局规则同为 (0,2,2)，靠源码顺序获胜太脆弱。
*/
.yj-panel .yj-table thead th { position: static; }
.yj-table td { white-space: nowrap; }
.yj-th-rank { width: 44px; }
.yj-rank { text-align: center; color: var(--text-muted); font-weight: 600; }
.yj-trophy-1 { color: #e6b400; }
.yj-trophy-2 { color: #9aa0a6; }
.yj-trophy-3 { color: #b5763a; }

/* 🔴 2026-09-28 主人拍板：名称/概念两列**内容改为居中**，与表头（全局 .stock-table 的
   text-align:center）一致 —— 原先只有单元格被改成左对齐，表头仍居中，实测「名称」表头
   在列中央而股票名在列左侧，错开约 80px（截图 deploy/yj-align-before.png）。
   改法选择"内容向表头看齐"（而非表头向内容看齐），保持全表 12 列对齐方式统一。 */
.yj-name { cursor: pointer; }
.yj-name-main { font-weight: 600; color: var(--text-main); }
.yj-name-sub { font-size: var(--fs-xs); color: var(--text-muted); }

.yj-score {
  font-weight: 700;
  color: var(--accent);
}
/* 触发红线的票：分数被压到 ≤45，用中性灰降低视觉权重（避免与"高分红"混淆） */
.yj-score-low { color: var(--text-muted); }

.yj-up { color: var(--accent); }
.yj-down { color: #00a854; }
.yj-warn { color: var(--accent); }
.yj-dim-cell { color: var(--text-secondary); }
.yj-time { color: var(--text-secondary); }
.yj-concept {
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--text-secondary);
  /* 同 .yj-name：内容向表头看齐（居中），不再单独左对齐 —— 见上方注释与 yj-align-before.png */
}

/* 2026-09-30 主人清理提示: .yj-note(底部口径与统计) / .yj-disclaimer 已连同其文案一并删除 */

/* 嵌入首页左视图（半宽）时收紧 */
.yj-embedded .yj-title { font-size: var(--fs-base); }
.yj-embedded .yj-concept { max-width: 110px; }

/* 浅色主题：白底上不用亮红当文字，改用深变体 */
body[data-bg="light"] .yj-down { color: #1f7a45; }
body[data-bg="light"] .yj-trophy-1 { color: #8a5500; }
body[data-bg="light"] .yj-chip { color: var(--watermark); }

/* ============================================================
   手机窄屏（2026-09-29 主人反馈："一进二手机页面看不全"）
   原状: 本文件**没有任何断点** —— .yj-table 被钉在 min-width:1020px(12 列全 nowrap)
        ⇒ 390px 屏只看得到前 3~4 列, 其余 8 列必须盲滑(手机不显示滚动条 ⇒ 体感=看不全)。
   改法(照 AuctionView.vue:1322-1332 的既有范式: 不藏列、不卡片化, 与全站一致):
     ① 收紧内边距/字号, 把 12 列的地板从 1020px 压到 880px(≤430px 再压到 820px);
        ⚠️ 单元格全是 nowrap ⇒ 内容真需要更宽时表格会自然变宽, **不会被压瘪**,
           这里的 min-width 只是"地板", 调小是安全操作;
     ② 横滑容器补触摸惯性(-webkit-overflow-scrolling);
     ③ 概念列在窄屏取消省略号、允许折行 —— 原来是"限宽省略 + JS 硬截 16 字",
        触屏没有 hover, 概念在手机上永远读不全(信息在数据层就丢了, 横滑也救不回来);
     ④ 顶部那行 .yj-swipe-hint 让"可以横滑"这件事可见。
   ============================================================ */
.yj-swipe-hint { display: none; }

@media (max-width: 768px) {
  .yj-swipe-hint {
    display: block;
    margin: 0 0 var(--s1);
    font-size: var(--fs-xs);
    color: var(--text-muted);
    text-align: right;
  }
  .yj-table { min-width: 880px; font-size: var(--fs-xs); }
  .yj-table th,
  .yj-table td { padding: var(--s2) var(--s1); }
  .yj-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
  /* 2026-09-29: 概念用 overflow-wrap 而非 word-break:break-all —— 后者会把 MiniLED/CPO/PCB
     这类英文概念从中间断开(交易名词断字很容易看错), 前者只在整词放不下时才断, 且优先在「、」后断。 */
  .yj-concept { max-width: 118px; white-space: normal; line-height: 1.3; overflow-wrap: anywhere; }
  .yj-embedded .yj-concept { max-width: 100px; }
}

@media (max-width: 430px) {
  /* ============================================================
     🔴 2026-09-29 主人拍板：一进二**先试卡片化**（本页单独试，金睛/火眼暂不动）
     背景: 12 列在 390px 屏上无论怎么收紧地板都要横滑 2 屏多，而手机不显示滚动条
          ⇒ 用户常年"看不全"（当天日志/反馈都指向这一点）。
     做法: ≤430px 把 `tr` 变卡片、`td` 变卡片内的字段，字段名由每个 `<td data-label>`
          用 `::before` 生成 —— **纯 CSS，不动接口、不动数据结构**；
          桌面/平板/横屏完全不受影响（断点外仍是表格）。
     回滚: 注释/删掉本段 @media 即恢复原状（其余 ≤768px 规则保持不变）。
     代价: 一屏约 8~9 张卡（表格约 14~18 行）—— 用"行数减半"换"字段全在一屏、零横滑"。
     ============================================================ */
  .yj-swipe-hint { display: none; }        /* 卡片不需要横滑, 提示反而误导 */
  .yj-scroll { overflow-x: visible; }
  .yj-table { display: block; width: 100%; min-width: 0; font-size: var(--fs-xs); }
  .yj-table thead { display: none; }       /* 字段名改由 data-label 在卡内展示 */
  .yj-table tbody { display: block; }
  .yj-table tbody tr {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: var(--s1) var(--s2);
    padding: var(--s2) var(--s2) var(--s2);
    margin: 0 0 var(--s2);
    border: 1px solid var(--border-soft, rgba(255, 255, 255, 0.1));
    border-radius: var(--r-md);
    background: var(--bg-panel, rgba(18, 22, 35, 0.85));
  }
  .yj-table tbody td {
    display: inline-flex;
    align-items: baseline;
    gap: var(--s1);
    width: auto;
    padding: 0;
    border: 0;
    font-size: var(--fs-xs);
    white-space: nowrap;
  }
  /* 第 1 行：名次(🏆/序号) + 名称(撑满, 逼后面字段换行) + 综合评分(靠右, 与名称同一行) */
  .yj-table tbody td:nth-child(1) { flex: 0 0 auto; }
  .yj-table tbody td:nth-child(2) { flex: 1 1 auto; min-width: 0; text-align: left; }
  .yj-table tbody td:nth-child(2) .yj-name-main { display: block; font-size: var(--fs-sm); }
  .yj-table tbody td:nth-child(2) .yj-name-sub { display: block; }
  .yj-table tbody td:nth-child(3) { flex: 0 0 auto; margin-left: auto; }
  /* 其余字段：灰标签 + 值, 自动换行铺满卡片（一行能放几个就放几个） */
  .yj-table tbody td:nth-child(n + 4)::before {
    content: attr(data-label);
    color: var(--text-muted);
    font-size: var(--fs-xs);
    font-weight: 400;
  }
  /* 概念：独占一行且可折行（表格态被 118px 限宽 + 省略号截断, 卡片态要能看全）
     🔴 2026-09-29: 原用 nth-child(12) —— 去掉「行业」列后概念变第 11 列, 序号会失配;
       改为按类名选择(.yj-concept), 列增删不再需要改这里。 */
  .yj-table tbody td.yj-concept {
    flex: 1 0 100%;
    max-width: none;
    min-width: 0;
    margin-top: 2px;
    white-space: normal;
    line-height: 1.35;
    overflow-wrap: anywhere;   /* 同上: 保住 MiniLED/CPO 等英文概念不被拦腰断开 */
  }
  /* 与 2026-09-28 那段注释同因(本 tab 没有 .home-filter ⇒ sticky 变量沿用旧值会压首行);
     卡片态 thead 已隐藏, 这里保留是为防以后有人在卡片上方又补表头时踩同一个坑。 */
  .yj-panel .yj-table thead th { position: static; }
}
</style>
