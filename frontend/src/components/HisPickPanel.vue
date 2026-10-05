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
          <div class="medal-code" @click="linkToSoftware(it.code)">{{ it.code }}</div>
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
            <th>排名</th><th>股票代码</th><th>股票名称</th><th>竞价涨幅</th><th>实时涨幅</th>
            <th>实体涨幅</th><th>异动</th><th>行业</th><th>概念</th><th>综合评分</th><th>可信度</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(it, idx) in list" :key="it.code">
            <td class="rank-col">{{ idx + 1 }}</td>
            <td class="code-click" @click="linkToSoftware(it.code)">{{ it.code }}</td>
            <td>{{ it.name }}</td>
            <td :class="cls(it.bidChange)">{{ fmt(it.bidChange) }}%</td>
            <td :class="isGreen(it) ? 'real-green' : cls(it.realChange)">{{ fmt(it.realChange) }}%</td>
            <td :class="cls(it.entityChange)">{{ fmt(it.entityChange) }}%</td>
            <td>{{ warnText(it.warnType) }}</td>
            <td>{{ it.industry }}</td>
            <td class="concept-col">{{ it.concept }}</td>
            <td class="prob-col">{{ it.probability }}%</td>
            <td>{{ it.confidence }}%</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="hp-right-bar">
      <button class="tdx-export-btn" @click="exportAll"><i class="fa fa-download"></i> 导出全部筛选结果至通达信</button>
    </div>

  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { hisPick } from '../api/hisPick'

const DEFAULTS = { stSuspend: true, markets: ['hs', 'cyb', 'kcb'], limitUp: true,
                   bidGt: 7, probLt: 65, confLt: 65, floatMvGt: 100, priceGt: 30 }
const LOCK_KEY = 'his_pick_filter_lock_v1'
const POOL_KEY = 'shunshi_stock_pool_v2'          // 与原件同一个 key
const POOL_LOCK_MS = 10 * 60 * 60 * 1000          // 原件：10 小时防刷新锁定

const form = reactive({ ...DEFAULTS, markets: [...DEFAULTS.markets] })
const locked = ref(false)
const loading = ref(false)
const list = ref([])
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
function linkToSoftware(code) {
  if (!code) return
  const f = document.createElement('iframe')
  f.style.display = 'none'
  f.src = `http://www.treeid/code_${code}`
  document.body.appendChild(f)
  setTimeout(() => document.body.removeChild(f), 1000)
}

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
   原: background:var(--accent) + color:var(--accent-text) ⇒ 浅红字压亮红底
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
.pool-btn:hover { background: var(--accent); border-color: var(--accent); color: var(--text-main); transform: translateY(-1px); }
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
.stock-table { width: 100%; border-collapse: collapse; text-align: center; min-width: 1100px; }
.stock-table thead { background: var(--accent-bg); border-bottom: 2px solid var(--accent); }
.stock-table th { padding: var(--s2) var(--s1); font-weight: 600; color: var(--accent-text); font-size: var(--fs-xs); white-space: nowrap; text-align: center; }
.stock-table td { padding: var(--s2) var(--s1); border-bottom: 1px solid var(--border-soft); font-size: var(--fs-xs); text-align: center; }
.stock-table tbody tr:hover { background: var(--bg-hover); }
.rank-col { font-weight: 700; color: var(--accent-deep); }
.up { color: var(--up); }
.down { color: var(--down); }
.real-green { color: var(--down) !important; }
.code-click { cursor: pointer; color: var(--gold) !important; font-weight: 700; }
.concept-col { max-width: 180px; white-space: pre-wrap; color: var(--text-secondary); }
.prob-col { color: var(--up); font-weight: 700; }
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
