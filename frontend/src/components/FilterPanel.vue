<template>
  <!-- 盘中/竞价共用同一套筛选条件(诗人需求: 盘中=不锁定的竞价,逻辑一致)
       2026-08-21 手机 + 桌面适配合一:
       用户连续 3 次反馈"右边输入框被遮住看不到" —— 根因是
       justify-content: space-between + nowrap (没命中媒体查询) 一起
       用, 把最后一项直接顶到屏幕右墙外切掉.

       解决: 全部样式通过 layoutStyle 响应式计算属性注入内联,
       根据当前视口宽度动态切换两种模式:
         A. 窄屏/手机/平板/笔记本 (window.innerWidth < 1400):
            - row-2 2 列 × 3 行 wrap, flex-start, 不许 space-between
            - 根容器 overflow-x:hidden 防撑破
            - 3 按钮独立一行靠右
         B. 大桌面 (>=1400):
            - row-1/row-2 一行 nowrap, row-2 space-between 两端对齐(美观)
            - 按钮吸右上角, 不单独换行
            - 每个 cell 恢复精确像素宽 (调用 inputW())
       内联 style 不依赖任何 CSS 文件缓存/specificity, 立刻生效. -->
  <div
v-if="store.filterReady" class="filter-custom" :class="{ 'filter-locked': store.isFilterLocked }"
       :style="layoutStyle.root"
>
    <!-- 第一行: 筛选项 + 右侧按钮对齐 -->
    <div class="filter-row filter-row-1" :style="layoutStyle.row1">
      <!-- 2026-08-25 正逻辑(勾上=只看这类票), tooltip 保留帮助理解; 主人要求去掉"只看"二字 -->
      <label style="white-space:nowrap;" title="勾选后只显示 ST / *ST / 停牌股; 不勾选则剔除"><input v-model="stSuspend" type="checkbox" :disabled="store.isFilterLocked"> ST/停牌</label>
      <span class="filter-divider" style="display:inline-block;">|</span>
      <label v-for="m in marketOptions" :key="m.value" style="white-space:nowrap;">
        <input v-model="markets" type="checkbox" :value="m.value" :disabled="store.isFilterLocked"> {{ m.label }}
      </label>
      <span class="filter-divider" style="display:inline-block;">|</span>
      <label style="white-space:nowrap;" title="勾选后把昨日涨停/连板股也包含进结果; 不勾选则剔除这类票"><input v-model="limitUp" type="checkbox" :disabled="store.isFilterLocked"> 昨涨停</label>
      <!-- 2026-09-28 v4.11.75 盘中实时专属: 剔除**已封涨停**(涨停池判定, 与"昨涨停"不同概念 ——
           后者看的是昨日, 前者看的是此刻) -->
      <template v-if="isSpot">
        <span class="filter-divider" style="display:inline-block;">|</span>
        <label style="white-space:nowrap;" title="剔除此刻已封涨停的票（涨停池判定，与「昨涨停」不是一回事）"><input v-model="spotExcludeZT" type="checkbox" :disabled="store.isFilterLocked"> 剔涨停</label>
      </template>

      <!-- 按钮组: 桌面端吸右上角; 手机端紧凑靠右 -->
      <!-- 2026-09-16 选股闸门: 交易日 9:00-9:26 全部动作按钮置灰(store.pickBlocked),
           title 显示具体原因; 输入框仍可编辑, 到点自动解禁后可直接点「应用」 -->
      <span class="filter-actions-top" :style="layoutStyle.actions">
        <button
class="tdx-export-btn filter-apply" style="background:var(--accent-deep);"
                :disabled="applyDisabled"
                :title="store.pickBlocked ? store.pickBlockedMsg : ''" @click="apply"
>应用</button>
        <button
class="tdx-export-btn filter-reset"
                :disabled="applyDisabled"
                :title="store.pickBlocked ? store.pickBlockedMsg : ''" @click="reset"
>重置</button>
        <button
v-if="store.strategy === 'auction'" class="tdx-export-btn filter-lock" :class="{ locked: store.isFilterLocked }"
                :disabled="store.pickBlocked" :title="store.pickBlocked ? store.pickBlockedMsg : ''"
                @click="toggleLock"
