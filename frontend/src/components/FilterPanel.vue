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
  <div v-if="store.filterReady" class="filter-custom" :class="{ 'filter-locked': store.isFilterLocked }"
       :style="layoutStyle.root">
    <!-- 第一行: 筛选项 + 右侧按钮对齐 -->
    <div class="filter-row filter-row-1" :style="layoutStyle.row1">
      <!-- 2026-08-25 正逻辑(勾上=只看这类票), tooltip 保留帮助理解; 主人要求去掉"只看"二字 -->
      <label style="white-space:nowrap;" title="勾选后只显示 ST / *ST / 停牌股; 不勾选则剔除"><input v-model="store.filterSettings.stSuspend" type="checkbox" :disabled="store.isFilterLocked"> ST/停牌</label>
      <span class="filter-divider" style="display:inline-block;">|</span>
      <label v-for="m in marketOptions" :key="m.value" style="white-space:nowrap;">
        <input v-model="store.filterSettings.markets" type="checkbox" :value="m.value" :disabled="store.isFilterLocked"> {{ m.label }}
      </label>
      <span class="filter-divider" style="display:inline-block;">|</span>
      <label style="white-space:nowrap;" title="勾选后把昨日涨停/连板股也包含进结果; 不勾选则剔除这类票"><input v-model="store.filterSettings.limitUp" type="checkbox" :disabled="store.isFilterLocked"> 昨涨停</label>

      <!-- 按钮组: 桌面端吸右上角; 手机端紧凑靠右 -->
      <!-- 2026-09-16 选股闸门: 交易日 9:00-9:26 全部动作按钮置灰(store.pickBlocked),
           title 显示具体原因; 输入框仍可编辑, 到点自动解禁后可直接点「应用」 -->
      <span class="filter-actions-top" :style="layoutStyle.actions">
        <button class="tdx-export-btn filter-apply" style="background:var(--accent-deep);"
                :disabled="store.pickBlocked || (store.isFilterLocked && store.strategy === 'auction')"
                :title="store.pickBlocked ? store.pickBlockedMsg : ''" @click="apply">应用</button>
        <button class="tdx-export-btn filter-reset"
                :disabled="store.pickBlocked || (store.isFilterLocked && store.strategy === 'auction')"
                :title="store.pickBlocked ? store.pickBlockedMsg : ''" @click="reset">重置</button>
        <button v-if="store.strategy === 'auction'" class="tdx-export-btn filter-lock" :class="{ locked: store.isFilterLocked }"
                :disabled="store.pickBlocked" :title="store.pickBlocked ? store.pickBlockedMsg : ''"
                @click="store.toggleFilterLock()">{{ store.isFilterLocked ? '解锁' : '锁定' }}</button>
        <!-- 2026-09-05: 刷新按钮从 StockView 顶部规则条移入本组(仅竞价模式; 9:30 后
             刷新实时行情, 9:30 前等同重新选股)。点击 emit 给父组件处理。
             样式与相邻的 重置/锁定 对齐(同 padding/字号, 见下方 .filter-refresh),
             且**不带图标** —— 左侧三个按钮均为纯文字, 带图标会导致宽度不一致。 -->
        <button v-if="store.strategy === 'auction'" class="tdx-export-btn filter-refresh"
                :disabled="store.pickBlocked"
                :title="store.pickBlocked ? store.pickBlockedMsg : '刷新实时行情'"
                @click="emit('refresh')">刷新</button>
      </span>
    </div>
    <!-- 第二行: 数值输入框.
         样式完全由 scoped CSS + 媒体查询控制 (参考 AuctionView tab 方案):
         - 桌面端 (≥1100px): 一排 nowrap, space-between 两端对齐
         - 手机端 (≤768px): flex-wrap wrap, flex-start, 按内容宽度 flow 换行 -->
    <div class="filter-row filter-row-2">
      <label class="filter-cell">
        竞涨 ≤<input v-model.number="store.filterSettings.bidGt" type="number" min="0" max="20" step="0.5" :disabled="store.isFilterLocked" :style="inputStyle(22)">%
      </label>
      <!-- 2026-09-14 主人拍板: 「涨停率」与「评分」筛的是同一个字段(probability), 属重复项 →
           合并为一项「分数」, 沿用原「评分」的**单阈值硬门槛**(2026-09-20 起默认 50, 最低 50), 绑定 scoreFloor。
           ⚠️ 保留说明: probLt/confLt 不再有 UI 入口, 但仍按默认值(50/50)传给后端,
             "分数<50 且 可信度<50"的双低判据在用户把分数调到 50 以下时会被主门槛覆盖(既有耦合, 未动)。 -->
      <label class="filter-cell">
        分数 ≥<input v-model.number="store.filterSettings.scoreFloor" type="number" min="50" max="100" step="1" title="最低 50" :disabled="store.isFilterLocked" :style="inputStyle(32)">分
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
        <input v-model.number="store.filterSettings.floatMvFloor" type="number" min="0" max="5000" step="10" :disabled="store.isFilterLocked" :style="inputStyle(32)"><span class="mv-range-op">≤ 自由流通 ≤</span><input v-model.number="store.filterSettings.floatMvGt" type="number" min="0" max="5000" step="10" :disabled="store.isFilterLocked" :style="inputStyle(32)">亿
      </label>
      <label class="filter-cell">
        股价 ≤<input v-model.number="store.filterSettings.priceGt" type="number" min="0" max="5000" step="10" title="0=不限" :disabled="store.isFilterLocked" :style="inputStyle(32)">元
      </label>
      <label class="filter-cell">
        竞额 ≥<input v-model.number="store.filterSettings.bidAmtFloor" type="number" min="0" max="100000" step="500" :disabled="store.isFilterLocked" :style="inputStyle(42)">万
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
import { showToast } from '../utils/toast'

