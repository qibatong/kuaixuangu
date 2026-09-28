<template>
  <div class="page-shell">
    <h1 class="visually-hidden">涨停梯队</h1>
    <!-- 头部 -->
    <div class="zt-head">
      <span class="zt-title"><i class="fa fa-sitemap"></i> 涨停梯队</span>
      <span class="zt-sub">实时涨停梯队 · 晋级率 · 题材主线（盘中持续刷新）</span>
      <span class="zt-time">{{ bjTime }}</span>
      <!-- 2026-09-27 v4.11.63《移动端清单》§二·4: 数据更新时刻(与左边那个"现在几点"的
           时钟不是一回事 —— 它只在成功拉到梯队数据时才前进) -->
      <DataStamp :at="dataAt" :ok="dataOk" :interval="autoOn ? 30 : 0" :stale="dataStale" />
    </div>

    <!-- 顶部统计 -->
    <div v-if="stat.ztCount" class="zt-stat">
      <span class="zt-stat-item">涨停 <b>{{ stat.ztCount }}</b> 家</span>
      <span class="zt-stat-item">最高 <b>{{ stat.maxLadder }}</b> 板</span>
      <span class="zt-stat-item">空间龙 <b>{{ stat.spaceDragon || '-' }}</b></span>
    </div>

    <!-- 晋级率 -->
    <div v-if="promote.date" class="zt-promote">
      <span class="zt-promo-item">首板→2板 <b>{{ pct(promote.r1to2) }}</b></span>
      <span class="zt-promo-item">2→3板 <b>{{ pct(promote.r2to3) }}</b></span>
      <span class="zt-promo-item">3→4板 <b>{{ pct(promote.r3to4) }}</b></span>
      <span class="zt-promo-item">综合 <b>{{ pct(promote.overall) }}</b></span>
      <span class="zt-promo-date">对比 {{ promote.date }}</span>
    </div>

    <!-- 题材分区(2026-09-20 主人要求: 点击联动过滤下方梯队个股, 再点取消) -->
    <div v-if="boards.length" class="zt-boards">
      <div class="zt-board" :class="{ active: !activeBoard }" @click="activeBoard = ''">全部</div>
      <div
v-for="b in boards" :key="b.name" class="zt-board"
           :class="{ main: b.main, active: activeBoard === b.name }" @click="toggleBoard(b.name)"