>{{ store.isFilterLocked ? '解锁' : '锁定' }}</button>
        <!-- 2026-09-05: 刷新按钮从 StockView 顶部规则条移入本组(仅竞价模式; 9:30 后
             刷新实时行情, 9:30 前等同重新选股)。点击 emit 给父组件处理。
             样式与相邻的 重置/锁定 对齐(同 padding/字号, 见下方 .filter-refresh),
             且**不带图标** —— 左侧三个按钮均为纯文字, 带图标会导致宽度不一致。 -->
        <button
v-if="store.strategy === 'auction'" class="tdx-export-btn filter-refresh"
                :disabled="store.pickBlocked"
                :title="store.pickBlocked ? store.pickBlockedMsg : '刷新实时行情'"
                @click="emit('refresh')"
>刷新</button>
      </span>
    </div>
    <!-- 第二行: 数值输入框.
         样式完全由 scoped CSS + 媒体查询控制 (参考 AuctionView tab 方案):
         - 桌面端 (≥1100px): 一排 nowrap, space-between 两端对齐
         - 手机端 (≤768px): flex-wrap wrap, flex-start, 按内容宽度 flow 换行 -->
    <div class="filter-row filter-row-2">
      <!-- ===== 竞价专属: 竞涨 / 竞额 =====
           2026-09-28 v4.11.75: spot 模式**不渲染**这两项 —— 盘中实时选股没有"竞价涨幅/
           竞价额"这两个概念(后端 /api/stocks_spot 也不消费 bidGt/bidLt/bidAmtFloor)。
           继续显示会让用户以为调它能影响盘中名单，实际无效。 -->
      <template v-if="!isSpot">
        <label class="filter-cell">
          竞涨 ≤<input v-model.number="bidGt" type="number" min="0" max="20" step="0.5" :disabled="store.isFilterLocked" :style="inputStyle(22)">%
        </label>
      </template>
      <!-- ===== 盘中实时专属: 实时涨幅区间 =====
           口径 = 后端 picker/filter.py::apply_spot_filters（R. 见 utils/filters.js 的
           buildSpotFilterParams 注释）。两端 0 = 不限（与市值/股价的 0=不限 约定一致）。
           ★ 2026-09-28 主人要求: **去掉「量比」与「换手」两个选择框**（AI选股 + 盘中实时
             两处都去）。只去 UI 入口 —— 字段、默认值、本地过滤(passSpotFilter)与后端传参
             **全部保留不动**（与 2026-09-14「评分≥」同类处置：保留说明、无 UI 入口），
             故两个筛选目标的默认值照常生效、对名单结果零影响。 -->
      <template v-else>
        <label class="filter-cell" title="实时涨幅区间(%)：当前价相对昨收的涨幅；两端 0 = 不限">
          <span class="mv-range-label">现涨</span><input v-model.number="chgFloor" type="number" min="0" max="20" step="0.5" :disabled="store.isFilterLocked" :style="inputStyle(26)"><span class="mv-range-op">~</span><input v-model.number="chgGt" type="number" min="0" max="20" step="0.5" :disabled="store.isFilterLocked" :style="inputStyle(26)">%
        </label>
      </template>
      <!-- 2026-09-14 主人拍板: 「涨停率」与「评分」筛的是同一个字段(probability), 属重复项 →
           合并为一项「分数」, 沿用原「评分」的**单阈值硬门槛**(2026-09-20 起默认 50, 最低 50), 绑定 scoreFloor。
           ⚠️ 保留说明: probLt/confLt 不再有 UI 入口, 但仍按默认值(50/50)传给后端,
             "分数<50 且 可信度<50"的双低判据在用户把分数调到 50 以下时会被主门槛覆盖(既有耦合, 未动)。 -->
      <label class="filter-cell">
        分数 ≥<input v-model.number="scoreFloor" type="number" min="50" max="100" step="1" title="最低 50" :disabled="store.isFilterLocked" :style="inputStyle(32)">分
      </label>
      <!-- 2026-09-14 主人需求: 原「流通 ≥」与「流通 ≤」两个独立格子**合并为区间一格**,
           显示为 `xx ≤ 自由流通 ≤ yy`。绑定字段不变(下限 floatMvFloor / 上限 floatMvGt),
           因此 filters.js 过滤语义、偏好持久化、后端传参全部零影响。
           ★ 2026-09-20 主人拍板「所有的流通市值改为自由流通市值」: 门槛判据的**数据口径**
             已从流通市值改为自由流通市值(后端 QuoteRow.mv = free_mv 优先, 缺则 float_mv;
             明细见 picker/contract.py 的 FIELD_AUTHORITY「mv」条), 故文案同步改「自由流通」。
           ⚠️ 符号必须是「≤」不能是「<」: filters.js 的判据是
              `mv < floor → 剔除` / `mv > ceil → 剔除` (= 非严格),
              显示成严格不等号会让文案与真实行为不符。
           ⚠️ 两端 0 仍表示「不限」(下限: mv<0 不可能命中; 上限: `floatMvGt > 0` 才生效)。 -->
      <label class="filter-cell" title="自由流通市值区间(亿)，含边界值；两端 0=不限">
        <span class="mv-range-label">自由流通</span><input v-model.number="floatMvFloor" type="number" min="0" max="5000" step="10" :disabled="store.isFilterLocked" :style="inputStyle(32)"><span class="mv-range-op">~</span><input v-model.number="floatMvGt" type="number" min="0" max="5000" step="10" :disabled="store.isFilterLocked" :style="inputStyle(32)">亿
      </label>
      <label class="filter-cell">
        股价 ≤<input v-model.number="priceGt" type="number" min="0" max="5000" step="10" title="0=不限" :disabled="store.isFilterLocked" :style="inputStyle(32)">元
      </label>
      <label v-if="!isSpot" class="filter-cell">
        <!-- 2026-09-21 主人反馈「对数字有遮挡」: 宽屏固定宽 42→56px, 4 位数(3500/10000)不裁边 -->
        竞额 ≥<input v-model.number="bidAmtFloor" type="number" min="0" max="100000" step="500" :disabled="store.isFilterLocked" :style="inputStyle(56)">万
      </label>
      <!-- 2026-09-14: 原「评分 ≥」行已删除(与「分数」重复, 同一个 probability 字段) -->
    </div>
  </div>
  <!-- 2026-08-25: 偏好/全局默认异步加载完成前的占位, 避免先用内置默认(limitUp=false)
       渲染，随后被用户偏好覆盖导致勾选状态闪烁 -->
  <div v-else class="filter-custom filter-loading" :style="layoutStyle.root">
    <span class="filter-loading-text">筛选加载中…</span>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, reactive, computed } from 'vue'
