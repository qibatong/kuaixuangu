<!--
  《顺势而为竞价终极版》前端（2026-10-03）
  🔴 版式与交互**照原件复刻**（docs/reference/his-pick-竞价终极版.html），
     数据全部来自**我们后端** /api/his-pick（他的选股逻辑已在后端，逐行等价，见 docs/后端移植_交付核对_20261003.md）。
  2026-10-03 二次校正：样式**逐条对齐原件的 CSS**（勋章区金色外框面板、emoji 🥇🥈🥉 28px、字号 22/18/48px、
     表格 thead 红底 + min-width 1100px + 居中 + #ffbcbc/#ff3a3a/#ffd700 配色、股票池行式布局、按钮药丸样式等）。
  除样式外，只做了主人明确要求的改动：去掉大标题行、概念换开盘啦（后端）、表头与内容居中。
-->
<template>
  <div class="hp-root">
    <!-- ① 标题行：2026-10-03 主人指令去掉（顶部已有页面标题）-->

    <!-- ② 规则条：2026-10-03 主人指令**整条删除**（原「9:30前可重新选股 · 9:30后仅更新实时涨幅」）
         —— 该行信息与「刷新实时涨幅」按钮、页脚等处重复，主人要求去掉。 -->

    <!-- ③ 筛选条（原件 8 项 + 应用/重置/锁定） -->
    <div class="filter-custom">
      <div class="fh-row">
        <label><input v-model="form.stSuspend" type="checkbox" :disabled="locked"> 剔除ST/停牌</label>
        <span class="filter-divider">|</span>
        <span class="fh-label">市场范围：</span>
        <label><input v-model="form.markets" type="checkbox" value="hs" :disabled="locked"> 沪深A股主板</label>
        <label><input v-model="form.markets" type="checkbox" value="cyb" :disabled="locked"> 创业板</label>
        <label><input v-model="form.markets" type="checkbox" value="kcb" :disabled="locked"> 科创板</label>
        <span class="filter-divider">|</span>
        <label><input v-model="form.limitUp" type="checkbox" :disabled="locked"> 剔除昨日涨停</label>
      </div>
      <div class="fh-row">
        <label>竞价涨幅 &gt; <input v-model.number="form.bidGt" type="number" min="0" max="20" step="0.5" :disabled="locked">%</label>
        <span class="filter-divider">|</span>
        <label>涨停率 &lt; <input v-model.number="form.probLt" type="number" min="5" max="95" step="1" :disabled="locked">%
          且可信度 &lt; <input v-model.number="form.confLt" type="number" min="50" max="90" step="1" :disabled="locked">%</label>
        <span class="filter-divider">|</span>
        <label>流通市值 &gt; <input v-model.number="form.floatMvGt" type="number" min="1" max="5000" step="1" :disabled="locked">亿</label>
        <label>股价 &gt; <input v-model.number="form.priceGt" type="number" min="1" max="5000" step="1" :disabled="locked">元</label>
        <button class="tdx-export-btn apply-btn" :disabled="locked || loading" @click="refresh(false)">应用筛选</button>
        <button class="tdx-export-btn real-time-btn" :disabled="loading" @click="refresh(true)">
          <i class="fa fa-refresh"></i> 刷新实时涨幅
        </button>
        <button class="tdx-export-btn reset-filter-btn" title="重置为默认条件" @click="resetFilter"><i class="fa fa-undo"></i> 重置</button>
        <button class="tdx-export-btn lock-filter-btn" :class="{ locked }" title="锁定/解锁筛选条件" @click="toggleLock">
          {{ locked ? '🔓 解锁' : '🔒 锁定' }}
        </button>
        <span v-if="locked" class="lock-indicator">🔒 筛选已锁定</span>
      </div>
    </div>

    <!-- ④ 勋章卡 Top3（原件：🥇🥈🥉 + 金牌/银牌/铜牌） -->
    <div class="medal-section">
      <div v-if="loading && !list.length" class="loading-placeholder">加载中...</div>
      <div v-else-if="!list.length" class="empty-state">暂无符合条件股票</div>
      <template v-else>
        <div v-for="(it, i) in medals" :key="it.code" class="medal-card">
          <div class="medal-rank"><span class="medal-rank-icon">{{ ['🥇', '🥈', '🥉'][i] }}</span> {{ ['金牌', '银牌', '铜牌'][i] }}</div>
          <div class="medal-name-big">{{ it.name }}</div>
          <div class="medal-code" @click="linkToSoftware(it.code, it.name)">{{ it.code }}</div>
          <div class="medal-prob-big">{{ it.probability }}%</div>
          <div class="medal-detail">
            <span class="bid-chg">竞涨幅{{ fmt(it.bidChange) }}%</span>
            <span class="conf-val">可信{{ it.confidence }}%</span>
          </div>
          <div class="medal-real-chg" :class="{ 'green-real': isGreen(it) }">实时涨幅{{ fmt(it.realChange) }}%</div>
        </div>
      </template>
    </div>

    <!-- ⑤ 导出前 N 只 -->
    <div class="hp-right-bar">
      <div class="export-medal-group">
        <span class="hp-export-hint">导出前</span>
        <select v-model.number="exportCount" class="export-select">
          <option :value="3">3只</option><option :value="5">5只</option>
          <option :value="8">8只</option><option :value="10">10只</option>
        </select>
        <button class="tdx-export-btn" @click="exportStocks(exportCount)"><i class="fa fa-download"></i> 导出至通达信</button>
      </div>
    </div>

    <!-- ⑥ 策略股票池（原件：行式布局 + 10 小时锁定） -->
    <div class="stock-pool-panel">
      <div class="pool-header">
        <div class="pool-title"><i class="fa fa-database"></i> 策略股票池
          <span class="auto-tag">{{ before930 ? '⏳ 9:30前自动选股' : '✅ 已锁定' }}</span>
          <span v-if="poolExpiryText" class="pool-expiry-info">{{ poolExpiryText }}</span>
        </div>
        <div class="pool-buttons">
          <button class="pool-btn" @click="addCurrentTop3"><i class="fa fa-plus-circle"></i> 加入当前前三</button>
          <button class="pool-btn" @click="clearPool"><i class="fa fa-trash-o"></i> 清空股票池</button>
          <button class="pool-btn" @click="exportPool"><i class="fa fa-share-square-o"></i> 导出股票池</button>
        </div>
      </div>
      <div class="pool-list">
        <div v-if="!stockPool.length" class="empty-pool">暂无股票，9:30前系统自动将前三名选入池中</div>
        <div v-for="p in stockPool" :key="p.code" class="pool-item">
          <div class="pool-item-info">
            <span class="pool-stock-name">{{ p.name }}</span>
            <span class="pool-stock-code">{{ p.code }}</span>
            <span class="pool-add-time">{{ p.timeText }}</span>
          </div>
          <button class="del-single" @click="removePool(p.code)">删除</button>
        </div>
      </div>
    </div>

    <!-- ⑦ 明细表（原件 11 列，表头与内容居中） -->
    <div class="hp-right-bar">
      <button class="tdx-export-btn" @click="exportAll"><i class="fa fa-download"></i> 导出全部筛选结果至通达信</button>
    </div>
    <div class="stock-table-container">
      <div v-if="loading && !list.length" class="loading-placeholder">正在初始化选股数据...</div>
      <div v-else-if="!list.length" class="empty-state">暂无符合条件股票</div>
      <table v-else class="stock-table">
        <thead>
          <tr>
            <!-- 2026-10-05 主人指令（方案 A）：① 删「行业」列；② 「综合评分」「可信度」移到「概念」左侧；
                 ③ 股票代码挪到股票名称**下方**（照竞价优选 / AI 精选报告的写法）。
                 列数 11 → 9，表格 min-width 随之从 1100 收到 980（见样式区）。 -->
            <th>排名</th>
            <th class="sortable" :class="{ active: sort.keyOf('name') }" :aria-sort="ariaSort('name')" tabindex="0"
                @click="sort.onSort('name', 'string')" @keydown.enter.prevent="sort.onSort('name', 'string')" @keydown.space.prevent="sort.onSort('name', 'string')">名称<span class="sort-ind" aria-hidden="true">{{ sort.ind('name') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('bidChange') }" :aria-sort="ariaSort('bidChange')" tabindex="0"
                @click="sort.onSort('bidChange')" @keydown.enter.prevent="sort.onSort('bidChange')" @keydown.space.prevent="sort.onSort('bidChange')">竞价涨幅<span class="sort-ind" aria-hidden="true">{{ sort.ind('bidChange') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('realChange') }" :aria-sort="ariaSort('realChange')" tabindex="0"
                @click="sort.onSort('realChange')" @keydown.enter.prevent="sort.onSort('realChange')" @keydown.space.prevent="sort.onSort('realChange')">实时涨幅<span class="sort-ind" aria-hidden="true">{{ sort.ind('realChange') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('entityChange') }" :aria-sort="ariaSort('entityChange')" tabindex="0"
                @click="sort.onSort('entityChange')" @keydown.enter.prevent="sort.onSort('entityChange')" @keydown.space.prevent="sort.onSort('entityChange')">实体涨幅<span class="sort-ind" aria-hidden="true">{{ sort.ind('entityChange') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('warnType') }" :aria-sort="ariaSort('warnType')" tabindex="0"
                @click="sort.onSort('warnType')" @keydown.enter.prevent="sort.onSort('warnType')" @keydown.space.prevent="sort.onSort('warnType')">异动<span class="sort-ind" aria-hidden="true">{{ sort.ind('warnType') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('probability') }" :aria-sort="ariaSort('probability')" tabindex="0"
                @click="sort.onSort('probability')" @keydown.enter.prevent="sort.onSort('probability')" @keydown.space.prevent="sort.onSort('probability')">综合评分<span class="sort-ind" aria-hidden="true">{{ sort.ind('probability') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('confidence') }" :aria-sort="ariaSort('confidence')" tabindex="0"
                @click="sort.onSort('confidence')" @keydown.enter.prevent="sort.onSort('confidence')" @keydown.space.prevent="sort.onSort('confidence')">可信度<span class="sort-ind" aria-hidden="true">{{ sort.ind('confidence') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('concept') }" :aria-sort="ariaSort('concept')" tabindex="0"
                @click="sort.onSort('concept', 'string')" @keydown.enter.prevent="sort.onSort('concept', 'string')" @keydown.space.prevent="sort.onSort('concept', 'string')">概念<span class="sort-ind" aria-hidden="true">{{ sort.ind('concept') }}</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(it, idx) in sortedList" :key="it.code">
            <td class="rank-col">{{ idx + 1 }}</td>
            <!-- 名称 + 代码两行，容器挂 .name-col + data-stock-* —— 与 YijinerView.vue:116 /
                 AipickReport.vue:106 同构（其它页面就是这么写的，主人要求「参考其他的页面」）。
                 ⚠️ 行为变化要说清：原来只有「代码」格（td.code-click）能被 App.vue 的全局代理
                 命中弹分时，现在**整格（名称+代码）**都能弹。这不是新增功能，是把原来只覆盖
                 代码格的既有行为扩到整格；若主人只要代码可点，说一声即可收窄。 -->
            <td class="name-col" :data-stock-code="it.code" :data-stock-name="it.name">
              <div class="name-main">{{ it.name }}</div>
              <div class="name-sub code-click" @click="linkToSoftware(it.code, it.name)">{{ it.code }}</div>
            </td>
            <td :class="cls(it.bidChange)">{{ fmt(it.bidChange) }}%</td>
            <td :class="isGreen(it) ? 'real-green' : cls(it.realChange)">{{ fmt(it.realChange) }}%</td>
            <td :class="cls(it.entityChange)">{{ fmt(it.entityChange) }}%</td>
            <td>{{ warnText(it.warnType) }}</td>
            <td class="prob-col">{{ it.probability }}%</td>
            <td>{{ it.confidence }}%</td>
            <td class="concept-col">{{ it.concept }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <!-- 2026-10-05 (M5): 此处原有**与上方工具条完全重复**的第二个「导出全部筛选结果至通达信」
         （同文案/同图标/同 @click=exportAll，实测同屏出现两次 ⇒ 用户不知道点哪个，也显得页面没做完）。
         保留表格上方工具条位置的那个，理由：它在表头之上、长列表滚动时仍能被第一眼看到。 -->

  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { hisPick } from '../api/hisPick'
import { linkToSoftware } from '../utils/tdx'
// 2026-10-05 主人要求「点表头排序」：复用站内通用排序 composable（与竞价优选 / 动态选股同源，
// 行为一致：点击 无 → 降序(数值列)/升序(字符串列) → 反向 → 无；空值恒排末尾）
import { useSortable } from '../composables/useSortable'

const DEFAULTS = { stSuspend: true, markets: ['hs', 'cyb', 'kcb'], limitUp: true,
                   bidGt: 7, probLt: 65, confLt: 65, floatMvGt: 100, priceGt: 30 }
const LOCK_KEY = 'his_pick_filter_lock_v1'
const POOL_KEY = 'shunshi_stock_pool_v2'          // 与原件同一个 key
const POOL_LOCK_MS = 10 * 60 * 60 * 1000          // 原件：10 小时防刷新锁定

const form = reactive({ ...DEFAULTS, markets: [...DEFAULTS.markets] })
const locked = ref(false)
const loading = ref(false)
const list = ref([])
// 2026-10-05 主人：竞价选股没有排序 ⇒ 表头可点。
// 🔴 排序**只影响表格**：金牌/银牌卡（list.slice(0,3)）与「导出全部/前 N」仍用原 list ——
//    那两个是"本策略的名次"，不该因为用户按某列排了个序就被改写。
const sort = useSortable()
const sortedList = computed(() => sort.sorted(list.value))

/** 无障碍：排序指示符（↑/↓）→ aria-sort 值（表头已加 tabindex/Enter/Space 键盘可点） */
function ariaSort(k) {
  const i = sort.ind(k)
  return i === ' ↓' ? 'descending' : i === ' ↑' ? 'ascending' : 'none'
}
const medals = ref([])
const date = ref('')
const fetchedAt = ref('')
const tick = ref(0)                               // 不显示；只用于定期刷新下面的时间类 computed
const exportCount = ref(3)
const stockPool = ref([])
const poolLockAt = ref(0)

// ---------- 时间（北京时间）----------
function bj() {
  const now = new Date()
  return new Date(now.getTime() + 8 * 3600 * 1000 + now.getTimezoneOffset() * 60 * 1000)
}
const before930 = computed(() => {
  void tick.value                                     // 依赖 tick ⇒ 随时间自动重算（时钟已不显示）
  const t = bj()
  return t.getHours() < 9 || (t.getHours() === 9 && t.getMinutes() < 30)
})
const poolExpiryText = computed(() => {
  void tick.value
  if (!poolLockAt.value) return ''
  const left = poolLockAt.value + POOL_LOCK_MS - Date.now()
  if (left <= 0) return ''
  return `🔒 锁定中 ${Math.floor(left / 3600000)}h${Math.floor((left % 3600000) / 60000)}m`
})

// ---------- 展示工具（照原件）----------
function fmt(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '0.00'
  return (n > 0 ? '+' : '') + n.toFixed(2)
}
function cls(v) { return Number(v) > 0 ? 'up' : 'down' }
function isGreen(it) { return Number(it.realChange) < Number(it.bidChange) }
function warnText(w) {
  const n = Number(w)
  return n === 5 ? '🔥强' : n === 4 ? '⚡中' : n === 3 ? '↑弱' : '-'
}
function timeText(ms) {
  if (!ms) return ''
  const d = new Date(ms)
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ` +
         `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')} 入选`
}

// ---------- 取数 ----------
async function fetchData(force = false) {
  loading.value = true
  try {
    const q = {
      markets: (form.markets || []).join(','),
      bidGt: form.bidGt, probLt: form.probLt, confLt: form.confLt,
      floatMvGt: form.floatMvGt, priceGt: form.priceGt,
      stSuspend: form.stSuspend ? 1 : 0, limitUp: form.limitUp ? 1 : 0,
    }
    if (force) q.force = 1
    const r = await hisPick(q)
    const d = r && r.data ? r.data : r
    if (!d || !d.ok) return
    list.value = d.list || []
    medals.value = d.medals || (d.list || []).slice(0, 3)
    date.value = d.date || ''
    fetchedAt.value = d.fetchedAt || ''
    autoAddTop3()
  } catch (e) {
    /* 静默：保留上一次结果 */
  } finally {
    loading.value = false
  }
}
function refresh(force) { fetchData(!!force) }

function resetFilter() {
  Object.assign(form, { ...DEFAULTS, markets: [...DEFAULTS.markets] })
  fetchData(false)
}

// ---------- 筛选锁定（照原件）----------
function toggleLock() {
  locked.value = !locked.value
  try {
    if (locked.value) localStorage.setItem(LOCK_KEY, JSON.stringify({ ...form }))
    else localStorage.removeItem(LOCK_KEY)
  } catch (e) { /* 隐私模式忽略 */ }
}

// ---------- 股票池（照原件：9:30 前自动收前 3；10 小时锁定）----------
function savePool() {
  try {
    localStorage.setItem(POOL_KEY, JSON.stringify({ list: stockPool.value, lockAt: poolLockAt.value }))
  } catch (e) { /* ignore */ }
}
function autoAddTop3() {
  if (!before930.value || !list.value.length) return
  if (poolLockAt.value && Date.now() - poolLockAt.value < POOL_LOCK_MS) return
  stockPool.value = list.value.slice(0, 3).map(x => ({ code: x.code, name: x.name, at: Date.now(),
                                                       timeText: timeText(Date.now()) }))
  poolLockAt.value = Date.now()
  savePool()
}
function addCurrentTop3() {
  if (!list.value.length) return
  const now = Date.now()
  stockPool.value = list.value.slice(0, 3).map(x => ({ code: x.code, name: x.name, at: now,
                                                       timeText: timeText(now) }))
  poolLockAt.value = now
  savePool()
}
function removePool(code) {
  stockPool.value = stockPool.value.filter(x => x.code !== code)
  savePool()
}
function clearPool() { stockPool.value = []; poolLockAt.value = 0; savePool() }
function exportPool() { exportByList(stockPool.value) }

// ---------- 导出 / 联动通达信（照原件实现）----------
function exportByList(arr) {
  if (!arr.length) return
  let imp = ''
  arr.forEach(it => {
    const c = String(it.code)
    const m = c.startsWith('6') ? '1' : (c.startsWith('4') || c.startsWith('8')) ? '2' : '0'
    imp += `${m}#${c}|`
  })
  window.location.href = `http://www.treeid/AddToBlock_${imp.slice(0, -1)}`
}
function exportStocks(n) { exportByList(list.value.slice(0, n)) }
function exportAll() { exportByList(list.value) }
// 🔴 2026-10-06 主人指令：点个股一律看详情，不跳通达信。
//    本文件原先**自己实现了一套跳转**（`document.createElement('iframe')` + `treeid` 协议）——
//    它绕过了 utils/tdx.js 的平台判断，**连安卓壳里也会跳**，而 iframe 触发的跳转
//    连 App.vue 全局委托的 `stopPropagation()` 都拦不住（iframe 是自己创建并 append 的）。
//    ⇒ 删除本地实现，改用模块统一的 linkToSoftware（= 打开个股详情/分时）。
//    ⚠️ 勿再在此处新增任何 treeid / 通达信协议跳转。

let timer = null
onMounted(() => {
  try {
    const lk = localStorage.getItem(LOCK_KEY)
    if (lk) { Object.assign(form, JSON.parse(lk)); locked.value = true }
    const p = localStorage.getItem(POOL_KEY)
    if (p) {
      const o = JSON.parse(p)
      stockPool.value = (o.list || []).map(x => ({ ...x, timeText: x.timeText || timeText(x.at) }))
      poolLockAt.value = o.lockAt || 0
    }
  } catch (e) { /* ignore */ }
  fetchData(false)
  // 时钟已去掉；改用 30s 轻量 tick 驱动 before930 / 锁定倒计时（开销可忽略）
  timer = setInterval(() => { tick.value++ }, 30000)
})
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<style scoped>
/* =========================================================================
   配色与尺寸**逐条对齐原件 CSS**（docs/reference/his-pick-竞价终极版.html）
   仅有两处主人明确要求的差异：① 去掉大标题行 ② 表头/内容居中（原件亦为居中）

   🔴 2026-10-04 修复「白底(浅色)主题下竞价选股显示异常」：
      本块是**逐条照抄原件(纯深色主题)的硬编码色**，切到白色背景后整块变成
      「卡片仍是深底 + 字是浅色/金色的深色主题配色」⇒ 边框不可见、文字发灰、勋章区一大块深色。
      修法 = **换成语义 token**（--bg-body / --bg-panel / --bg-card / --text-main /
      --accent-text / --gold …）—— 它们在 `body[data-bg="light"]` 下有另一套值，
      深浅两套主题**自动适配**；而不是再抄一份 light 覆盖块（那样每次改样式都要改两处、必漏一处）。
      ⚠️ 涨跌语义色(--up/--down/--up-strong)按 main.css 约定**不随主题变**，保持原样。
   ========================================================================= */
.hp-root {
  background: var(--bg-body);
  color: var(--text-main);
  border-radius: 8px;
  padding: 2px 6px 12px;
  line-height: 1.5;
}
.right-group { display: flex; align-items: center; gap: var(--s3); flex-wrap: wrap; }
.btn-group { display: flex; gap: var(--s2); flex-wrap: wrap; }
.tdx-export-btn {
  background: var(--accent-bg2); border: 1px solid var(--accent); padding: var(--s2) var(--s4); border-radius: var(--r-sm);
  color: var(--accent-text); font-weight: 700; font-size: var(--fs-xs); cursor: pointer; transition: all 0.2s;
  backdrop-filter: blur(4px); display: inline-flex; align-items: center; gap: var(--s1); white-space: nowrap;
}
.tdx-export-btn:hover:enabled { background: var(--accent-deep); color: var(--text-main); border-color: var(--accent-deep); transform: translateY(-1px); box-shadow: 0 5px 12px var(--accent-bg2); }
.tdx-export-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.real-time-btn { background: rgba(0, 200, 100, 0.2); border-color: var(--down); }
.real-time-btn:hover:enabled { background: var(--down); }
.reset-filter-btn { background: rgba(80, 140, 255, 0.2); border-color: #5a8aff; color: var(--text-main); }
/* 2026-10-05 (S3): 主操作按钮对比度修复。
   原: background:var(--accent-solid) + color:var(--accent-text) ⇒ 浅红字压亮红底
      深色 #ffbcbc on #ff5c5c = 1.90:1 / 浅色 #b91c1c on #c62828 = 1.31:1, 两套主题都不达 AA。
   现: 实心深红 + 纯白字 = 5.36:1(dark) / 5.36:1(light)，同时与旁边 3 个描边按钮拉开主次层级。 */
.apply-btn {
  background: var(--accent-deep2);
  border-color: var(--accent-deep2);
  color: #fff;
}
.apply-btn:hover:enabled { background: var(--accent-deep); border-color: var(--accent-deep); color: #fff; }
.lock-filter-btn { background: var(--bg-hover); border: 1px solid var(--border-soft); color: var(--text-secondary); }
.lock-filter-btn.locked { background: var(--warn-bg); border-color: var(--warn); color: var(--warn-text); animation: lockPulse 2s ease-in-out infinite; }
@keyframes lockPulse { 0%, 100% { box-shadow: 0 0 0 0 var(--warn-bg); } 50% { box-shadow: 0 0 0 8px rgba(255, 140, 40, 0); } }
.filter-custom {
  background: var(--bg-card); border: 1px solid var(--accent-border);
  border-radius: var(--r-md); padding: var(--s2) var(--s2); margin: var(--s2) 0; font-size: var(--fs-xs); color: var(--accent-text);
}
.fh-row { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s2); margin: var(--s1) 0; }
.fh-row label { display: inline-flex; align-items: center; gap: var(--s1); }
.fh-row input[type="number"] {
  width: 62px; background: var(--bg-input); border: 1px solid var(--border-soft);
  color: var(--text-main); border-radius: var(--r-sm); padding: 2px var(--s1); font-size: var(--fs-xs);
}
.fh-label { color: var(--accent-text); font-weight: 600; }
.filter-divider { color: var(--border-soft); }
.lock-indicator { color: var(--warn-text); font-size: var(--fs-xs); }
/* 勋章区（原件 .medal-section/.medal-card/…） */
.medal-section {
  background: var(--bg-panel); backdrop-filter: blur(8px); border-radius: var(--r-lg); padding: var(--s4);
  border: 1px solid var(--gold); margin: var(--s3) 0;
  display: flex; flex-wrap: wrap; gap: var(--s4); justify-content: space-around;
}
.medal-card {
  flex: 1 1 180px; min-width: 180px; text-align: center; padding: var(--s3) var(--s2); border-radius: var(--r-lg);
  background: var(--bg-card); border: 1px solid var(--gold);
  display: flex; flex-direction: column; align-items: center; gap: var(--s2);
}
.medal-rank { font-size: var(--fs-xl); font-weight: 700; color: var(--gold); display: flex; align-items: center; gap: var(--s2); }
.medal-rank-icon { font-size: var(--fs-3xl); }
.medal-name-big { font-size: var(--fs-2xl); font-weight: 700; color: var(--text-main); letter-spacing: 1px; margin: 2px 0; }
.medal-code {
  font-size: var(--fs-xl); font-weight: 600; color: var(--gold); cursor: pointer; font-family: var(--font-mono);
  letter-spacing: 1px; margin: 2px 0; transition: color 0.2s;
}
.medal-code:hover { color: var(--text-main); text-shadow: 0 0 8px rgba(255, 215, 0, 0.6); }
.medal-prob-big { font-size: 48px; font-weight: 700; color: var(--up); line-height: 1; margin: var(--s1) 0; }
.medal-detail { font-size: var(--fs-sm); color: var(--text-secondary); display: flex; gap: var(--s3); align-items: center; }
.medal-detail .bid-chg { color: var(--up); font-weight: 600; }
.medal-detail .conf-val { color: var(--accent-text); font-weight: 600; }
.medal-real-chg { font-size: var(--fs-base); font-weight: 700; color: var(--up-strong); margin-top: 2px; }
.medal-real-chg.green-real { color: var(--down) !important; }
.hp-right-bar { display: flex; justify-content: flex-end; margin: var(--s2) 0; }
.export-medal-group { display: flex; align-items: center; gap: var(--s2); background: var(--bg-card); padding: var(--s2) var(--s4); border-radius: var(--r-md); }
.hp-export-hint { color: var(--accent-text); font-size: var(--fs-xs); }
.export-select {
  background: var(--bg-input); border: 1px solid var(--accent); color: var(--text-main); padding: var(--s2) var(--s2);
  border-radius: var(--r-sm); font-size: var(--fs-xs); outline: none; cursor: pointer;
}
/* 股票池（原件 .stock-pool-panel/.pool-*） */
.stock-pool-panel {
  background: var(--bg-card); border: 1px dashed var(--accent-border);
  border-radius: var(--r-md); padding: var(--s3); margin: var(--s2) 0;
}
.pool-header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: var(--s2); margin-bottom: var(--s2); border-bottom: 1px dashed var(--accent-border); padding-bottom: var(--s2); }
.pool-title { font-size: var(--fs-md); font-weight: 700; color: var(--accent-text); display: flex; align-items: center; gap: var(--s2); }
/* 2026-10-05 (S2 附带): 原 `background: var(--warn-bg)` 是**半透明琥珀**, 而本标签就嵌在
   `.lock-filter-btn.locked`(同样是琥珀底)内部 ⇒ 琥珀叠琥珀, 实测深色 1.91:1 / 浅色 2.28:1 不可读。
   改为**不透明**底色, 两套主题各给一档, 彻底消除"叠色后不可预期"。 */
.auto-tag {
  font-size: var(--fs-xs); color: var(--warn-text); background: #3a2a12;
  padding: var(--s1) var(--s2); border-radius: var(--r-pill);
}
body[data-bg="light"] .auto-tag { background: #fdf3e6; color: var(--warn-text); }
.pool-expiry-info { font-size: var(--fs-xs); color: var(--gold-text); margin-left: var(--s2); background: var(--warn-bg); padding: var(--s1) var(--s2); border-radius: var(--r-pill); white-space: nowrap; display: inline-block; }
.pool-buttons { display: flex; gap: var(--s2); flex-wrap: wrap; }
.pool-btn {
  background: var(--bg-input); border: 1px solid var(--accent-border); padding: var(--s1) var(--s4);
  border-radius: var(--r-pill); font-size: var(--fs-xs); font-weight: 500; cursor: pointer; transition: 0.2s;
  color: var(--accent-text); display: inline-flex; align-items: center; gap: var(--s1);
}
.pool-btn:hover { background: var(--accent-solid); border-color: var(--accent); color: var(--text-main); transform: translateY(-1px); }
.pool-list { max-height: 240px; overflow-y: auto; margin-top: var(--s2); border-radius: var(--r-md); }
.pool-item {
  display: flex; align-items: center; justify-content: space-between; background: var(--bg-subtle);
  margin: var(--s2) 0; padding: var(--s2) var(--s3); border-radius: 16px; border-left: 3px solid var(--up);
}
.pool-item-info { display: flex; flex-direction: column; gap: 2px; }
.pool-stock-name { font-weight: 600; font-size: var(--fs-base); }
.pool-stock-code { font-size: var(--fs-xs); color: var(--text-muted); font-family: var(--font-mono); }
.pool-add-time { font-size: var(--fs-xs); color: var(--text-muted); }
.del-single { background: var(--accent-deep); border: none; border-radius: var(--r-pill); padding: var(--s1) var(--s2); color: var(--text-main); cursor: pointer; font-size: var(--fs-xs); }
.del-single:hover { background: var(--accent-deep2); }
.empty-pool { text-align: center; padding: var(--s5); color: var(--text-muted); font-size: var(--fs-sm); }
/* 明细表（原件 .stock-table-*；表头与内容居中 ✓） */
.stock-table-container {
  background: var(--bg-panel); backdrop-filter: blur(4px); border-radius: var(--r-md); padding: var(--s2);
  border: 1px solid var(--accent-border); margin: var(--s2) 0; overflow-x: auto;
}
/* 2026-10-05 方案 A：列数 11 → 9（删行业、代码并入名称列）⇒ min-width 同步下调。
   🔴 定值依据是**实测自然宽度**，不是拍脑袋：线上量出「去掉 min-width 后表格自然宽 = 676px」，
      而 9 列里最宽的概念列本身有 max-width:180px 兜着 ⇒ 给 720 就够（比自然宽留 ~44px 余量，
      个别长名/长概念也不会挤断）。
   ⚠️ 原值 1100 是从「11 列 + 4 列长表头」时代留下的：不减就等于**白留 300+px 空列宽**
      —— 实测 980 时表格被撑到 980，各列之间大片空白，反而更不像样。 */
.stock-table { width: 100%; border-collapse: collapse; text-align: center; min-width: 720px; }
.stock-table thead { background: var(--accent-bg); border-bottom: 2px solid var(--accent); }
.stock-table th { padding: var(--s2) var(--s1); font-weight: 600; color: var(--accent-text); font-size: var(--fs-xs); white-space: nowrap; text-align: center; }
.stock-table td { padding: var(--s2) var(--s1); border-bottom: 1px solid var(--border-soft); font-size: var(--fs-xs); text-align: center; }
.stock-table tbody tr:hover { background: var(--bg-hover); }
.rank-col { font-weight: 700; color: var(--accent-deep); }
.up { color: var(--up); }
.down { color: var(--down); }
.real-green { color: var(--down) !important; }
.code-click { cursor: pointer; color: var(--gold) !important; font-weight: 700; }
/* 2026-10-05 方案 A：名称格改「名称 + 代码」两行。
   写法对齐竞价优选（.yj-name-main / .yj-name-sub，YijinerView.vue:402-403）与 AI 精选报告
   （.name-main / .name-sub）—— 主人要求「参考其他的页面」。
   · 名称用 --text-main + 600，代码用 --gold（沿用原 .code-click 的配色，不动视觉语言）
   · line-height 1.3 收紧，两行合计仍比原来「代码列 + 名称列」省宽（列数 11 → 9）
   · 整表居中（.stock-table td 就是 center）⇒ 这里**不改成左对齐**，避免又出现
     「表头居中、内容左对齐」那种错位（YijinerView 那边踩过这个坑，注释里有记录）。 */
.stock-table .name-col { white-space: nowrap; }
.stock-table .name-main { font-weight: 600; color: var(--text-main); line-height: 1.3; }
.stock-table .name-sub { font-size: var(--fs-xs); line-height: 1.3; font-variant-numeric: tabular-nums; }
.concept-col { max-width: 180px; white-space: pre-wrap; color: var(--text-secondary); }
.prob-col { color: var(--up); font-weight: 700; }
/* 2026-10-05 主人要求：点表头排序。视觉照 StockTable.vue:399-418 的既有排序表头范式
   （pointer / hover 高亮 / active / 指示符占位），但**文字色用 token `--on-accent`**，
   **不照抄那边的裸 `#fff`** —— `_verify/color_guard.js` 是棘轮闸门：裸色值只许减不许增。 */
.stock-table th.sortable { cursor: pointer; user-select: none; }
.stock-table th.sortable:hover { color: var(--on-accent); }
.stock-table th.sortable.active { color: var(--on-accent); }
.stock-table .sort-ind { display: inline-block; min-width: 10px; color: var(--on-accent); font-weight: 700; }
.loading-placeholder, .empty-state { padding: var(--s8); text-align: center; color: var(--accent-text); background: var(--bg-card); border-radius: 16px; }
/* 2026-10-03: 原为 780px —— 不在 main.css 顶部约定的 5 档内，_verify/breakpoint_guard.js 判为
   新断点碎片（「780 与 768 是同一意图写两处」的魔数对，改一处必漏一处）。
   语义就是「手机端」⇒ 并到既有 768 档（763~780 那 17px 的差异无设计意图）。 */
@media (max-width: 768px) {
  .medal-card { min-width: 140px; }
  .medal-name-big { font-size: var(--fs-xl); }
  .medal-prob-big { font-size: 36px; }
}
</style>
