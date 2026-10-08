<template>
  <!--
    全局股票快速搜索（2026-09-27 v4.11.63《快选股移动端追加清单》§三「🔍 跳股」）
    ====================================================================
    清单原文：「现在搜索只在管理后台/历史/股性页里有，用户在行情里想直接看某只票
    找不到入口。输入代码/拼音首字母/名字，下拉匹配，点进去看详情。优先级高。」

    两个挂载位（清单写「顶部导航**或**底部 tabbar 旁」，本版两处都挂，各有分工）：
      · variant="nav"    → NavBar 右侧工具区（桌面常显输入框；≤768px 由 CSS 隐藏）
      · variant="tabbar" → AppTabBar 第 7 格（手机专属；桌面整条 tabbar 都是 display:none）
    为什么手机要单独一个入口：**.nav-bar 不是 sticky**（main.css 里无 sticky 声明），
    页面往下滚一屏就够不着顶部输入框了 —— 而「看盘中想直接看某只票」恰恰发生在滚到表格
    中段的时候。底部 tabbar 是 fixed 的，天然永远够得着。

    ★ 点结果看详情 = `openStockChart(code, name)`（composables/uiBus）。
      这是全站既有通道：App.vue 持有 StockChartModal，任意表格点股票单元格走的也是它，
      分时/日K/周K/月K 四个 tab 齐备。**不新造详情页**，也不走 `linkToSoftware`
      （那是唤起通达信客户端，不是"看详情"）。

    ★ 搜索请求落到后端 `/api/stocks/search`（services/stock_search.py）：
      清单写"纯前端零后端改动"，但本仓没有任何全市场名录接口，且拼音首字母要 GBK
      编码器（前端没有）⇒ 必须落到后端。数据全程读本地 SQLite，零网络、零配额消耗。
  -->
  <div ref="rootRef" class="ss-root" :class="'ss-root--' + variant" data-no-chart>
    <!-- 桌面内联输入框 -->
    <div v-if="variant === 'nav'" class="ss-inline" :class="{ 'is-open': open }">
      <i class="fa fa-search ss-inline-icon"></i>
      <input
        ref="inputRef"
        v-model="kw"
        class="ss-inline-input"
        type="search"
        placeholder="搜索代码 / 名称 / 拼音"
        autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false"
        aria-label="搜索股票代码、名称或拼音首字母"
        :aria-expanded="open ? 'true' : 'false'"
        @focus="openPanel"
      />
      <button v-if="kw" class="ss-inline-clear" type="button" aria-label="清空" @click="reset">
        <i class="fa fa-times-circle"></i>
      </button>
    </div>

    <!-- 手机底部 tabbar 第 7 格 -->
    <button
      v-else
      class="ss-tab"
      :class="{ active: open }"
      type="button"
      aria-label="搜索股票"
      :aria-expanded="open ? 'true' : 'false'"
      @click="toggleTab"
    >
      <i class="fa fa-search ss-tab-icon"></i>
      <span class="ss-tab-label">搜索</span>
    </button>

    <!-- 结果面板：Teleport 到 body —— 与 NavBar 用户菜单同款处理(2026-08-18 iOS Safari)：
         fixed 元素留在 .nav-tools / .app-tabbar 这类滚动容器里会被当容器内容裁剪。 -->
    <Teleport to="body">
      <StockSearchPanel
        v-if="open"
        ref="panelRef"
        :style="panelStyle"
        :variant="variant"
        :kw="kw"
        :rows="rows"
        :phase="phase"
        :err-msg="errMsg"
        :active-idx="activeIdx"
        @input="kw = $event"
        @pick="pick"
        @retry="run"
        @close="closePanel"
        @keydown="onKey"
      />
    </Teleport>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { stocksSearch } from '../api/stocks'
import { openStockChart } from '../composables/uiBus'
import StockSearchPanel from './StockSearchPanel.vue'

const props = defineProps({
  variant: {
    type: String,
    default: 'nav',
    validator: (v) => v === 'nav' || v === 'tabbar',
  },
})

const DEBOUNCE_MS = 300     // 与清单「输代码/拼音首字母/名字，下拉匹配」的手感对齐

const rootRef = ref(null)
const inputRef = ref(null)      // 仅 nav 变体（tabbar 变体的输入框在面板里）
const panelRef = ref(null)