import { useStocksStore } from '../stores/stocks'
import { trackUsage } from '../api/activity'
import { showToast } from '../utils/toast'

const store = useStocksStore()
// 2026-09-05: 刷新按钮由 StockView 顶部规则条下移至此(与 应用/重置/锁定 同一组),
// 父组件通过 @refresh 绑定自己的刷新逻辑; 未监听时点击无副作用。
const emit = defineEmits(['refresh'])
const marketOptions = [
  { value: 'hs', label: '主' },
  { value: 'cyb', label: '创业' },
  { value: 'kcb', label: '科创' },
  { value: 'bj', label: '北交' }      // 2026-09-29 主人拍板「北交所纳入」
]

/* =========================================================
   2026-09-28 v4.11.75 盘中实时(spot)支持
   ---------------------------------------------------------
   两套条件**(必须)**分开存: 竞价 filterSettings / 盘中 spotFilterSettings。
   若共用一份, 用户调完盘中的"现涨区间"再切回竞价, 竞价就会莫名带上它
   (后端 /api/stocks 不消费 chgGt ⇒ 表现为"设置静默无效" —— 正是本次要根除的病)。

   ⚠️ 共用门槛(市值/价格/评分/市场)在 UI 上只出现一次(同一排输入框) ——
   因为后端两套过滤本就复用同一批门槛(apply_spot_filters 直接复用竞价门槛判据)。
   ⇒ 实现 = **共用键用 computed 双向代理**(读写都同步两份), 专属键直接绑各自对象。
   ========================================================= */
const isSpot = computed(() => store.strategy === 'spot')