>
        <span class="zb-name">{{ b.name }}</span>
        <span class="zb-meta">{{ b.count }}家·{{ b.maxLadder }}板</span>
      </div>
    </div>

    <!-- 分层梯队(受题材联动过滤) -->
    <div v-if="loading" class="loading-placeholder"><div class="spinner"></div><div>加载涨停梯队...</div></div>
    <div v-else-if="!filteredLadders.length" class="empty-state">{{ activeBoard ? '该题材暂无涨停梯队个股' : '暂无涨停梯队数据（非交易时段可能为空）' }}</div>
    <div v-else class="zt-ladders">
      <div v-for="L in filteredLadders" :key="L.ladder" class="zt-tier">
        <div class="zt-tier-label" :class="'tier-' + Math.min(L.ladder, 5)">{{ L.ladder }}板 <span class="zt-tier-count">{{ L.stocks.length }}</span></div>
        <div class="zt-tier-cards">
          <div v-for="it in L.stocks" :key="it.code" class="zt-card" :class="'tier-' + Math.min(L.ladder, 5)" @click="viewReason(it)">
            <div class="zc-top">
              <span class="zc-name">{{ it.name }}</span>
              <span class="zc-code">{{ it.code }}</span>
            </div>
            <div class="zc-board">{{ it.boardName || it.concept || '-' }}</div>
            <div class="zc-meta">
              <span v-if="it.seal">封单 {{ yi(it.seal) }}亿</span>
              <span v-if="it.mainNet !== null && it.mainNet !== undefined" :class="it.mainNet >= 0 ? 'up' : 'down'">{{ it.mainNet >= 0 ? '主力吸筹' : '主力出货' }} {{ yi(Math.abs(it.mainNet)) }}亿</span>
              <span v-if="it.turnover">换手 {{ it.turnover.toFixed(2) }}%</span>
            </div>
            <!-- ★ 2026-09-28：接口本来就下发了这些字段（`_parse_ladder` 共 18 个），卡片此前只画了 7 个
                 ⇒ 补上，零上游成本。单位统一走 yi()=亿元。
                 ⚠️ 刻意**不显示** `ztCount` —— `ladder_image.py` 明确写过「连板数用 pid，不用 ztCount
                    （不可靠）」；档位以 rebin 后的真实连板数为准（就是上方的「N板」）。 -->
            <div class="zc-meta zc-meta2">
              <span v-if="it.limitTime">首封 {{ hhmm(it.limitTime) }}</span>
              <span v-if="it.maxSeal">最大封单 {{ yi(it.maxSeal) }}亿</span>
              <span v-if="it.amplitude">振幅 {{ Number(it.amplitude).toFixed(1) }}%</span>
              <span v-if="it.amount">成交 {{ yi(it.amount) }}亿</span>
              <span v-if="it.floatMv">流通 {{ yi(it.floatMv) }}亿</span>
              <!-- ⚠️ 上游 `mainSell` 是**负数**（实测：买 0.42 + 卖 −0.15 = 主力净额 0.27 ✓ 对得上）
                   ⇒ 这里必须取绝对值，否则会显示成「卖-0.15亿」。 -->
              <span v-if="it.mainBuy || it.mainSell" class="zc-mb">主力 买{{ yi(it.mainBuy || 0) }}/卖{{ yi(Math.abs(it.mainSell || 0)) }}亿</span>
            </div>
            <div v-if="it.reason" class="zc-reason">{{ it.reason }}</div>
          </div>
        </div>
      </div>
    </div>

    <!-- 断板反包（★ 2026-09-28 新增）: 昨日断板、今日重新起板且近 5 个交易日有涨停史
         （不是新首板）。后端 kpl.fetch_fanbao_stocks() 早就有，此前**只在盘后 PNG 图里**，
         网页端一直看不到。仅在有数据时渲染，空态不占版面。 -->
    <div v-if="fanbao.length" class="zt-tier fanbao-tier">
      <div class="zt-tier-label fanbao-label">断板反包 <span class="zt-tier-count">{{ fanbao.length }}</span></div>
      <div class="zt-tier-cards">
        <div v-for="it in fanbao" :key="it.code" class="zt-card fanbao-card" @click="viewReason(it)">
          <div class="zc-top">
            <span class="zc-name">{{ it.name }}</span>
            <span class="zc-code">{{ it.code }}</span>
          </div>
          <div class="zc-meta">
            <span :class="Number(it.change) >= 0 ? 'up' : 'down'">{{ Number(it.change) >= 0 ? '+' : '' }}{{ it.change }}%</span>
          </div>
          <div v-if="it.reason" class="zc-reason">{{ it.reason }}</div>
        </div>
      </div>
    </div>

    <!-- 盘后天梯图 -->
    <div v-if="imgDates.length" class="img-panel">
      <div class="img-head">
        <span class="img-title"><i class="fa fa-image"></i> 盘后涨停梯队图</span>
        <span class="img-sub">每日 15:30 自动生成</span>
        <span class="img-date-pick">
          日期
          <select v-model="imgDate" class="img-select" @change="onImgDate">
            <option v-for="d in imgDates" :key="d" :value="d">{{ d }}</option>
          </select>
          <a class="img-dl" :href="imgDownloadUrl" download>下载</a>
        </span>
      </div>
      <div v-if="imgUrl" class="img-body">
        <img :src="imgUrl" class="ladder-img" :alt="'涨停梯队 ' + imgDate" />
      </div>
    </div>

    <!-- 涨停原因弹窗 -->
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
import { onMounted, onBeforeUnmount, ref, reactive, computed } from 'vue'
import { usePolling } from '../composables/usePolling'
import { kplZtEchelon, kplLadderDates, kplZtReason, kplFanbao } from '../api/kpl'
import { trackUsageOnce } from '../api/activity'
import { useUserStore } from '../stores/user'
import { bjDateTimeStr, isIntradayNow, fmtTsTime } from '../utils/time'
import { useDataStamp } from '../composables/useDataStamp'
import DataStamp from '../components/DataStamp.vue'

const user = useUserStore()

