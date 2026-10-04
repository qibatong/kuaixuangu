<template>
  <!--
    连板天梯（2026-10-01 主人拍板重设计；效果图 v4 确认版）
    结构：KPI 四色 → 晋级率 → 题材主线（精简，一行可滑）→ 阶梯层 → 断板反包 → 盘后天梯图（折叠）

    🔴 相对旧版的改动（主人逐条指定）：
      ① 标题「涨停梯队」→「连板天梯」；**去掉**副标题「实时涨停梯队 · 晋级率 · 题材主线（盘中持续刷新）」
         与右侧「时间 + 更新于」整块（连 DataStamp 一起移除）
      ② 手机整页 8588px → ≈1200px：低板(1~2板)由大卡改**紧凑 chips**（1板默认 12 只 + 展开），
         每层加**阶梯条**（长度 ∝ 家数、颜色随高度）⇒ 一眼看出金字塔形状
      ③ 高板(≥3板)每只一行：封单/主力/首封/换手 + 题材
      ④ 题材主线**精简**：只留「≥3家」或「有≥2板」，按 高度→家数 排序，最多 8 个，其余收进「+N 其他」
      ⑤ **文字不使用绿色**：题材标签用本页原有约定色（金色）、断板反包改紫色、主力净额去掉红绿
      ⑥ 点票弹**全字段详情**（接口本就下发 18 个字段，旧版只画 7~12 个）
    ⚠️ 保留不动：题材点击过滤、30s 盘中轮询、涨停原因弹层、盘后天梯图（日期回看 + 下载）。
  -->
  <div class="page-shell lb-root">
    <h1 class="visually-hidden">连板天梯</h1>

    <header class="lb-head">
      <span class="lb-logo" aria-hidden="true">
        <i class="lb-logo-b1"></i><i class="lb-logo-b2"></i><i class="lb-logo-b3"></i>
      </span>
      <div class="lb-h1">连板天梯</div>
    </header>

    <div v-if="loading" class="loading-placeholder"><div class="spinner"></div><div>加载连板天梯...</div></div>

    <template v-else>
      <div class="lb-kpi">
        <div class="lb-kpi-i k-red"><b>{{ stat.ztCount || 0 }}</b><span>涨停家数</span></div>
        <div class="lb-kpi-i k-orange"><b>{{ maxLadderText }}</b><span>最高板</span></div>
        <div class="lb-kpi-i k-gold lb-kpi-w"><b>{{ stat.spaceDragon || '-' }}</b><span>空间龙</span></div>
        <div class="lb-kpi-i k-purple lb-kpi-w"><b>{{ pct(promote.overall) }}</b><span>综合晋级率</span></div>
      </div>

      <div class="lb-promo">
        <span class="lb-promo-i p-red">1→2<b>{{ pct(promote.r1to2) }}</b></span>
        <span class="lb-promo-i p-orange">2→3<b>{{ pct(promote.r2to3) }}</b></span>
        <span class="lb-promo-i p-gold">3→4<b>{{ pct(promote.r3to4) }}</b></span>
        <span class="lb-promo-d">对比 {{ promote.date || '-' }}</span>
      </div>

      <!-- 题材主线：精简（≥3家 或 有≥2板），一行可滑，其余收进「+N 其他」 -->
      <div v-if="boards.length" class="lb-boards">
        <button class="lb-bd" :class="{ on: !activeBoard }" @click="activeBoard = ''">全部</button>
        <button
          v-for="b in shownBoards" :key="b.name" class="lb-bd"
          :class="{ on: activeBoard === b.name, main: b.main }"
          @click="toggleBoard(b.name)"
        >
          {{ b.name }}<em>{{ b.count }}家·{{ b.maxLadder }}板</em>
        </button>
        <button
          v-if="!boardExpand && hiddenBoards.length" class="lb-bd lb-bd-more"
          @click="boardExpand = true"
        >
          +{{ hiddenBoards.length }} 其他
        </button>
        <button v-else-if="boardExpand && hiddenBoards.length" class="lb-bd lb-bd-more" @click="boardExpand = false">收起</button>
      </div>

      <div v-if="!filteredLadders.length" class="empty-state">
        {{ activeBoard ? '该题材暂无连板个股' : '暂无连板梯队数据（非交易时段可能为空）' }}
      </div>

      <div v-else class="lb-ladder">
        <div v-for="L in filteredLadders" :key="L.ladder" class="lb-tier" :class="'lb-t' + Math.min(L.ladder, 5)">
          <div class="lb-tier-head">
            <span class="lb-tier-name">{{ ladderText(L.ladder) }}</span>
            <span class="lb-bar"><i :style="{ width: barW(L.stocks.length) }"></i></span>
            <span class="lb-tier-cnt">{{ L.stocks.length }}只</span>
          </div>

          <!-- 高板(≥3板)：逐票一行明细 -->
          <div v-if="L.ladder >= 3" class="lb-rows">
            <div v-for="it in L.stocks" :key="it.code" class="lb-row" @click="openDetail(it)">
              <div class="lb-row-top">
                <span class="lb-badge">{{ ladderText(L.ladder) }}</span>
                <span class="lb-name">{{ it.name }}</span>
                <span class="lb-code">{{ it.code }}</span>
                <span class="lb-tag">{{ it.boardName || it.concept || '-' }}</span>
              </div>
              <div class="lb-m">
                <span class="lb-m-i">封单<b :class="sealCls(it.seal)">{{ yi(it.seal) }}</b>亿</span>
                <span class="lb-m-i">主力<b class="n-neutral">{{ signed(it.mainNet) }}</b>亿</span>
                <span class="lb-m-i">首封<b class="n-gold">{{ hhmm(it.limitTime) }}</b></span>
                <span class="lb-m-i">换手<b class="n-purple">{{ it.turnover ? Number(it.turnover).toFixed(1) : '-' }}</b>%</span>
              </div>
            </div>
          </div>

          <!-- 低板(1~2板)：chips 流（1板默认只铺 12 只，其余一键展开） -->
          <div v-else class="lb-chips">
            <button v-for="it in shownStocks(L)" :key="it.code" class="lb-chip" @click="openDetail(it)">
              {{ it.name }}<em :class="sealCls(it.seal)">{{ yi(it.seal) }}</em>
            </button>
            <button v-if="L.stocks.length > shownStocks(L).length" class="lb-chip lb-more" @click="expandTier(L.ladder)">
              +{{ L.stocks.length - shownStocks(L).length }} 展开
            </button>
          </div>
        </div>

        <!-- 断板反包（独立接口；青色→紫色，主人要求文字不用绿色） -->
        <div v-if="fanbao.length" class="lb-tier lb-tf">
          <div class="lb-tier-head">
            <span class="lb-tier-name">断板反包</span>
            <span class="lb-bar"><i :style="{ width: barW(fanbao.length) }"></i></span>
            <span class="lb-tier-cnt">{{ fanbao.length }}只</span>
          </div>
          <div class="lb-chips">
            <button v-for="it in fanbao" :key="it.code" class="lb-chip lb-chip-f" @click="openDetail(it)">
              {{ it.name }}<em :class="sealCls(it.seal)">{{ yi(it.seal) }}</em>
            </button>
          </div>
        </div>
      </div>
    </template>

    <!-- 盘后天梯图（默认折叠；保留日期回看与下载） -->
    <section v-if="imgDates.length" class="lb-img">
      <button class="lb-img-head" @click="showImg = !showImg">
        <i class="fa fa-image"></i> 盘后天梯图
        <span class="lb-img-sub">每日 15:30 生成</span>
        <span class="lb-img-caret">{{ showImg ? '收起' : '展开' }}</span>
      </button>
      <div v-if="showImg" class="lb-img-body">
        <div class="lb-img-bar">
          <select v-model="imgDate" class="img-select" @change="onImgDate">
            <option v-for="d in imgDates" :key="d" :value="d">{{ d }}</option>
          </select>
          <a class="lb-dl" :href="imgDownloadUrl" download>下载</a>
        </div>
        <img v-if="imgUrl" :src="imgUrl" class="ladder-img" :alt="'连板天梯 ' + imgDate" />
      </div>
    </section>

    <!-- 个股详情（接口 18 个字段尽量都露出来） -->
    <div v-if="detail" class="lb-mask" @click.self="detail = null">
      <div class="lb-modal">
        <div class="lb-modal-h">
          <span class="lb-modal-name">{{ detail.name }}</span>
          <span class="lb-modal-code">{{ detail.code }}</span>
          <span v-if="detail.limitUpDays || detail.ladder" class="lb-badge">{{ ladderText(detail.limitUpDays || detail.ladder) }}</span>
          <button class="lb-modal-x" @click="detail = null"><i class="fa fa-times"></i></button>
        </div>
        <div class="lb-modal-tag">{{ detail.boardName || detail.concept || '-' }}</div>
        <div class="lb-grid">
          <div><span>封单额</span><b :class="sealCls(detail.seal)">{{ yi(detail.seal) }} 亿</b></div>
          <div><span>最大封单</span><b class="n-orange">{{ yi(detail.maxSeal) }} 亿</b></div>
          <div><span>主力净额</span><b class="n-neutral">{{ signed(detail.mainNet) }} 亿</b></div>
          <div><span>成交额</span><b class="n-blue">{{ yi(detail.amount) }} 亿</b></div>
          <div><span>换手率</span><b class="n-purple">{{ detail.turnover ? Number(detail.turnover).toFixed(2) : '-' }}%</b></div>
          <div><span>振幅</span><b class="n-orange">{{ detail.amplitude ? Number(detail.amplitude).toFixed(1) : '-' }}%</b></div>
          <div><span>流通市值</span><b class="n-blue">{{ yi(detail.floatMv) }} 亿</b></div>
          <div><span>首封时间</span><b class="n-gold">{{ hhmm(detail.limitTime) }}</b></div>
        </div>
        <div class="lb-modal-foot">
          <button class="lb-reason-btn" @click="viewReason(detail)"><i class="fa fa-lightbulb-o"></i> 看历史涨停原因</button>
        </div>
      </div>
    </div>

    <!-- 涨停原因弹窗（保留原功能） -->
    <div v-if="reasonModal.show" class="modal-mask" @click.self="reasonModal.show = false">
      <div class="reason-modal">
        <div class="reason-head">
          <span>{{ reasonModal.name }} <span class="sclt">{{ reasonModal.code }}</span></span>
          <button class="close-btn" @click="reasonModal.show = false"><i class="fa fa-times"></i></button>
        </div>
        <div v-if="reasonLoading" class="reason-loading">加载涨停原因...</div>
        <div v-else-if="!reasonModal.list.length" class="reason-loading">暂无涨停原因记录</div>
        <div v-for="(r, i) in reasonModal.list" :key="i" class="reason-item">
          <div class="reason-date">{{ r.date }}</div>
          <div class="reason-text">{{ r.reason || r.text || '-' }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { kplZtEchelon, kplLadderDates, kplZtReason, kplFanbao } from '../api/kpl'
import { trackUsageOnce } from '../api/activity'
import { useUserStore } from '../stores/user'
// 2026-10-01: 旧版的 bjDateTimeStr(时钟) 与 DataStamp(更新于) 已按主人要求整块去掉
import { isIntradayNow, fmtTsTime } from '../utils/time'

const user = useUserStore()

const loading = ref(true)
const stat = ref({ ztCount: 0, maxLadder: 0, spaceDragon: '' })
const promote = ref({})
const ladders = ref([])
const boards = ref([])
const fanbao = ref([])

const imgDates = ref([])
const imgDate = ref('')
const imgUrl = ref('')
const showImg = ref(false)

const detail = ref(null)
const reasonModal = reactive({ show: false, code: '', name: '', list: [] })
const reasonLoading = ref(false)
const activeBoard = ref('')
const tierExpanded = reactive({})
const boardExpand = ref(false)

function yi(v) { return (v === null || v === undefined) ? '0' : (Number(v) / 1e8).toFixed(2) }
function signed(v) {
  const n = Number(v || 0) / 1e8
  return (n >= 0 ? '+' : '') + n.toFixed(2)
}
function pct(v) { return (v === null || v === undefined) ? '--' : (v * 100).toFixed(0) + '%' }
function hhmm(ts) {
  const s = fmtTsTime(ts)
  return s && s.length >= 5 ? s.slice(-5) : '—'
}
/** 档位文案：8 档是「八板+」（rebin 把 ≥8 归到 8，标签不能写死"8板"） */
function ladderText(n) {
  const v = Number(n) || 0
  if (v >= 8) return '8板+'
  return v + '板'
}
/** 封单额按大小着色（大=红 中=橙 小=金）—— 与"不要绿色"一致，不用红绿涨跌色 */
function sealCls(v) {
  const a = Number(v || 0) / 1e8
  if (a >= 3) return 'n-red'
  if (a >= 1) return 'n-orange'
  return 'n-gold'
}
const maxLadderText = computed(() => {
  const v = Number(stat.value.maxLadder) || 0
  return v >= 8 ? '8+' : String(v)
})

const _imgToken = () => (user.apiToken ? `token=${encodeURIComponent(user.apiToken)}` : '')
const imgDownloadUrl = computed(() => (`/api/ladder/image/${imgDate.value}/download${imgDate.value && _imgToken() ? '?' + _imgToken() : ''}`))

function onImgDate() {
  imgUrl.value = `/api/ladder/image/${imgDate.value}${_imgToken() ? '?' + _imgToken() : ''}`
}

async function loadImgDates() {
  try {
    const d = await kplLadderDates()
    imgDates.value = (d && d.dates) || []
    if (imgDates.value.length) {
      imgDate.value = imgDates.value[0]
      onImgDate()
    }
  } catch (e) { /* 静默 */ }
}

// 题材联动(2026-09-20 主人要求): 点击题材过滤下方梯队, 再点取消; 与后端分组同口径取首题材
function toggleBoard(name) {
  activeBoard.value = activeBoard.value === name ? '' : name
}
function firstBoardOf(s) {
  const b = (s.boardName || s.concept || '').trim()
  return (b ? b.split('、')[0].split(',')[0].trim() : '') || '其他'
}
const filteredLadders = computed(() => {
  if (!activeBoard.value) return ladders.value
  return ladders.value
    .map(L => ({ ...L, stocks: L.stocks.filter(s => firstBoardOf(s) === activeBoard.value) }))
    .filter(L => L.stocks.length > 0)
})

/**
 * 题材精简（主人 2026-10-01：「展示的概念太多了要精简」）：
 *   · 保留：家数 ≥3 **或** 该题材内有 ≥2 板（有高度 = 有主线意义）
 *   · 排序：最高板 desc → 家数 desc（主线在左）
 *   · 默认最多 8 个，其余收进「+N 其他」（一点展开）
 *   ⇒ 「汽车零部件 2家·1板」这类长尾噪声不再占位（旧版 16 个 chip 铺了 8 行）。
 */
const BOARDS_SHOWN = 8
const sortedBoards = computed(() => boards.value.slice()
  .sort((a, b) => (b.maxLadder - a.maxLadder) || (b.count - a.count)))
const hiddenBoards = computed(() => sortedBoards.value.filter(b => !(b.count >= 3 || b.maxLadder >= 2)))
const shownBoards = computed(() => (boardExpand.value
  ? sortedBoards.value
  : sortedBoards.value.filter(b => b.count >= 3 || b.maxLadder >= 2).slice(0, BOARDS_SHOWN)))

/** 阶梯条：长度 ∝ 该层家数 ⇒ 金字塔形状一眼可见 */
const maxTierCount = computed(() => Math.max(1, ...ladders.value.map(L => L.stocks.length)))
function barW(n) { return Math.max(8, Math.round((n / maxTierCount.value) * 100)) + '%' }

/** 低板默认铺的 chip 数：1板 40 只若全铺 = 6 屏（旧版最大痛点） */
function shownStocks(L) {
  const cap = tierExpanded[L.ladder] ? 9999 : (L.ladder === 1 ? 12 : 24)
  return L.stocks.slice(0, cap)
}
function expandTier(l) { tierExpanded[l] = true }

function openDetail(it) { detail.value = it }

async function viewReason(it) {
  reasonModal.show = true
  reasonModal.code = it.code
  reasonModal.name = it.name
  reasonModal.list = []
  reasonLoading.value = true
  try {
    const d = await kplZtReason(it.code)
    reasonModal.list = (d && d.list) || []
  } catch (e) { reasonModal.list = [] } finally {
    reasonLoading.value = false
  }
}

// 断板反包（2026-09-28 新增）：独立接口，后端 30 分钟缓存 ⇒ 与梯队同频刷新也不会压上游。
async function loadFanbao() {
  try {
    const d = await kplFanbao()
    fanbao.value = (d && d.list) || []
  } catch (e) { fanbao.value = [] }
}

async function load() {
  try {
    const d = await kplZtEchelon()
    if (d && d.stat) stat.value = d.stat
    if (d && d.promote) promote.value = d.promote
    ladders.value = (d && d.ladders) || []
    boards.value = (d && d.boards) || []
    loadFanbao()
  } catch (e) { /* 静默 */ } finally {
    loading.value = false
  }
}

let echelonTimer = null
onMounted(() => {
  // 2026-09-22 v4.11.35: 涨停梯队打开即算一次(Once 版, 组件内 30s 轮询不重复上报)
  trackUsageOnce('ladder')
  load()
  loadImgDates()
  // 盘中 30s 刷新梯队(收盘/周末不轮询)
  echelonTimer = setInterval(() => { if (isIntradayNow()) load() }, 30000)
})
onBeforeUnmount(() => { if (echelonTimer) clearInterval(echelonTimer) })
</script>

<style scoped>
/* 桌面端限宽居中（旧版是整幅铺开），手机端自然占满 */
.lb-root { max-width: 980px; margin: 0 auto; padding-bottom: calc(70px + env(safe-area-inset-bottom)); }

.lb-head { display: flex; align-items: center; gap: var(--s2); margin-bottom: var(--s2); }
.lb-logo {
  width: 26px; height: 26px; border-radius: var(--r-md); flex: 0 0 auto;
  background: linear-gradient(145deg, var(--qg-red-a), var(--qg-orange-b));
  display: flex; align-items: flex-end; gap: 2px; padding: var(--s1) var(--s1);
}
.lb-logo i { flex: 1; border-radius: 2px 2px 0 0; background: var(--qg-on); }
.lb-logo-b1 { height: 35%; opacity: 0.85; }
.lb-logo-b2 { height: 65%; opacity: 0.92; }
.lb-logo-b3 { height: 100%; }
.lb-h1 { font-size: var(--fs-lg); font-weight: 700; color: var(--text-main); }

/* KPI 四格：红/橙/金/紫 各一色（主人反馈"颜色太单调"） */
.lb-kpi { display: grid; grid-template-columns: 0.8fr 0.8fr 1.3fr 1fr; gap: var(--s2); }
.lb-kpi-i {
  background: var(--bg-card); border: 1px solid var(--border-soft); border-left-width: 3px;
  border-radius: var(--r-md); padding: var(--s1) var(--s2); display: flex; flex-direction: column; gap: 1px; min-width: 0;
}
.lb-kpi-i b { font-size: var(--fs-md); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lb-kpi-i span { font-size: var(--fs-xs); color: var(--text-muted); }
.k-red { border-left-color: var(--qg-red-a); }
.k-red b { color: var(--qg-red-a); }
.k-orange { border-left-color: var(--qg-orange-a); }
.k-orange b { color: var(--qg-orange-a); }
.k-gold { border-left-color: var(--qg-gold-a); }
.k-gold b { color: var(--qg-gold-a); }
.k-purple { border-left-color: var(--qg-purple-a); }
.k-purple b { color: var(--qg-purple-a); }

.lb-promo { display: flex; align-items: center; gap: var(--s2); margin: var(--s2) 2px 0; font-size: var(--fs-xs); color: var(--text-muted); }
.lb-promo-i b { margin-left: var(--s1); font-size: var(--fs-xs); }
.p-red b { color: var(--qg-red-a); }
.p-orange b { color: var(--qg-orange-a); }
.p-gold b { color: var(--qg-gold-a); }
.lb-promo-d { margin-left: auto; color: var(--text-dim); }

.lb-boards { display: flex; gap: var(--s2); overflow-x: auto; margin: var(--s2) -var(--s2); padding: 0 var(--s2) var(--s1); scrollbar-width: none; }
.lb-boards::-webkit-scrollbar { display: none; }
.lb-bd {
  flex: 0 0 auto; display: inline-flex; align-items: center; gap: var(--s1);
  background: var(--bg-input); border: 1px solid var(--border-soft); border-radius: 13px;
  color: var(--qg-gold-a); font-size: var(--fs-xs); padding: var(--s1) var(--s2); cursor: pointer;
}
.lb-bd em { font-style: normal; font-size: var(--fs-xs); color: var(--text-secondary); }
.lb-bd.main { border-color: var(--qg-gold-a); }
.lb-bd.on { background: var(--accent); border-color: var(--accent); color: var(--qg-on); }
.lb-bd.on em { color: var(--qg-hi); }
.lb-bd-more { color: var(--qg-orange-a); border-style: dashed; border-color: var(--qg-orange-a); }

/* ===== 阶梯 ===== */
.lb-ladder { display: flex; flex-direction: column; gap: var(--s2); margin-top: var(--s2); }
.lb-tier {
  background: var(--bg-card); border: 1px solid var(--border-soft); border-left-width: 4px;
  border-radius: var(--r-lg); padding: var(--s2) var(--s2) var(--s2);
}
.lb-tier-head { display: flex; align-items: center; gap: var(--s2); }
.lb-tier-name { font-size: var(--fs-xs); font-weight: 700; flex: 0 0 auto; padding: 1px var(--s2); border-radius: var(--r-md); color: var(--qg-on); }
.lb-tier-cnt { font-size: var(--fs-xs); color: var(--text-muted); flex: 0 0 auto; }
.lb-bar { flex: 1 1 auto; height: 8px; border-radius: var(--r-sm); background: var(--bg-input); overflow: hidden; }
.lb-bar i { display: block; height: 100%; border-radius: var(--r-sm); transition: width 0.25s; }

/* 层色：5板深红 / 4板红 / 3板橙 / 2板金 / 1板蓝（无绿；6板以上沿用最深红=5板档） */
.lb-t5 { border-left-color: var(--qg-red-b); }
.lb-t5 .lb-tier-name { background: linear-gradient(90deg, var(--qg-red-a), var(--qg-red-b)); }
.lb-t5 .lb-bar i { background: linear-gradient(90deg, var(--qg-red-a), var(--qg-red-b)); }
.lb-t4 { border-left-color: var(--qg-red-a); }
.lb-t4 .lb-tier-name { background: var(--qg-red-a); }
.lb-t4 .lb-bar i { background: var(--qg-red-a); }
.lb-t3 { border-left-color: var(--qg-orange-a); }
.lb-t3 .lb-tier-name { background: var(--qg-orange-a); }
.lb-t3 .lb-bar i { background: var(--qg-orange-a); }
.lb-t2 { border-left-color: var(--qg-gold-a); }
.lb-t2 .lb-tier-name { background: var(--qg-gold-b); }
.lb-t2 .lb-bar i { background: var(--qg-gold-a); }
.lb-t1 { border-left-color: var(--qg-blue-a); }
.lb-t1 .lb-tier-name { background: var(--qg-blue-b); }
.lb-t1 .lb-bar i { background: var(--qg-blue-a); }
.lb-tf { border-left-color: var(--qg-purple-a); }
.lb-tf .lb-tier-name { background: var(--qg-purple-a); }
.lb-tf .lb-bar i { background: var(--qg-purple-a); }

.lb-rows { margin-top: var(--s2); display: flex; flex-direction: column; }
.lb-row { padding: var(--s2) 2px; border-top: 1px solid var(--border-soft); cursor: pointer; }
.lb-row:active { background: var(--bg-hover); }
.lb-row-top { display: flex; align-items: baseline; gap: var(--s1); }
.lb-badge {
  font-size: var(--fs-xs); font-weight: 700; color: var(--qg-on); background: var(--qg-red-b);
  border-radius: var(--r-sm); padding: 1px var(--s1); flex: 0 0 auto;
}
.lb-name { font-size: var(--fs-sm); font-weight: 600; color: var(--text-main); }
.lb-code { font-size: var(--fs-xs); color: var(--text-dim); }
.lb-tag { font-size: var(--fs-xs); color: var(--qg-gold-a); margin-left: 2px; }
.lb-m { display: flex; gap: var(--s2); margin-top: var(--s1); font-size: var(--fs-xs); color: var(--text-muted); flex-wrap: wrap; }
.lb-m-i b { margin: 0 1px; font-weight: 700; }
.n-red { color: var(--qg-red-a); }
.n-orange { color: var(--qg-orange-a); }
.n-gold { color: var(--qg-gold-a); }
.n-purple { color: var(--qg-purple-a); }
.n-blue { color: var(--qg-blue-a); }
.n-neutral { color: var(--text-secondary); }

.lb-chips { display: flex; flex-wrap: wrap; gap: var(--s1); margin-top: var(--s2); }
.lb-chip {
  display: inline-flex; align-items: center; gap: var(--s1);
  background: var(--bg-input); border: 1px solid var(--border-soft); border-radius: var(--r-md);
  color: var(--text-main); font-size: var(--fs-xs); padding: var(--s1) var(--s2); cursor: pointer;
}
.lb-chip em { font-style: normal; font-size: var(--fs-xs); font-weight: 700; }
.lb-chip:active { background: var(--bg-hover); }
.lb-t2 .lb-chip { border-color: var(--qg-gold-a); }
.lb-t1 .lb-chip { border-color: var(--qg-blue-a); }
.lb-chip-f { border-color: var(--qg-purple-a); }
.lb-more { color: var(--qg-orange-a); border-style: dashed; border-color: var(--qg-orange-a); }

/* 盘后天梯图（折叠） */
.lb-img { margin-top: var(--s2); border: 1px solid var(--border-soft); border-radius: var(--r-lg); background: var(--bg-card); overflow: hidden; }
.lb-img-head {
  display: flex; align-items: center; gap: var(--s2); width: 100%; padding: var(--s2) var(--s2);
  background: transparent; border: none; color: var(--text-secondary); font-size: var(--fs-xs); cursor: pointer;
}
.lb-img-sub { font-size: var(--fs-xs); color: var(--text-dim); }
.lb-img-caret { margin-left: auto; font-size: var(--fs-xs); color: var(--accent); }
.lb-img-body { padding: 0 var(--s2) var(--s2); }
.lb-img-bar { display: flex; align-items: center; gap: var(--s2); margin-bottom: var(--s2); }
.img-select {
  background: var(--bg-input); color: var(--text-secondary); border: 1px solid var(--border-soft);
  border-radius: var(--r-md); padding: var(--s1) var(--s2); font-size: var(--fs-xs);
}
.lb-dl { font-size: var(--fs-xs); color: var(--accent); text-decoration: none; }
.ladder-img { width: 100%; border-radius: var(--r-md); display: block; }

/* 详情弹层 */
.lb-mask { position: fixed; inset: 0; z-index: 1200; background: rgba(0, 0, 0, 0.6); display: flex; align-items: center; justify-content: center; padding: var(--s4); }
.lb-modal {
  width: 100%; max-width: 420px; background: var(--bg-panel-solid); border: 1px solid var(--border-soft);
  border-top: 3px solid var(--qg-red-a); border-radius: var(--r-lg); padding: var(--s3);
}
.lb-modal-h { display: flex; align-items: baseline; gap: var(--s2); }
.lb-modal-name { font-size: var(--fs-md); font-weight: 700; color: var(--text-main); }
.lb-modal-code { font-size: var(--fs-xs); color: var(--text-dim); }
.lb-modal-x { margin-left: auto; background: transparent; border: none; color: var(--text-muted); cursor: pointer; }
.lb-modal-tag { font-size: var(--fs-xs); color: var(--qg-gold-a); margin: var(--s1) 0 var(--s2); }
.lb-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--s2) var(--s2); }
.lb-grid > div { display: flex; justify-content: space-between; border-bottom: 1px solid var(--border-soft); padding-bottom: var(--s1); font-size: var(--fs-xs); }
.lb-grid span { color: var(--text-muted); }
.lb-grid b { font-weight: 700; }
.lb-modal-foot { margin-top: var(--s2); text-align: right; }
.lb-reason-btn {
  background: transparent; border: 1px solid var(--accent-border); color: var(--accent);
  border-radius: var(--r-md); padding: var(--s1) var(--s2); font-size: var(--fs-xs); cursor: pointer;
}

/* 涨停原因弹窗（沿用原样式） */
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,0.6); display: flex; align-items: center; justify-content: center; z-index: 1300; }
.reason-modal { background: var(--bg-panel-solid); border: 1px solid var(--border-soft); border-radius: var(--r-lg); width: 560px; max-width: 92vw; max-height: 70vh; overflow: auto; padding: var(--s4); }
.reason-head { display: flex; align-items: center; justify-content: space-between; color: var(--qg-gold-a); font-size: var(--fs-lg); margin-bottom: var(--s4); }
.sclt { color: var(--text-dim); font-size: var(--fs-xs); }
.close-btn { background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: var(--fs-lg); }
.reason-loading { color: var(--text-muted); font-size: var(--fs-sm); }
.reason-item { border-top: 1px solid var(--border-soft); padding: var(--s2) 0; }
.reason-date { color: var(--qg-gold-a); font-size: var(--fs-xs); margin-bottom: var(--s1); }
.reason-text { color: var(--text-secondary); font-size: var(--fs-sm); line-height: 1.5; }
</style>