// 共用门槛: 用 computed 的 get/set 做双向代理 —— 读时取当前份, 写时两份同值。
// 这样模板 v-model 照常工作, 且切换模式后门槛不会"跳变"。
function _shared(key) {
  return computed({
    get() {
      return (isSpot.value ? store.spotFilterSettings : store.filterSettings)[key]
    },
    set(val) {
      store.filterSettings[key] = val
      store.spotFilterSettings[key] = val
    }
  })
}
const stSuspend = _shared('stSuspend')
const limitUp = _shared('limitUp')
const floatMvFloor = _shared('floatMvFloor')
const floatMvGt = _shared('floatMvGt')
const priceGt = _shared('priceGt')
const scoreFloor = _shared('scoreFloor')

// markets 是数组(v-for 多选框), set 时需换新引用才能触发响应式
const markets = computed({
  get() {
    return (isSpot.value ? store.spotFilterSettings : store.filterSettings).markets
  },
  set(val) {
    const v = Array.isArray(val) ? val.slice() : []
    store.filterSettings.markets = v.slice()
    store.spotFilterSettings.markets = v.slice()
  }
})

// 专属键: 直接透出当前模式那一份的字段(用 computed 只读 + set 直写, 避免对象整体替换)
const bidGt = computed({
  get: () => store.filterSettings.bidGt,
  set: (v) => { store.filterSettings.bidGt = v }
})
const bidAmtFloor = computed({
  get: () => store.filterSettings.bidAmtFloor,
  set: (v) => { store.filterSettings.bidAmtFloor = v }
})

function _spotField(key) {
  return computed({
    get: () => store.spotFilterSettings[key],
    set: (v) => { store.spotFilterSettings[key] = v }
  })
}
const chgFloor = _spotField('chgFloor')
const chgGt = _spotField('chgGt')
// ★ 2026-09-28 主人要求: 去掉「量比」「换手」两个**选择框**(AI选股 + 盘中实时两处都去)。
//   ⇒ 这里同步删掉 volRatioFloor / turnoverFloor / turnoverGt 三个 computed(原本只服务
//      那两个输入框)。**字段本身与默认值一字未动** —— 仍活在 store.spotFilterSettings、
//      本地过滤(passSpotFilter)与后端传参(buildSpotFilterParams)里, 照常生效。
//      (= 与 2026-09-14「评分≥」同类处置: 保留数据链路, 只撤 UI 入口。)
const spotExcludeZT = _spotField('spotExcludeZT')

// 按钮禁用: 竞价模式受 9:00-9:26 闸门; 盘中模式**不受闸门**(盘中随时可重选)。
const applyDisabled = computed(() => {
  if (isSpot.value) return false
  return store.pickBlocked || (store.isFilterLocked && store.strategy === 'auction')
})

/* =========================================================
   响应式视口宽度 —— FilterPanel 布局根据当前 CSS 视口
   宽度在运行时动态切换, 完全不依赖 CSS 文件的媒体查询
   或 webview 是否缓存了旧 chunk. 任何屏幕尺寸只要
   window.innerWidth 变, style 立刻重新计算注入内联.

   2026-08-20 手机横屏适配补充:
   iPhone 14 Pro Max 横屏时, CSS 视口 innerWidth = 932, 如果
   直接用 innerWidth 对比 1100 阈值, 虽然 932<1100 仍然命中
   手机模式, 但后续如果我们把阈值调到 900 就会误判. 更稳妥的
   做法是: 以"设备真实短边"判断是不是手机:
     shortEdge = min(innerW, innerH, screen.w, screen.h)
   这样:
     手机竖屏 430: shortEdge=430 < 1100 → 手机 2 列 ✓
     手机横屏 932: shortEdge=430 < 1100 → 手机 2 列 ✓
     桌面 1280x800: shortEdge=800? 不对 —— 真实
       桌面 browser window innerW >= 1100 直接宽屏判断, 所以
       我们用 OR 逻辑: (shortEdge < 1100) AND (是手机 OR 窗口
       宽度 < 1100). 简化成一句话: 只有 "innerW >= 1100 且
       shortEdge >= 768" 才算桌面模式, 其它全部手机.
========================================================= */
const viewport = reactive({ w: (typeof window !== 'undefined') ? window.innerWidth : 1920, h: (typeof window !== 'undefined') ? window.innerHeight : 1080 })
let __resizeRaf = 0
function onResize() {
  cancelAnimationFrame(__resizeRaf)
  __resizeRaf = requestAnimationFrame(() => {
    viewport.w = window.innerWidth
    viewport.h = window.innerHeight
  })
}
onMounted(() => {
  viewport.w = window.innerWidth
  viewport.h = window.innerHeight
  window.addEventListener('resize', onResize, { passive: true })
  // 关键: 横屏/竖屏旋转必须强制再读一次, 不然 iOS Safari
  // orientationchange 之后的首帧 innerWidth 可能是旧值.
  window.addEventListener('orientationchange', () => setTimeout(onResize, 50), { passive: true })
  window.addEventListener('orientationchange', () => setTimeout(onResize, 250), { passive: true })
})
onBeforeUnmount(() => {
  cancelAnimationFrame(__resizeRaf)
  window.removeEventListener('resize', onResize)
})