const loading = ref(true)
const bjTime = ref('--:--:--')
// 清单 §二·4: 数据更新时刻 + "当前是否在自动刷新"（后者决定要不要显示「每 30s 自动刷新」）
// ⚠️ 这里必须**分解赋值**：useDataStamp 返回的是普通对象，模板只会自动解包「顶层 ref」，
//    写 `ds.at` 会把 ref 对象本身渲染出来（要写成 ds.at.value 才对）—— 分解后既干净又不会踩。
const { at: dataAt, ok: dataOk, stale: dataStale, mark: markData } = useDataStamp()
const autoOn = ref(false)
const stat = ref({ ztCount: 0, maxLadder: 0, spaceDragon: '' })
const promote = ref({})
const ladders = ref([])
const boards = ref([])

// 盘后天梯图
const imgDates = ref([])
const imgDate = ref('')
const imgUrl = ref('')

const reasonModal = reactive({ show: false, code: '', name: '', list: [] })
const reasonLoading = ref(false)

function _imgToken() {
  return user.apiToken ? `token=${encodeURIComponent(user.apiToken)}` : ''
}
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

function yi(v) { return (v / 1e8).toFixed(2) }
// 首封时间：接口给的是 **epoch 秒**（见 backend/app/services/ladder_image.py 的口径注释），
// 复用项目自带的 `fmtTsTime`（UTC+8 格式化，返回 'YYYY-MM-DD HH:MM'）⇒ 取后 5 位就是 HH:MM。
function hhmm(ts) {
  const s = fmtTsTime(ts)
  return s && s.length >= 5 ? s.slice(-5) : '—'
}
function pct(v) { return (v === null || v === undefined) ? '--' : (v * 100).toFixed(0) + '%' }

// 题材联动(2026-09-20 主人要求): 点击题材过滤下方梯队, 再点取消; 与后端分组同口径取首题材
const activeBoard = ref('')
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

async function viewReason(it) {
  reasonModal.show = true
  reasonModal.code = it.code
  reasonModal.name = it.name
  reasonModal.list = []
  reasonLoading.value = true
  try {
    const d = await kplZtReason(it.code)
    reasonModal.list = d.reason || []
  } catch (e) {
    reasonModal.list = []
  } finally {
    reasonLoading.value = false
  }
}

// 断板反包（2026-09-28 新增）：独立接口，后端 30 分钟缓存 ⇒ 与梯队同频刷新也不会压上游。
const fanbao = ref([])
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
    markData()          // ★ 只在成功路径推进「更新于」；失败不得推进（见 useDataStamp 注释）
  } catch (e) { /* 静默 */ } finally {
    loading.value = false
  }
}

