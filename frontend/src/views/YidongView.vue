<template>
  <div class="page-shell">
    <h1 class="visually-hidden">异动监管</h1>
    <div class="yd-head">
      <span class="yd-title"><i class="fa fa-bullhorn"></i> 异动监管</span>
      <span class="yd-sub">严重异动 · 异动计算器 · 重点监控</span>
      <span class="yd-time">{{ bjTime }}</span>
    </div>

    <!--
      🔴 2026-09-28 主人拍板：置顶的「⑥ 实时异动流」**整块移除**（组件 YidongFlow.vue 一并删除），
        本页也不再请求 `/api/kpl/yidong-realtime` —— 页面从「异动监管」标题直接进入下方三个 tab。
        ⚠️ 该接口**仍在用**：严重异动 tab 里「交易所已公布异动（开盘啦）」折叠表走同一个
           `kplYidongRealtime`，那块**保留**（见下方 loadPub）⇒ 别把这个 api 函数当死代码删掉。
        沿革：它原在盘中页（MarketView），2026-09-28 早先才搬到本页置顶并修好取数，当天又按主人要求去掉。
    -->

    <div class="yd-tabs">
      <button class="yd-tab" :class="{ active: tab === 'warn' }" @click="switchTab('warn')">
        <i class="fa fa-exclamation-triangle"></i> 严重异动
      </button>
      <button class="yd-tab" :class="{ active: tab === 'calc' }" @click="switchTab('calc')">
        <i class="fa fa-calculator"></i> 异动计算器
      </button>
      <button class="yd-tab" :class="{ active: tab === 'monitor' }" @click="switchTab('monitor')">
        <i class="fa fa-eye"></i> 重点监控
      </button>
    </div>

    <!-- ① 严重异动：我方按交易所口径算出的「明日/今日越线」名单（盘后 15:45 生成） -->
    <div v-if="tab === 'warn'" class="yd-panel">
      <div class="yd-toolbar">
        <span class="yd-tip">
          <i class="fa fa-info-circle"></i>
          全市场 10/30 日涨跌幅偏离值（交易所口径：区间首尾相减）—— 红 = 今日已越线，黄 = 明日涨停即越线或已临近
        </span>
        <span v-if="devStatusText" class="yd-badge">{{ devStatusText }}</span>
        <button class="rot-reset-btn" title="刷新" aria-label="刷新" @click="reloadWarn">
          <i class="fa fa-refresh"></i>
        </button>
      </div>

      <DevWarnList :rows="devRows" :loading="devLoading" :failed="devFailed" :date="devDate" />

      <!-- 交易所已公布的异动（开盘啦，保留原「严重异动」数据源；折叠不抢主位） -->
      <div class="yd-fold">
        <button class="yd-fold-h" @click="showPub = !showPub">
          <i class="fa" :class="showPub ? 'fa-caret-down' : 'fa-caret-right'"></i>
          交易所已公布异动（开盘啦，共 {{ pubRows.length }} 只）
        </button>
        <div v-if="showPub" class="yd-fold-b">
          <div v-if="pubLoading" class="loading-placeholder"><div class="spinner"></div><div>加载中…</div></div>
          <div v-else-if="!pubRows.length" class="empty-state">暂无已公布异动数据</div>
          <table v-else class="stock-table">
            <thead>
              <tr><th>名称</th><th>异动类型</th><th>偏离值</th><th>触发条件</th><th>是否触发</th></tr>
            </thead>
            <tbody>
              <tr v-for="item in pubRows" :key="item.code">
                <td class="stock-info-cell" @click="linkToSoftware(item.code)">
                  <div class="stock-name-row"><span class="stock-name">{{ item.name }}</span></div>
                  <div class="stock-code-row"><span class="stock-code">{{ item.code }}</span></div>
                </td>
                <td class="type-col">{{ item.type }}</td>
                <td class="dev-col">
                  <span v-if="item.deviation != null" class="dev-num">{{ fmtNum(item.deviation) }}%</span>
                  <span v-if="item.days" class="dev-days">{{ item.days }}日</span>
                  <span v-else-if="item.deviation == null">-</span>
                </td>
                <td class="trigger-col">{{ item.trigger }}</td>
                <td><span class="trigger-status" :class="{ triggered: isTriggered(item.triggered) }">{{ item.triggered }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- ② 异动计算器：任意代码实时算三条线 + 明日触发空间 + 十日投影 -->
    <div v-else-if="tab === 'calc'" class="yd-panel">
      <div class="yd-toolbar">
        <span class="yd-tip"><i class="fa fa-info-circle"></i> 实时计算（不依赖盘后名单，任意代码可用）</span>
      </div>
      <div class="cal-bar">
        <input
          v-model="calcCode"
          class="cal-input"
          type="text"
          placeholder="输入代码 / 名称 / 拼音，如 605058 或 电科"
          aria-label="股票代码"
          @input="onCalcInput"
          @keydown.enter="onCalcEnter"
          @keydown.down.prevent="moveSug(1)"
          @keydown.up.prevent="moveSug(-1)"
          @keydown.esc="closeSug"
        />
        <button class="cal-btn" :disabled="calcLoading" @click="doCalc">
          <i class="fa fa-search"></i> {{ calcLoading ? '计算中…' : '计算' }}
        </button>
        <button v-if="calcCode" class="cal-btn cal-btn-ghost" @click="clearCalc">清空</button>
      </div>

      <!-- 🔎 索引下拉（2026-10-01 主人: 「异动计算器搜索框是不是类似主页搜索框，有个索引」）
           数据源与顶栏搜索**完全相同**：GET /api/stocks/search —— 后端本地索引(约 5561 条, 进程内 TTL 600s),
           匹配口径 = 代码精确 > 代码前缀 > 拼音前缀 > 名称前缀 > 名称包含 > 板块。
           价值: 名称/拼音有歧义时**看得见候选**（原实现是 limit=1 盲取第一条, 选错了无从察觉）。 -->
      <div v-if="sugRows.length" class="cal-sug" role="listbox" aria-label="股票候选">
        <button
          v-for="(r, i) in sugRows" :key="r.code" class="cal-sug-i"
          :class="{ on: i === sugIdx }" role="option" :aria-selected="i === sugIdx"
          @click="pickSug(r)"
        >
          <span class="cal-sug-name">{{ r.name }}</span>
          <span class="cal-sug-code">{{ r.code }}</span>
          <span v-if="r.board" class="cal-sug-board">{{ firstConcept(r.board) }}</span>
        </button>
      </div>
      <div v-else-if="sugPhase === 'empty'" class="cal-sug cal-sug-empty">未找到匹配股票（可直接输入 6 位代码）</div>
      <DevRiskDetail
        :data="calcData"
        :loading="calcLoading"
        :failed="calcFailed"
        :code="calcShown"
      />
    </div>

    <!-- ③ 重点监控：交易所当日重点监控名单（开盘啦），保持原样 -->
    <div v-else class="yd-panel">
      <div class="yd-toolbar">
        <span class="yd-tip"><i class="fa fa-info-circle"></i> 当日重点监控股票列表</span>
        <button class="rot-reset-btn" title="刷新" aria-label="刷新" @click="loadMonitor"><i class="fa fa-refresh"></i></button>
      </div>
      <div v-if="monLoading" class="loading-placeholder"><div class="spinner"></div><div>加载重点监控...</div></div>
      <div v-else-if="!monList.length" class="empty-state">暂无重点监控数据</div>
      <table v-else class="stock-table">
        <thead>
          <tr>
            <th>排名</th>
            <th class="sortable merged-col" :class="{ active: monSort.keyOf('code') || monSort.keyOf('name') }" @click="monSort.onSort('code', 'string')">名称<span class="sort-ind">{{ monSort.ind('code') }}</span></th>
            <th class="sortable" :class="{ active: monSort.keyOf('startDate') }" @click="monSort.onSort('startDate', 'string')">开始日期<span class="sort-ind">{{ monSort.ind('startDate') }}</span></th>
            <th class="sortable" :class="{ active: monSort.keyOf('endDate') }" @click="monSort.onSort('endDate', 'string')">结束日期<span class="sort-ind">{{ monSort.ind('endDate') }}</span></th>
            <th class="sortable" :class="{ active: monSort.keyOf('times') }" @click="monSort.onSort('times')">次数<span class="sort-ind">{{ monSort.ind('times') }}</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(item, idx) in monSort.sorted(monList)" :key="item.code">
            <td class="rank-col">{{ idx + 1 }}</td>
            <td class="stock-info-cell" @click="linkToSoftware(item.code)">
              <div class="stock-name-row"><span class="pool-hover-wrap"><span class="stock-name">{{ item.name }}</span><PoolHoverBtn :item="item" /></span></div>
              <div class="stock-code-row"><span class="stock-code">{{ item.code }}</span></div>
            </td>
            <td>{{ item.startDate }}</td>
            <td>{{ item.endDate }}</td>
            <td><span class="lb-badge">{{ item.times }}次</span></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
/**
 * 异动监管 `/yidong` —— v4.11.64 按《快选异动停牌风险功能工单》改为**三 tab**：
 *
 *   ① 严重异动   我方按上交所 5.4.2 口径自算的「越线/将越线」名单（`/api/dev/tomorrow`）
 *                + 折叠保留开盘啦已公布异动（原 tab 内容，不丢数据）
 *   ② 异动计算器 任意代码实时算（`/api/dev/risk`）
 *   ③ 重点监控   交易所当日重点监控（`/api/kpl/yidong-monitor`），保持原样
 *
 * ★ 已按主人拍板**删除**原「热门股偏离值」与「多次异动」两个 tab。
 *
 * 口径（★ 与工单正文相反，2026-09-27 主人拍板取交易所口径）：
 *   偏离值 =（期末收盘/期初前收盘 − 1）×100% −（对应指数同区间 − 1）×100%
 *   ——「区间首尾相减」，**不是**工单正文写的「逐日偏离值求和」。
 *   3 天各涨停时区间法 = 33.10%，逐日累加 = 30.00%，两者结果不同。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { usePolling } from '../composables/usePolling'
import { useSortable } from '../composables/useSortable'
import { request } from '../api/request'
import { stocksSearch } from '../api/stocks'
import { kplYidongRealtime, kplYidongMonitor } from '../api/kpl'
import { devRisk, devTomorrow } from '../api/dev'
import { linkToSoftware } from '../utils/tdx'
import { bjTimeStr } from '../utils/time'
import { fmtNum } from '../utils/format'
import PoolHoverBtn from '../components/PoolHoverBtn.vue'
import DevWarnList from '../components/DevWarnList.vue'
import DevRiskDetail from '../components/DevRiskDetail.vue'

const props = defineProps({
  // 首屏落在哪个 tab（warn / calc / monitor）。
  // ★ 存在的理由有二：① 让 SSR 冒烟测试能渲染「异动计算器」分支
  //   （它是纯 DOM 交互分支，不渲染就完全测不到 —— 本项目两次模板事故都是这么漏的）；
  //   ② 将来从别处深链到「计算器」时可以直接传参，不必再解析 query。
  initialTab: { type: String, default: 'warn' },
})

const tab = ref(['warn', 'calc', 'monitor'].indexOf(props.initialTab) >= 0 ? props.initialTab : 'warn')
const bjTime = ref('--:--:--')

// ---- ① 严重异动（我方结果表）----
const devRows = ref([])
const devLoading = ref(true)
const devFailed = ref(false)
const devDate = ref('')

// ---- ① 交易所已公布异动（开盘啦，折叠）----
const pubRows = ref([])
const pubLoading = ref(true)
const showPub = ref(false)

// ---- ② 异动计算器 ----
const calcCode = ref('')
const calcShown = ref('')        // 已提交计算的代码（与输入框解耦：输入框改了不立刻重算）
const calcData = ref(null)
const calcLoading = ref(false)
const calcFailed = ref(false)

// ---- ③ 重点监控 ----
const monList = ref([])
const monLoading = ref(true)
const monSort = useSortable()

const devStatusText = computed(() => {
  if (devFailed.value) return '读取失败'
  if (!devRows.value.length) return ''
  return `共 ${devRows.value.length} 只`
})

function switchTab(t) {
  tab.value = t
  monSort.clear()
}

function isTriggered(status) {
  return status && (status.includes('已触发') || status.includes('已停牌'))
}

async function loadWarn() {
  devLoading.value = true
  devFailed.value = false
  try {
    const d = await devTomorrow()
    devRows.value = (d && d.list) || []
    devDate.value = (d && d.date) || ''
  } catch (e) {
    devRows.value = []
    devDate.value = ''
    devFailed.value = true
  } finally {
    devLoading.value = false
  }
}

function reloadWarn() {
  loadWarn()
  loadPub()
}

async function loadPub() {
  pubLoading.value = true
  try {
    const d = await kplYidongRealtime()
    pubRows.value = (d && d.list) || []
  } catch (e) {
    pubRows.value = []
  } finally {
    pubLoading.value = false
  }
}

async function loadMonitor() {
  monLoading.value = true
  try {
    const d = await kplYidongMonitor()
    monList.value = (d && d.list) || []
  } catch (e) {
    monList.value = []
  } finally {
    monLoading.value = false
  }
}

// ---- 计算器搜索框的"索引"下拉（与顶栏 StockSearch 同一后端数据源）----
const sugRows = ref([])
const sugIdx = ref(-1)
const sugPhase = ref('idle')   // idle | loading | ok | empty
let sugTimer = null
let sugSeq = 0                 // 序号丢弃过期响应（与 StockSearch.vue 同一手法）

function closeSug() { sugRows.value = []; sugIdx.value = -1; sugPhase.value = 'idle' }

/** 输入即查（300ms 防抖，与顶栏搜索同频）；6 位纯数字=代码本身，不必查 */
function onCalcInput() {
  const q = String(calcCode.value || '').trim()
  if (sugTimer) clearTimeout(sugTimer)
  if (!q || /^\d{6}$/.test(q)) { closeSug(); return }
  sugPhase.value = 'loading'
  sugTimer = setTimeout(async () => {
    const my = ++sugSeq
    try {
      const d = await stocksSearch(q, 20)
      if (my !== sugSeq) return
      sugRows.value = (d && d.list) || []
      sugIdx.value = sugRows.value.length ? 0 : -1
      sugPhase.value = sugRows.value.length ? 'ok' : 'empty'
    } catch (e) {
      if (my !== sugSeq) return
      sugRows.value = []
      sugIdx.value = -1
      sugPhase.value = 'empty'
    }
  }, 300)
}

/** 候选行只显示**首个概念**（`board` 是整串概念，几十个词会撑成一片文字） */
function firstConcept(b) {
  return String(b || '').split(',')[0].split('、')[0].trim()
}

function moveSug(step) {
  if (!sugRows.value.length) return
  const n = sugRows.value.length
  sugIdx.value = (sugIdx.value + step + n) % n
}

/** 选中候选 ⇒ 用**确定的 code** 计算（不再盲取搜索结果第一条） */
function pickSug(r) {
  calcCode.value = r.code
  closeSug()
  doCalc()
}

function onCalcEnter() {
  if (sugRows.value.length && sugIdx.value >= 0) { pickSug(sugRows.value[sugIdx.value]); return }
  doCalc()
}

// 卸载清防抖定时器（否则离开页面后 300ms 还会打一次搜索接口）
onBeforeUnmount(() => { if (sugTimer) clearTimeout(sugTimer) })

async function doCalc() {
  let c = String(calcCode.value || '').trim()
  if (!/^\d{6}$/.test(c)) {
    // 名称/拼音 → 先搜
    try {
      const d = await request('/api/stocks/search', { query: { q: c, limit: 1 } })
      const hit = (d && d.list && d.list[0])
      if (hit && hit.code) { c = hit.code }
      else {
        calcShown.value = c
        calcData.value = { ok: false, reason: 'not_found', msg: '未找到匹配股票: ' + c }
        calcFailed.value = false
        return
      }
    } catch(e) {
      calcShown.value = c
      calcData.value = { ok: false, reason: 'search_fail', msg: '搜索失败: ' + c }
      calcFailed.value = false
      return
    }
  }
  calcShown.value = c
  calcLoading.value = true
  calcFailed.value = false
  try {
    // ★ 后端算不出时返 200 + ok:false（带 reason），**不是**请求失败 —— 两者必须分开
    calcData.value = await devRisk(c)
  } catch (e) {
    calcData.value = null
    calcFailed.value = true
  } finally {
    calcLoading.value = false
  }
}

function clearCalc() {
  calcCode.value = ''
  calcShown.value = ''
  calcData.value = null
  calcFailed.value = false
}

// ⚠️ 与 v4.11.59 同一条纪律：usePolling 必须注册在 **setup 顶层**，
//   不能在 onMounted 回调里（那时 currentInstance 为 null，onBeforeUnmount 静默失败
//   ⇒ 定时器与 visibilitychange 监听永不清理）。
//   首拉由 onMounted 显式完成 ⇒ immediate:false，顺带消掉首屏双请求。
usePolling(() => { bjTime.value = bjTimeStr() }, 1000, { immediate: false })
// 名单类数据每 30 秒刷新；异动计算器**不轮询**（用户主动触发，避免覆盖他正在看的票）
usePolling(() => { loadWarn(); loadMonitor() }, 30000, { immediate: false })

onMounted(() => {
  bjTime.value = bjTimeStr()
  loadWarn()
  loadPub()
  loadMonitor()
  // 支持 /yidong?code=600519 直接带票进入（浏览器环境才有 location）
  try {
    const q = new URLSearchParams(window.location.search).get('code') || ''
    if (/^\d{6}$/.test(q)) {
      calcCode.value = q
      tab.value = 'calc'
      doCalc()
    }
  } catch (e) { /* 非浏览器环境忽略 */ }
})
</script>

<style scoped>
.yd-head {
  display: flex; align-items: baseline; gap: var(--s3); flex-wrap: wrap; margin-bottom: var(--s3);
}
.yd-title { font-size: var(--fs-2xl); font-weight: 700; color: var(--warn-text); }
.yd-title .fa { color: var(--star); }
.yd-sub { color: var(--text-muted); font-size: var(--fs-sm); }
.yd-time { margin-left: auto; color: var(--text-muted); font-size: var(--fs-base); font-family: inherit; }

.yd-tabs { display: flex; gap: var(--s2); margin-bottom: var(--s4); }
.yd-tab {
  padding: var(--s2) var(--s4); border-radius: var(--r-md); border: 1px solid var(--border-soft);
  background: var(--bg-subtle); color: var(--text-secondary); font-size: var(--fs-base);
  cursor: pointer; transition: border-color 0.2s, color 0.2s;
}
.yd-tab:hover { border-color: var(--star); color: var(--warn-text); }
.yd-tab.active {
  background: rgba(255, 180, 0, 0.15); border-color: var(--star);
  color: var(--gold); font-weight: 600;
}

.yd-panel {
  background: var(--bg-subtle); border: 1px solid var(--border-soft);
  border-radius: var(--r-lg); padding: var(--s4);
}

.yd-toolbar { display: flex; align-items: center; gap: var(--s2); margin-bottom: var(--s2); flex-wrap: wrap; }
.yd-tip { color: var(--text-muted); font-size: var(--fs-xs); flex: 1; min-width: 0; line-height: 1.5; }
.yd-badge {
  display: inline-block; padding: 2px var(--s2); border-radius: var(--r-lg);
  background: rgba(255, 180, 0, 0.12); color: var(--star);
  font-size: var(--fs-xs); font-weight: 600;
}

/* 折叠区：交易所已公布异动 */
.yd-fold { margin-top: var(--s4); border-top: 1px dashed var(--border-soft); padding-top: var(--s2); }
.yd-fold-h {
  background: none; border: none; padding: var(--s1) 0; cursor: pointer;
  color: var(--text-secondary); font-size: var(--fs-sm);
}
.yd-fold-h:hover { color: var(--gold); }
.yd-fold-h .fa { margin-right: var(--s2); color: var(--star); }
.yd-fold-b { margin-top: var(--s2); }
.yd-fold-b .stock-table { width: 100%; }

/* 异动计算器 */
.cal-bar { display: flex; gap: var(--s2); align-items: center; margin-bottom: var(--s4); flex-wrap: wrap; }
.cal-input {
  width: 220px; padding: var(--s2) var(--s3); border-radius: var(--r-md);
  border: 1px solid var(--border-soft); background: var(--bg-main, #1a1a1a);
  color: var(--text-main); font-size: var(--fs-base); font-family: inherit;
  letter-spacing: 1px; outline: none;
}
.cal-input:focus { border-color: var(--star); }
.cal-input::placeholder { color: var(--text-muted); letter-spacing: 0; }

/* 🔎 计算器搜索框的"索引"下拉（2026-10-01）—— 与顶栏搜索同一后端索引，块级展开（不做浮层，
   避免手机端定位/遮挡问题；列表最多 20 条，展开后页面自然下推） */
.cal-sug {
  margin: -var(--s2) 0 var(--s4);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-md);
  background: var(--bg-card);
  overflow: hidden;
  max-width: 520px;
}
.cal-sug-i {
  display: flex; align-items: baseline; gap: var(--s2); width: 100%; flex-wrap: nowrap;
  padding: var(--s2) var(--s2); background: transparent; border: none;
  border-top: 1px solid var(--border-soft);
  color: var(--text-secondary); font-size: var(--fs-sm); text-align: left; cursor: pointer;
}
.cal-sug-i:first-child { border-top: none; }
.cal-sug-i.on { background: var(--bg-subtle); }
.cal-sug-name { font-weight: 600; color: var(--text-main); }
.cal-sug-code { font-size: var(--fs-xs); color: var(--text-dim); }
.cal-sug-board {
  margin-left: auto; flex: 0 1 auto; max-width: 46%;
  font-size: var(--fs-xs); color: var(--star);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.cal-sug-empty { padding: var(--s2) var(--s2); font-size: var(--fs-xs); color: var(--text-muted); }
.cal-btn {
  padding: var(--s2) var(--s4); border-radius: var(--r-md); cursor: pointer; font-size: var(--fs-base);
  border: 1px solid var(--star); background: rgba(255, 180, 0, 0.15); color: var(--gold);
  font-weight: 600;
}
.cal-btn:disabled { opacity: 0.6; cursor: default; }
.cal-btn-ghost { background: none; border-color: var(--border-soft); color: var(--text-muted); font-weight: 400; }

.desc-col { max-width: 300px; }
.concept-col { max-width: 240px; color: #9cf; }
.type-col { max-width: 200px; color: var(--gold); white-space: normal; word-break: break-word; overflow-wrap: anywhere; }
.trigger-col { max-width: 180px; color: var(--text-muted); font-size: var(--fs-xs); white-space: normal; word-break: break-word; overflow-wrap: anywhere; }
.dev-col { text-align: right; white-space: nowrap; }
.dev-col .dev-num { color: var(--accent); font-weight: 600; font-size: var(--fs-sm); }
.dev-col .dev-days { color: var(--text-muted); font-size: var(--fs-xs); margin-left: var(--s1); }

.trigger-status {
  display: inline-block; padding: 2px var(--s2); border-radius: var(--r-sm);
  font-size: var(--fs-xs); font-weight: 500;
  color: var(--text-faint); background: rgba(255, 255, 255, 0.05); white-space: nowrap;
}
.trigger-status.triggered { color: var(--accent); background: rgba(255, 77, 79, 0.15); border: 1px solid rgba(255, 77, 79, 0.4); }

/* 表格单元格居中对齐 */
.yd-panel .stock-table th,
.yd-panel .stock-table td { vertical-align: middle !important; text-align: center !important; }

/* 合并列: 代码+名称 上下排布
   关键: td 必须是 table-cell + vertical-align:middle 才能自动撑满整行高度,
        不能设 display:flex(flex 高度由自身内容决定, 不会跟随行高, 导致偏上) */
.yd-panel .stock-table td.stock-info-cell {
  cursor: pointer; min-width: 80px !important; padding: var(--s2) var(--s1) !important;
  display: table-cell !important; vertical-align: middle !important; text-align: center !important;
}
.stock-info-cell .stock-name-row { display: block !important; line-height: 1.4 !important; text-align: center !important; }
.stock-info-cell .stock-name { font-weight: 600 !important; color: var(--text-main); font-size: var(--fs-sm) !important; }
.stock-info-cell .stock-code-row { display: block !important; line-height: 1.2 !important; text-align: center !important; margin-top: 2px !important; }
.stock-info-cell .stock-code {
  font-family: inherit; font-size: var(--fs-xs) !important; color: var(--text-muted); letter-spacing: 0.5px !important;
}
.stock-info-cell:hover .stock-name,
.stock-info-cell:hover .stock-code { color: var(--accent); }

.loading-placeholder { text-align: center; padding: var(--s8); color: var(--text-muted); }
.spinner {
  width: 28px; height: 28px; border: 3px solid rgba(255, 180, 0, 0.3);
  border-top-color: var(--star); border-radius: 50%;
  animation: spin 0.8s linear infinite; margin: 0 auto 10px;
}
@keyframes spin { to { transform: rotate(360deg); } }
.empty-state { text-align: center; padding: var(--s8); color: var(--text-muted); }

.lb-badge {
  display: inline-block; color: var(--up); border: 1px solid rgba(255, 80, 40, 0.5);
  border-radius: var(--r-sm); padding: 0 var(--s1); font-size: var(--fs-xs);
  background: rgba(255, 80, 40, 0.12);
}

/* 浅色主题覆盖 */
body[data-bg="light"] .yd-title { color: #8a5500; }
body[data-bg="light"] .yd-title .fa { color: var(--gold-deep); }
body[data-bg="light"] .yd-sub { color: var(--text-faint); }
body[data-bg="light"] .yd-time { color: var(--text-faint); }
body[data-bg="light"] .yd-tab { color: var(--text-faint); border-color: var(--border-soft); background: rgba(255, 255, 255, 0.6); }
body[data-bg="light"] .yd-tab:hover { color: var(--watermark); border-color: var(--gold-deep); }
body[data-bg="light"] .yd-tab.active { color: var(--watermark); background: rgba(255, 180, 0, 0.15); border-color: var(--gold-deep); }
body[data-bg="light"] .yd-panel { background: rgba(255, 255, 255, 0.85); border-color: var(--border-soft); }
body[data-bg="light"] .lb-badge { color: var(--brand-deep); border-color: rgba(184, 48, 16, 0.5); background: rgba(255, 80, 80, 0.1); }
body[data-bg="light"] .yd-fold-h { color: var(--watermark); }
body[data-bg="light"] .yd-fold-h:hover { color: #8a5500; }
body[data-bg="light"] .cal-input { background: #fff; color: #3a2a00; }
body[data-bg="light"] .cal-btn { color: #8a5500; background: #fff8e6; border-color: var(--gold-deep); }
body[data-bg="light"] .cal-btn-ghost { background: none; color: var(--text-faint); border-color: var(--border-soft); }

/* 移动端适配 */
@media (max-width: 768px) {
  .yd-panel { overflow-x: auto; -webkit-overflow-scrolling: touch; padding: var(--s2) var(--s2); }
  .yd-panel .stock-table { min-width: 680px; }
  .yd-tabs { flex-wrap: nowrap; overflow-x: auto; -webkit-overflow-scrolling: touch; scrollbar-width: none; padding-bottom: var(--s1); }
  .yd-tabs::-webkit-scrollbar { display: none; }
  .yd-tab { flex-shrink: 0; white-space: nowrap; padding: var(--s2) var(--s3); font-size: var(--fs-sm); }
  .yd-head { gap: var(--s2); }
  .yd-title { font-size: var(--fs-lg); }
  .yd-sub { font-size: var(--fs-xs); width: 100%; }
  .yd-time { margin-left: 0; font-size: var(--fs-xs); }
  .yd-panel .stock-table th { padding: var(--s2) var(--s1); font-size: var(--fs-xs); }
  .yd-panel .stock-table td { padding: var(--s2) var(--s1); font-size: var(--fs-xs); }
  .desc-col { max-width: 180px; }
  .cal-input { width: 100%; }
  .cal-bar { gap: var(--s2); }
}
</style>