// 关键阈值: 真正"电脑"宽度 = 窗口宽 >= 1100, 且设备非手机短边 (>=768)
// 这样 iPhone 14 Pro Max 横屏 innerWidth=932 不会被误判成电脑,
// 因为 shortEdge = min(932, 430, screen.w, screen.h) = 430 < 768
const shortEdge = computed(() => {
  const sw = typeof screen !== 'undefined' ? (screen.width || viewport.w) : viewport.w
  const sh = typeof screen !== 'undefined' ? (screen.height || viewport.h) : viewport.h
  return Math.min(viewport.w, viewport.h, sw, sh)
})
const isWideDesktop = computed(() => viewport.w >= 1100 && shortEdge.value >= 768)

/* =============== 布局 style (仅注入 root/row1/actions, row2 完全由 scoped CSS 控制) =============== */
const layoutStyle = computed(() => {
  if (isWideDesktop.value) {
    return {
      root: { width: '100%', maxWidth: '100%', boxSizing: 'border-box' },
      row1: {
        display: 'flex', flexWrap: 'nowrap', justifyContent: 'flex-start',
        alignItems: 'center', gap: '6px', width: '100%', boxSizing: 'border-box',
      },
      actions: {
        marginLeft: 'auto', display: 'inline-flex', alignItems: 'center',
        justifyContent: 'flex-end', gap: '6px', flexWrap: 'nowrap',
      },
    }
  }
  return {
    root: {
      width: '100%', maxWidth: '100%', boxSizing: 'border-box', overflowX: 'hidden',
    },
    row1: {
      // 2026-09-21 主人手机反馈「应用旁边是重置按钮吗？显示不全」——原 nowrap 把
      // 重置/锁定/刷新挤到 overflow-x:hidden 外直接裁掉。改 wrap: 筛选项排不下时
      // 按钮组(marginLeft:auto)整体换到下一行靠右, 正是本文件头注释描述的设计。
      display: 'flex', flexWrap: 'wrap', justifyContent: 'flex-start',
      alignItems: 'center', gap: '3px', rowGap: '5px', width: '100%',
      boxSizing: 'border-box', fontSize: '0.75rem',
    },
    actions: {
      marginLeft: 'auto', display: 'inline-flex', alignItems: 'center',
      justifyContent: 'flex-end', gap: '3px', flexWrap: 'nowrap', flexShrink: 0,
    },
  }
})

/* 输入框样式: 仅控制输入框本身的宽度/字号, 不涉及 cell/row2 布局 */
function inputStyle(px) {
  // 2026-09-21 P0-3 最小字号12px: 原窄屏 11px/宽屏 11.5px 统一提到 0.75rem(12px)
  if (isWideDesktop.value) return { width: `${px}px`, padding: '1px 3px', fontSize: '0.75rem', textAlign: 'center', lineHeight: '1.3' }
  const maxW = (viewport.w <= 480) ? 65 : (viewport.w <= 768 ? 75 : 85)
  return {
    width: 'auto', maxWidth: `${maxW}px`, minWidth: `${Math.min(22, px)}px`,
    padding: '2px 4px', fontSize: '0.75rem',
    lineHeight: '1.3', textAlign: 'center', boxSizing: 'border-box',
  }
}