const kw = ref('')
const open = ref(false)
const rows = ref([])
// idle | loading | ok | empty | err —— 五态互斥，面板据此渲染（见 StockSearchPanel 注释）
const phase = ref('idle')
const errMsg = ref('')
const activeIdx = ref(-1)

const anchor = ref({ bottom: 0, left: 0, width: 320 })

/* ---------- 排版定位 ----------
   两条不同的板：
   · nav    → 挂在输入框正下方（桌面，无软键盘遮挡问题）；
   · tabbar → **从顶部弹出**而不是贴着 tabbar 往上长。原因：手机点开后立刻聚焦输入框，
              软键盘会吃掉屏幕下半部分；贴着底部的面板会被键盘压住，顶部弹出的面板
              和它的输入框始终留在可视区内。 */
const panelStyle = computed(() => {
  if (props.variant === 'tabbar') {
    return {
      position: 'fixed',
      top: 'calc(env(safe-area-inset-top, 0px) + 8px)',
      left: '8px',
      right: '8px',
      zIndex: 1600,
      maxHeight: '64vh',
    }
  }
  const a = anchor.value
  return {
    position: 'fixed',
    top: (a.bottom + 6) + 'px',
    left: a.left + 'px',
    width: Math.max(280, a.width) + 'px',
    zIndex: 1600,
    maxHeight: '60vh',
  }
})

function captureAnchor() {
  const el = rootRef.value
  if (!el) return
  const r = el.getBoundingClientRect()
  anchor.value = { bottom: r.bottom, left: r.left, width: r.width }
}

/* ---------- 开关 ---------- */
function openPanel() {
  if (props.variant === 'nav') captureAnchor()
  open.value = true
  if (props.variant === 'tabbar') {
    nextTick(() => { panelRef.value && panelRef.value.focus() })
  } else {
    nextTick(() => { inputRef.value && inputRef.value.focus() })
  }
}
function closePanel() {
  open.value = false
  activeIdx.value = -1
  if (props.variant === 'nav' && inputRef.value) inputRef.value.blur()
}
function toggleTab() {
  if (open.value) closePanel()
  else openPanel()
}
function reset() {
  kw.value = ''
  rows.value = []
  phase.value = 'idle'
  errMsg.value = ''
  activeIdx.value = -1
  if (inputRef.value) inputRef.value.focus()
}

/* ---------- 搜索（300ms 防抖 + 过期响应丢弃） ---------- */
let _timer = null
let _seq = 0

watch(kw, () => {
  clearTimeout(_timer)
  const q = kw.value.trim()
  if (!q) { rows.value = []; phase.value = 'idle'; activeIdx.value = -1; return }
  // 已有结果时不清空旧列表，只标记 loading —— 否则每敲一个字列表就闪一下空
  phase.value = 'loading'
  _timer = setTimeout(run, DEBOUNCE_MS)
})

async function run() {
  const q = kw.value.trim()
  if (!q) return
  const my = ++_seq
  phase.value = 'loading'
  try {
    const d = await stocksSearch(q, 20)
    if (my !== _seq) return                    // 已被更晚的请求取代 ⇒ 丢弃
    const list = (d && d.list) || []
    rows.value = list
    activeIdx.value = list.length ? 0 : -1
    // ★ 空结果与失败必须分开：空 = 确实没这只票；失败 = 数据源不可用。
    //   两者都渲染成"空列表"就是本项目最忌讳的"静默"。
    phase.value = list.length ? 'ok' : 'empty'
  } catch (e) {
    if (my !== _seq) return
    rows.value = []
    errMsg.value = (e && e.message) || '网络异常'
    phase.value = 'err'
  }
}

/* ---------- 选中 / 键盘 ---------- */
function pick(r) {
  if (!r || !r.code) return
  openStockChart(r.code, r.name)      // 全站既有通道：弹分时/K线
  closePanel()
}
function move(d) {
  const n = rows.value.length
  if (!n) return
  const cur = activeIdx.value
  activeIdx.value = ((cur < 0 ? (d > 0 ? -1 : 0) : cur) + d + n) % n
  nextTick(() => {
    // 2026-09-30 v4.11.84 (P2-5): 不再用「面板内全局 querySelector('.ss-row.is-active')」——
    //   面板复用/页面上另有高亮行时会定位到别的元素。改为按 activeIdx 取子组件暴露的行节点。
    const els = (panelRef.value && panelRef.value.rowEls) || []
    const row = els[activeIdx.value]
    // 面板内的 ul 才是滚动容器，block:'nearest' 只滚它，不动整页
    if (row && row.scrollIntoView) row.scrollIntoView({ block: 'nearest' })
  })
}
function onKey(e) {
  if (e.key === 'Escape') { e.preventDefault(); closePanel(); return }
  if (e.key === 'ArrowDown') { e.preventDefault(); move(1); return }
  if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); return }
  if (e.key === 'Enter') {
    e.preventDefault()
    const r = rows.value[activeIdx.value] || rows.value[0]
    if (r) pick(r)
  }
}