const store = useStocksStore()
// 2026-09-05: 刷新按钮由 StockView 顶部规则条下移至此(与 应用/重置/锁定 同一组),
// 父组件通过 @refresh 绑定自己的刷新逻辑; 未监听时点击无副作用。
const emit = defineEmits(['refresh'])
const marketOptions = [
  { value: 'hs', label: '主' },
  { value: 'cyb', label: '创业' },
  { value: 'kcb', label: '科创' }
]

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
      display: 'flex', flexWrap: 'nowrap', justifyContent: 'flex-start',
      alignItems: 'center', gap: '3px', width: '100%',
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

// 保留旧 API 以防外部 CSS 选择器里仍然有 inputW 相关引用 (目前无)
function inputW(px) { return { width: `${px}px` } }

async function apply() {
  try {
    await store.applyCustomFilter()
  } catch (e) {
    // 2026-09-07 主人反馈「点应用没更新股池」: 原实现空吞异常(无 toast 无更新, 静默失败
    // 极难排查)—— 失败原因可见化(如会员/限流/后端异常), 便于定位
    showToast('❌ 应用失败：' + (e.message || '未知错误'), 'error')
  }
}
function reset() { store.resetFilterToDefault() }
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

/* ===== 第二行: 核心布局 —— 桌面端一排两端对齐 ===== */
.filter-row-2 {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: nowrap;
  gap: 0;
  width: 100%;
  box-sizing: border-box;
  margin-top: 2px;
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

  /* 第二行: 换行 + 左对齐 */
  .filter-row-2 {
    flex-wrap: wrap !important;
    justify-content: flex-start !important;
    gap: 4px 6px !important;
  }
  .filter-cell {
    flex: 0 0 auto !important;
    font-size: 0.75rem;
  }
  .filter-cell input[type="number"] {
    max-width: 70px;
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
  .filter-cell input[type="number"] { max-width: 60px; font-size: 0.75rem; }
  .mv-range-op { margin: 0 1px; font-size: 0.75rem; }
}
</style>