async function apply() {
  // 2026-09-28 v4.11.75: 盘中实时(spot)走独立分支 —— 它没有"锁定"概念、
  // 不受 9:26 闸门、不做本地快照预筛(实时字段每次都在变, 缓存会立刻过期)。
  if (isSpot.value) {
    try {
      await store.fetchSpotList()
      store.saveUserPrefs()          // 盘中 6 参数入账号偏好(后端白名单已含)
      trackUsage('picker', false)
    } catch (e) {
      trackUsage('picker', true)
      showToast('❌ 盘中选股失败：' + (e.message || '未知错误'), 'error')
    }
    return
  }
  try {
    await store.applyCustomFilter()
    // 2026-09-22 v4.11.35 行为记录: 口径 = 用户主动操作一次记一次。
    // 失败/被拦(配额 429、闸门禁用)也记, 但进 blocked 计数 —— 这是运营最关心的
    // 「想用却用不了」的转化线索。放在 await 之后是为了拿到真实结果再判定。
    trackUsage('picker', !!(store.pickBlocked || store.quotaExceeded))
  } catch (e) {
    trackUsage('picker', true)
    // 2026-09-07 主人反馈「点应用没更新股池」: 原实现空吞异常(无 toast 无更新, 静默失败
    // 极难排查)—— 失败原因可见化(如会员/限流/后端异常), 便于定位
    showToast('❌ 应用失败：' + (e.message || '未知错误'), 'error')
  }
}
function reset() {
  // 盘中重置只复原 6 个盘中参数, **不动**竞价那份(否则用户切回竞价发现条件被改了)
  if (isSpot.value) return store.resetSpotFilterToDefault()
  return store.resetFilterToDefault()
}

// 锁定/解锁: 只有「锁定」算一次使用(解锁是撤销动作, 不该计使用次数)
function toggleLock() {
  const wasLocked = store.isFilterLocked
  store.toggleFilterLock()
  if (!wasLocked) trackUsage('picker')
}
</script>

<style scoped>
/* =========================================================
   2026-08-20 筛选面板彻底重写 —— 参考 AuctionView tab 方案:
   纯 CSS 媒体查询, 不依赖 JS computed 注入 inline style.
   桌面端 (≥1100px): 一排 nowrap, space-between 两端对齐
   手机端 (≤768px): flex-wrap wrap, flex:0 0 auto, flex-start + gap
========================================================= */

/* 整个筛选面板: 不允许横向溢出 */
.filter-custom {
  width: 100%;
  box-sizing: border-box;
  overflow-x: hidden;
}