/* ---------- 点外部 / 页面滚动 ---------- */
function inPanel(t) {
  const el = panelRef.value && panelRef.value.$el
  return !!(el && t && el.contains(t))
}
function onDocClick(e) {
  if (!open.value) return
  const inRoot = rootRef.value && rootRef.value.contains(e.target)
  if (!inRoot && !inPanel(e.target)) closePanel()
}
function onDocScroll(e) {
  // 手机 tabbar 变体的面板是 fixed 的，页面滚动不影响它 ⇒ 不关；
  // nav 变体的面板锚在导航栏下方，滚动后锚点就跑掉了 ⇒ 关（与 NavBar 用户菜单同策略）。
  if (!open.value || props.variant !== 'nav') return
  if (inPanel(e.target)) return          // 结果列表自己滚动不算"页面滚动"
  closePanel()
}
function onResize() {
  if (open.value && props.variant === 'nav') captureAnchor()
}

onMounted(() => {
  document.addEventListener('click', onDocClick)
  document.addEventListener('scroll', onDocScroll, true)
  window.addEventListener('resize', onResize, { passive: true })
})
onBeforeUnmount(() => {
  clearTimeout(_timer)
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('scroll', onDocScroll, true)
  window.removeEventListener('resize', onResize)
})
</script>

<style scoped>
/* ============ 桌面内联输入框（NavBar 内） ============ */
.ss-inline {
  display: flex; align-items: center; gap: var(--s1);
  background: var(--bg-input);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-md);
  padding: 0 var(--s2);
  transition: border-color 0.15s;
}
.ss-inline.is-open { border-color: var(--accent); }
.ss-inline-icon { color: var(--text-muted); font-size: var(--fs-sm); }
.ss-inline-input {
  width: 150px;                 /* 桌面常驻但不喧宾夺主；聚焦时展开 */
  background: transparent; border: none; outline: none;
  color: var(--text-main);
  font-size: var(--fs-sm);
  padding: var(--s1) 0;
  -webkit-appearance: none; appearance: none;
}
.ss-inline-input:focus { width: 200px; }
.ss-inline-input::-webkit-search-cancel-button { display: none; }
.ss-inline-input::placeholder { color: var(--text-muted); }
.ss-inline-clear {
  background: transparent; border: none; cursor: pointer;
  color: var(--text-muted); padding: 0; font-size: var(--fs-sm);
}
.ss-inline-clear:hover { color: var(--accent); }
/* 2026-10-01: 手机端**不再隐藏**顶部输入框。
   原因：主人拍板底部 tab 收为 4 格（首页/竞价/盘中/我的）并去掉第 7 格搜索，
   原注释「改由底部 tabbar 第 7 格承担」的前提已不存在 ⇒ 顶栏搜索必须常驻，
   否则手机端没有搜索入口。窄屏下输入框由 NavBar 的 flex 自适应收窄。 */

/* ============ 手机底部 tabbar 第 7 格 ============ */
/* 尺寸/配色与 AppTabBar 的 .tabbar-item 对齐（56px 高、图标 18px、文字 10px、选中 accent） */
.ss-root--tabbar {
  flex: 1 1 0;
  min-width: 0;
  display: flex;
}
.ss-tab {
  flex: 1 1 0;
  min-width: 0;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 2px;
  height: 56px;
  background: transparent; border: none;
  color: var(--text-muted);
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  transition: color 0.15s, transform 0.15s;
}
.ss-tab-icon { font-size: var(--fs-xl); line-height: 1; transition: transform 0.15s; }
.ss-tab-label {
  font-size: var(--fs-xs); line-height: 1; font-weight: 400;
  white-space: nowrap; max-width: 100%;
  overflow: hidden; text-overflow: ellipsis;
}
.ss-tab.active { color: var(--accent); }
.ss-tab.active .ss-tab-icon { transform: scale(1.1); }
.ss-tab.active .ss-tab-label { font-weight: 600; }
.ss-tab:active { transform: scale(0.92); }
</style>
