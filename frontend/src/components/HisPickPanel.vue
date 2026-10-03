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
   ========================================================================= */
.hp-root {
  background: linear-gradient(135deg, #0f1219 0%, #0a0c12 100%);
  color: #eef2ff;
  border-radius: 8px;
  padding: 2px 6px 12px;
  line-height: 1.5;
}
.right-group { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.btn-group { display: flex; gap: 6px; flex-wrap: wrap; }
.tdx-export-btn {
  background: rgba(255, 70, 70, 0.2); border: 1px solid #ff5c5c; padding: 6px 14px; border-radius: 4px;
  color: #fff0f0; font-weight: bold; font-size: 12px; cursor: pointer; transition: all 0.2s;
  backdrop-filter: blur(4px); display: inline-flex; align-items: center; gap: 4px; white-space: nowrap;
}
.tdx-export-btn:hover:enabled { background: #ff3a3a; color: #fff; border-color: #fff; transform: translateY(-1px); box-shadow: 0 5px 12px rgba(255, 60, 60, 0.3); }
.tdx-export-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.real-time-btn { background: rgba(0, 200, 100, 0.2); border-color: #00c864; }
.real-time-btn:hover:enabled { background: #00c864; }
.reset-filter-btn { background: rgba(80, 140, 255, 0.2); border-color: #5a8aff; color: #d0e0ff; }
.apply-btn { background: #ff5c5c; }
.lock-filter-btn { background: rgba(160, 160, 160, 0.2); border: 1px solid #999; color: #ddd; }
.lock-filter-btn.locked { background: rgba(255, 140, 40, 0.25); border-color: #ff8c28; color: #ffe0c0; animation: lockPulse 2s ease-in-out infinite; }
@keyframes lockPulse { 0%, 100% { box-shadow: 0 0 0 0 rgba(255, 140, 40, 0.5); } 50% { box-shadow: 0 0 0 8px rgba(255, 140, 40, 0); } }
.filter-custom {
  background: rgba(0, 0, 0, 0.28); border: 1px solid rgba(255, 120, 120, 0.18);
  border-radius: 6px; padding: 8px 10px; margin: 6px 0; font-size: 12px; color: #ffd9d9;
}
.fh-row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin: 4px 0; }
.fh-row label { display: inline-flex; align-items: center; gap: 4px; }
.fh-row input[type="number"] {
  width: 62px; background: rgba(255, 255, 255, 0.08); border: 1px solid rgba(255, 255, 255, 0.18);
  color: #eef2ff; border-radius: 3px; padding: 2px 4px; font-size: 12px;
}
.fh-label { color: #ffbcbc; font-weight: 600; }
.filter-divider { color: rgba(255, 255, 255, 0.2); }
.lock-indicator { color: #ffe9a8; font-size: 12px; }
/* 勋章区（原件 .medal-section/.medal-card/…） */
.medal-section {
  background: rgba(18, 22, 35, 0.85); backdrop-filter: blur(8px); border-radius: 10px; padding: 16px;
  border: 1px solid rgba(255, 215, 0, 0.4); margin: 12px 0;
  display: flex; flex-wrap: wrap; gap: 15px; justify-content: space-around;
}
.medal-card {
  flex: 1 1 180px; min-width: 180px; text-align: center; padding: 12px 8px; border-radius: 12px;
  background: rgba(0, 0, 0, 0.35); border: 1px solid rgba(255, 215, 0, 0.3);
  display: flex; flex-direction: column; align-items: center; gap: 6px;
}
.medal-rank { font-size: 18px; font-weight: 700; color: #ffd700; display: flex; align-items: center; gap: 6px; }
.medal-rank-icon { font-size: 28px; }
.medal-name-big { font-size: 22px; font-weight: 800; color: #fff; letter-spacing: 1px; margin: 2px 0; }
.medal-code {
  font-size: 18px; font-weight: 600; color: #ffd700; cursor: pointer; font-family: 'Consolas', monospace;
  letter-spacing: 1px; margin: 2px 0; transition: color 0.2s;
}
.medal-code:hover { color: #fff; text-shadow: 0 0 8px rgba(255, 215, 0, 0.6); }
.medal-prob-big { font-size: 48px; font-weight: 900; color: #ff7b4a; line-height: 1; margin: 4px 0; }
.medal-detail { font-size: 13px; color: #ddd; display: flex; gap: 12px; align-items: center; }
.medal-detail .bid-chg { color: #ff8a6f; font-weight: 600; }
.medal-detail .conf-val { color: #ffbcbc; font-weight: 600; }
.medal-real-chg { font-size: 14px; font-weight: 700; color: #ff5252; margin-top: 2px; }
.medal-real-chg.green-real { color: #00c864 !important; }
.hp-right-bar { display: flex; justify-content: flex-end; margin: 6px 0; }
.export-medal-group { display: flex; align-items: center; gap: 8px; background: rgba(0, 0, 0, 0.3); padding: 8px 14px; border-radius: 8px; }
.hp-export-hint { color: #ffbcbc; font-size: 12px; }
.export-select {
  background: rgba(0, 0, 0, 0.5); border: 1px solid #ff5c5c; color: #fff; padding: 6px 10px;
  border-radius: 4px; font-size: 12px; outline: none; cursor: pointer;
}
/* 股票池（原件 .stock-pool-panel/.pool-*） */
.stock-pool-panel {
  background: rgba(0, 0, 0, 0.3); border: 1px dashed rgba(255, 120, 120, 0.3);
  border-radius: 8px; padding: 12px; margin: 10px 0;
}
.pool-header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; margin-bottom: 8px; border-bottom: 1px dashed rgba(255, 120, 120, 0.3); padding-bottom: 10px; }
.pool-title { font-size: 15px; font-weight: 700; color: #ffd9d9; display: flex; align-items: center; gap: 8px; }
.auto-tag { font-size: 11px; color: #ffe9a8; background: rgba(255, 215, 0, 0.12); padding: 3px 10px; border-radius: 20px; }
.pool-expiry-info { font-size: 11px; color: #ffcc88; margin-left: 8px; background: rgba(255, 150, 50, 0.15); padding: 3px 10px; border-radius: 20px; white-space: nowrap; display: inline-block; }
.pool-buttons { display: flex; gap: 8px; flex-wrap: wrap; }
.pool-btn {
  background: rgba(50, 65, 100, 0.6); border: 1px solid rgba(255, 160, 120, 0.5); padding: 5px 14px;
  border-radius: 20px; font-size: 12px; font-weight: 500; cursor: pointer; transition: 0.2s;
  color: #ffe0e0; display: inline-flex; align-items: center; gap: 5px;
}
.pool-btn:hover { background: #ff6a3a; border-color: #fff0c0; color: #fff; transform: translateY(-1px); }
.pool-list { max-height: 240px; overflow-y: auto; margin-top: 10px; border-radius: 8px; }
.pool-item {
  display: flex; align-items: center; justify-content: space-between; background: rgba(20, 28, 40, 0.7);
  margin: 6px 0; padding: 8px 12px; border-radius: 16px; border-left: 3px solid #ff8866;
}
.pool-item-info { display: flex; flex-direction: column; gap: 2px; }
.pool-stock-name { font-weight: 600; font-size: 14px; }
.pool-stock-code { font-size: 11px; color: #bbaaee; font-family: monospace; }
.pool-add-time { font-size: 10px; color: #88aacc; }
.del-single { background: rgba(220, 50, 50, 0.6); border: none; border-radius: 20px; padding: 4px 10px; color: #fff; cursor: pointer; font-size: 11px; }
.del-single:hover { background: #ff3a3a; }
.empty-pool { text-align: center; padding: 20px; color: #ccaacc; font-size: 13px; }
/* 明细表（原件 .stock-table-*；表头与内容居中 ✓） */
.stock-table-container {
  background: rgba(18, 22, 35, 0.85); backdrop-filter: blur(4px); border-radius: 8px; padding: 10px;
  border: 1px solid rgba(255, 80, 80, 0.35); margin: 8px 0; overflow-x: auto;
}
.stock-table { width: 100%; border-collapse: collapse; text-align: center; min-width: 1100px; }
.stock-table thead { background: rgba(255, 60, 60, 0.15); border-bottom: 2px solid #ff4d4d; }
.stock-table th { padding: 10px 4px; font-weight: 600; color: #ffbcbc; font-size: 12px; white-space: nowrap; text-align: center; }
.stock-table td { padding: 8px 4px; border-bottom: 1px solid rgba(255, 80, 80, 0.1); font-size: 12px; text-align: center; }
.stock-table tbody tr:hover { background: rgba(255, 60, 60, 0.05); }
.rank-col { font-weight: bold; color: #ff3a3a; }
.up { color: #ff8a6f; }
.down { color: #00c864; }
.real-green { color: #00c864 !important; }
.code-click { cursor: pointer; color: #ffd700 !important; font-weight: bold; }
.concept-col { max-width: 180px; white-space: pre-wrap; color: #b9c3da; }
.prob-col { color: #ff8a6f; font-weight: 700; }
.loading-placeholder, .empty-state { padding: 40px; text-align: center; color: #ffaaaa; background: rgba(0, 0, 0, 0.5); border-radius: 16px; }
@media (max-width: 780px) {
  .medal-card { min-width: 140px; }
  .medal-name-big { font-size: 18px; }
  .medal-prob-big { font-size: 36px; }
}
</style>