/* 2026-08-25: 偏好加载完成前的占位(保持两行筛选面板的高度, 避免布局塌陷) */
.filter-loading {
  min-height: 56px;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  padding: 0 4px;
}
.filter-loading-text {
  font-size: 0.75rem;
  color: var(--text-muted, #889);
  opacity: 0.7;
}

/* 第一行 (checkbox/市场范围/分隔符 + 右上角3按钮) */
.filter-row-1 {
  display: flex;
  flex-wrap: nowrap;
  justify-content: flex-start;
  align-items: center;
  gap: 6px;
  width: 100%;
  box-sizing: border-box;
}

/* 右上角三按钮 (应用/重置/锁定) */
.filter-actions-top {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  flex-wrap: nowrap;
}
.filter-apply { padding: 4px 9px; font-size: 0.75rem; }
.filter-reset { padding: 3px 8px; font-size: 0.75rem; }
.filter-lock  { padding: 3px 8px; font-size: 0.75rem; }
/* 2026-09-05: 刷新按钮从 StockView 顶部移入本组, 尺寸与相邻的 重置/锁定 完全对齐
   (纯文字无图标; 应用按钮略大是主按钮的既有设计, 保留其视觉主次) */
.filter-refresh { padding: 3px 8px; font-size: 0.75rem; }

/* ===== 第二行: 核心布局 =====
   2026-09-21 主人反馈桌面端「很分散」: 原 space-between 把 5 个格子拉开到整行两端 →
   改 flex-start 靠左聚拢 + 20px 列间距; margin-top 2→8px 与上方按钮行拉开, 防贴叠 */
.filter-row-2 {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  flex-wrap: nowrap;
  gap: 20px;
  width: 100%;
  box-sizing: border-box;
  margin-top: 8px;
}

/* 隐藏 number 输入框的上下箭头(spin button): 会占输入框宽度挤压数字显示 */
.filter-cell input[type="number"]::-webkit-outer-spin-button,
.filter-cell input[type="number"]::-webkit-inner-spin-button {
  -webkit-appearance: none;
  margin: 0;
}

.filter-cell {
  display: inline-flex;
  align-items: center;
  justify-content: flex-start;
  flex: 0 1 auto;
  white-space: nowrap;
  gap: 2px;
  padding: 0;
  min-width: 0;
  overflow: hidden;
  font-size: 0.75rem;
}

/* 2026-09-14: 流通市值区间一格的中间运算符 `< 流通 <`
   (与两侧 input 之间留 3px, 手机端收紧到 2px; nowrap 保证不拆行) */
.mv-range-op {
  display: inline-block;
  margin: 0 3px;
  white-space: nowrap;
  font-size: 0.75rem;
  line-height: 1.3;
}
/* 5-5: 对称双输入框「自由流通 [20] ~ [200] 亿」的前置标签 */
.mv-range-label {
  display: inline-block;
  margin-right: 3px;
  white-space: nowrap;
  font-size: 0.75rem;
  line-height: 1.3;
}

.filter-cell input[type="number"] {
  width: auto;
  max-width: 85px;
  min-width: 46px;
  flex: 0 0 auto;
  padding: 2px 4px;
  font-size: 0.75rem;
  line-height: 1.3;
  text-align: center;
  box-sizing: border-box;
}

/* ===== 手机端 (≤768px): 紧凑布局, 按钮靠右 ===== */
@media (max-width: 768px) {
  .filter-row-1 {
    flex-wrap: nowrap;
    gap: 3px;
    font-size: 0.75rem;
  }
  .filter-row-1 label {
    font-size: 0.75rem;
    gap: 2px;
  }
  .filter-divider {
    margin: 0 1px;
    opacity: 0.4;
  }
  .filter-actions-top {
    order: unset;
    width: auto;
    margin-left: auto !important;
    margin-top: 0;
    flex-wrap: nowrap;
    justify-content: flex-end;
    gap: 3px;
    flex-shrink: 0;
  }
  .filter-apply { padding: 3px 7px; font-size: 0.75rem; min-height: 24px; }
  .filter-reset { padding: 3px 7px; font-size: 0.75rem; min-height: 24px; }
  .filter-lock  { padding: 3px 7px; font-size: 0.75rem; min-height: 24px; }
  /* 2026-09-05: 刷新按钮(手机端同 重置/锁定 尺寸, 含 min-height 保证等高) */
  .filter-refresh { padding: 3px 7px; font-size: 0.75rem; min-height: 24px; }

  /* 第二行: 换行 + 左对齐; 2026-09-21 主人反馈「对数字有遮挡」: 行间距 4→8px,
     输入框 70→80px 留足 4 位数余量 */
  .filter-row-2 {
    flex-wrap: wrap !important;
    justify-content: flex-start !important;
    gap: 8px 8px !important;
    margin-top: 8px !important;
  }
  .filter-cell {
    flex: 0 0 auto !important;
    font-size: 0.75rem;
  }
  .filter-cell input[type="number"] {
    max-width: 80px;
    font-size: 0.75rem;
  }
  .mv-range-op {
    margin: 0 2px;
    font-size: 0.75rem;
  }
}

/* 超窄屏 (≤480px): 进一步压缩 */
@media (max-width: 480px) {
  .filter-row-1 {
    gap: 2px;
    font-size: 0.75rem;
  }
  .filter-row-1 label { font-size: 0.75rem; }
  .filter-actions-top { gap: 2px; }
  .filter-apply, .filter-reset, .filter-lock {
    padding: 2px 6px; font-size: 0.75rem; min-height: 22px;
  }
  .filter-cell { font-size: 0.75rem; }
  /* 2026-09-21 主人反馈「对数字有遮挡」: 60→76px, 保证 4 位数完整显示 */
  .filter-cell input[type="number"] { max-width: 76px; font-size: 0.75rem; }
  .mv-range-op { margin: 0 1px; font-size: 0.75rem; }
}
</style>