let echelonTimer = null
// ⚠️ 2026-09-27 v4.11.59 修: 时钟轮询从 onMounted 回调搬到 setup 顶层 ——
//    Vue 调用 mounted 回调时 currentInstance 为 null, usePolling 内的 onBeforeUnmount
//    会静默注册失败 ⇒ 1s 定时器永不清理(详见 EmConceptPanel.vue 的长注释)。
usePolling(() => { bjTime.value = bjDateTimeStr(); autoOn.value = isIntradayNow() }, 1000, { immediate: false })
onMounted(() => {
  bjTime.value = bjDateTimeStr()
  autoOn.value = isIntradayNow()
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
.zt-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
/* 断板反包区(2026-09-28): 与梯队同构, 但用**青色**边框与「N板」的红梯度区分开
   (红=连板强度, 青=形态反包) */
.fanbao-tier { margin-top: 14px; }
.fanbao-label { color: #4dd0c2; }
.fanbao-card { border-left: 3px solid #4dd0c2; }
/* 卡片第二行补充字段(2026-09-28)：首封时间/最大封单/振幅/成交/流通/主力买卖 ——
   单行放不下会自然换行，字号比主行再小半级，避免把主信息(封单/主力净额/换手)淹掉 */
.zc-meta2 { margin-top: 2px; opacity: 0.82; }
.zc-meta2 .zc-mb { color: #c9a86a; }
.zt-title { font-size: 1.25rem; font-weight: 700; color: #ffe0a0; }
.zt-title .fa { color: #ffb400; }
.zt-sub { color: var(--text-muted); font-size: 0.8125rem; }
.zt-time { margin-left: auto; color: #aaa; font-size: 0.875rem; font-family: inherit; }

/* 顶部统计 */
.zt-stat { display: flex; gap: 18px; flex-wrap: wrap; margin-bottom: 10px; font-size: 0.8125rem; color: var(--text-secondary); }
.zt-stat-item b { color: #ff6a6a; font-size: 0.9375rem; margin: 0 2px; }

/* 晋级率 */
.zt-promote { display: flex; gap: 14px; flex-wrap: wrap; align-items: center; margin-bottom: 12px; padding: 8px 12px; background: rgba(255,180,0,0.08); border: 1px solid rgba(255,180,0,0.25); border-radius: 8px; font-size: 0.75rem; color: var(--text-secondary); }
.zt-promo-item b { color: #ffb400; font-size: 0.8125rem; margin-left: 3px; }
.zt-promo-date { color: var(--text-muted); font-size: 0.75rem; margin-left: auto; }

/* 题材分区 */
.zt-boards { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; }
.zt-board { display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px; border-radius: 6px; background: var(--bg-hover); border: 1px solid var(--border-soft); font-size: 0.75rem; color: var(--text-secondary); cursor: pointer; transition: border-color 0.15s, background 0.15s; }
.zt-board:hover { border-color: var(--accent-deep); }
.zt-board.main { background: rgba(255,80,80,0.14); border-color: rgba(255,80,80,0.5); }
.zt-board.active { background: rgba(255,180,0,0.18); border-color: #ffb400; }
.zt-board.active .zb-name { color: #ffb400; }
.zb-name { font-weight: 600; color: var(--text-main); }
.zb-meta { color: var(--text-muted); font-size: 0.75rem; }

/* 分层梯队 */
.zt-ladders { display: flex; flex-direction: column; gap: 14px; }
.zt-tier { display: flex; gap: 10px; align-items: flex-start; }
.zt-tier-label { flex: 0 0 52px; text-align: center; font-size: 0.9375rem; font-weight: 700; padding: 8px 0; border-radius: 8px; color: #fff; }
.zt-tier-label .zt-tier-count { display: block; font-size: 0.75rem; font-weight: 500; opacity: 0.85; }
.zt-tier-cards { flex: 1; display: flex; gap: 8px; flex-wrap: wrap; }

/* 高度配色: 4+ 红 / 3 黄 / 2 红 / 1 灰 */
.tier-5, .tier-4 { background: #c0392b; }
.tier-3 { background: #d9a020; }
.tier-2 { background: #d9534f; }
.tier-1 { background: #6b7280; }

.zt-card { flex: 1 1 200px; min-width: 200px; max-width: 320px; padding: 8px 10px; border-radius: 8px; background: var(--bg-hover); border: 1px solid var(--border-soft); cursor: pointer; transition: border-color 0.15s; }
.zt-card:hover { border-color: var(--accent-deep); }
.zt-card.tier-4, .zt-card.tier-5 { border-left: 3px solid #ff5252; }
.zt-card.tier-3 { border-left: 3px solid #ffb400; }
.zt-card.tier-2 { border-left: 3px solid #ff8a80; }
.zt-card.tier-1 { border-left: 3px solid #9aa3af; }
.zc-top { display: flex; align-items: baseline; gap: 6px; }
.zc-name { font-size: 0.8125rem; font-weight: 600; color: var(--text-main); }
.zc-code { font-size: 0.75rem; color: var(--text-muted); font-family: inherit; }
.zc-board { font-size: 0.75rem; color: #ffb400; margin: 2px 0; }
.zc-meta { display: flex; gap: 8px; flex-wrap: wrap; font-size: 0.75rem; color: var(--text-muted); margin: 2px 0; }
.zc-meta .up { color: #ff6a6a; }
.zc-meta .down { color: #6ad66a; }
.zc-reason { font-size: 0.75rem; color: var(--text-dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* 盘后天梯图 */
.img-panel { background: var(--bg-hover); border: 1px solid var(--border-soft); border-radius: 10px; margin-top: 16px; }
.img-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 12px 14px; border-bottom: 1px solid var(--border-soft); }
.img-title { font-size: 1rem; font-weight: 700; color: #ffe0a0; }
.img-title .fa { color: #ffb400; }
.img-sub { color: var(--text-muted); font-size: 0.75rem; }
.img-date-pick { margin-left: auto; display: flex; align-items: center; gap: 8px; color: var(--text-secondary); font-size: 0.8125rem; }
.img-select { padding: 6px 10px; border-radius: 8px; border: 1px solid var(--border-soft); background: var(--bg-panel-solid); color: var(--text-main); font-size: 0.8125rem; }
.img-dl { display: inline-block; padding: 7px 14px; border-radius: 8px; background: var(--accent-deep); color: #fff; font-size: 0.8125rem; text-decoration: none; }
.img-dl:hover { opacity: 0.85; }
.img-body { padding: 14px; overflow-x: auto; }
.ladder-img { display: block; max-width: 960px; width: 100%; height: auto; border-radius: 6px; }

/* 弹窗 */
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,0.6); display: flex; align-items: center; justify-content: center; z-index: 1000; }
.reason-modal { background: var(--bg-panel-solid); border: 1px solid var(--border-soft); border-radius: 12px; width: 560px; max-width: 92vw; max-height: 70vh; overflow: auto; padding: 18px; }
.reason-head { display: flex; align-items: center; justify-content: space-between; color: #ffe0a0; font-size: 1rem; margin-bottom: 14px; }
.close-btn { background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: 1rem; }
.close-btn:hover { color: #ff6a6a; }
.reason-loading { color: var(--text-muted); padding: 20px; text-align: center; }
.reason-item { padding: 10px 0; border-bottom: 1px solid var(--border-soft); }
.reason-date { color: #ffb400; font-size: 0.8125rem; margin-bottom: 6px; }
.sclt { display: inline-block; margin-left: 8px; color: var(--accent-deep); font-size: 0.75rem; border: 1px solid rgba(var(--accent-rgb), 0.5); border-radius: 4px; padding: 0 6px; }
.reason-text { color: var(--text-secondary); font-size: 0.8125rem; line-height: 1.6; white-space: pre-wrap; }

.loading-placeholder { text-align: center; padding: 40px; color: var(--text-muted); }
.spinner { width: 28px; height: 28px; border: 3px solid rgba(255,180,0,0.3); border-top-color: #ffb400; border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 10px; }
@keyframes spin { to { transform: rotate(360deg); } }
.empty-state { text-align: center; padding: 40px; color: var(--text-muted); }

/* 浅色主题 */
body[data-bg="light"] .zt-title { color: #8a5500; }
body[data-bg="light"] .zt-title .fa { color: #c79100; }
body[data-bg="light"] .zt-promote { background: rgba(255,180,0,0.1); border-color: rgba(199,145,0,0.3); }
body[data-bg="light"] .zt-promo-item b { color: #8a5500; }
body[data-bg="light"] .zt-board.main { background: rgba(184,48,16,0.1); border-color: rgba(184,48,16,0.4); }
body[data-bg="light"] .zt-board.active { background: rgba(199,145,0,0.15); border-color: #c79100; }
body[data-bg="light"] .zt-board.active .zb-name { color: #8a5500; }
body[data-bg="light"] .zb-name { color: #1a1d26; }
body[data-bg="light"] .zc-name { color: #1a1d26; }
body[data-bg="light"] .zc-board { color: #8a5500; }
body[data-bg="light"] .img-title { color: #8a5500; }
body[data-bg="light"] .img-dl { background: #c79100; }
body[data-bg="light"] .reason-head { color: #5a4a3a; }
body[data-bg="light"] .reason-date { color: #8a5500; }
body[data-bg="light"] .sclt { color: #b83010; border-color: rgba(184,48,16,0.5); }
body[data-bg="light"] .reason-modal { background: rgba(255,255,255,0.98); }

/* 手机端 */
@media (max-width: 768px) {
  .zt-title { font-size: 1.0625rem; }
  .zt-tier { flex-direction: column; gap: 6px; }
  .zt-tier-label { flex: 0 0 auto; width: 100%; padding: 5px 0; border-radius: 6px; }
  .zt-tier-label .zt-tier-count { display: inline; margin-left: 6px; }
  .zt-card { min-width: 100%; max-width: 100%; }
  .img-date-pick { margin-left: 0; width: 100%; }
  .img-dl { flex: 1; text-align: center; }
}
</style>
