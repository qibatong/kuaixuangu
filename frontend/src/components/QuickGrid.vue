<template>
  <!--
    手机端首页「快捷入口」宫格（2026-10-01 主人拍板版 + 图标重设计）

    背景（主人参考竞品「短线侠」手机端后拍板）:
      · **10 格常驻展开**（5×2），不做折叠；用紧凑规格（图标 42px / 标签 10px / 行距收紧），
        并把「市场情绪」面板压成一行（见 StockView.vue），实测 390×844 下首屏仍完整可见 4 行名单。
      · 10 格**可编辑**（长按或点右上「编辑」）：其余页面进"候选池"，换入即可。
        🔴 这不是锦上添花 —— 底部 tab 收成 4 格（首页/竞价/盘中/我的）后，
        盘前资讯/连板梯队/龙虎榜/自选池靠本宫格保证一级入口，历史回看/大V复盘/股性/异动监管
        靠"候选池可换入"保证可达（详见 docs/移动端规划-参考短线侠-20261001.md §〇）。
      · 编辑结果存 localStorage（kx_quickgrid_v1），默认 10 格 = 主人确认的那套。

    🎨 图标（2026-10-01 重设计，主人要求「参考同类型重新设计」）:
      放弃 Font Awesome 单色字形，改为**自绘多色 SVG**：每个入口一个色系（10 色系，
      走 main.css :root 的 --qg-* token），图标 = **白色主形 + 白色高光次形**（var(--qg-hi)）
      叠在同色系渐变方块上 —— 与同类型 App（竞品 5×2 宫格）的图标语言一致，但仍贴在深色主题里。
      好处：不受自托管 FA 子集(108 个)限制（fa-home 之类本来就不在集内）、40px 下更清晰、可整组调色。

    ⚠️ 只在 ≤768px 渲染（桌面端顶部导航已含全部入口，不重复占位）。
    ⚠️ 组件内**不写裸色值**：配色一律引用 token（_verify/color_guard.js 是"只许减不许增"的棘轮）。
  -->
  <div class="qg-root">
    <div class="qg-head">
      <span class="qg-title">快捷入口</span>
      <button v-if="!editing" class="qg-edit" @click="beginEdit">编辑</button>
      <template v-else>
        <button class="qg-edit" @click="resetDefaults">恢复默认</button>
        <button class="qg-edit qg-edit-done" @click="finishEdit">完成</button>
      </template>
    </div>

    <div class="qg-grid">
      <button
        v-for="(it, i) in slots"
        :key="it.key"
        class="qg-item"
        :class="{ 'qg-picking': editing && pickIndex === i }"
        :data-qg="it.key"
        :aria-label="it.label"
        @click="onTap(it, i)"
        @contextmenu.prevent="beginEdit(i)"
      >
        <span class="qg-ic" :class="'qg-h-' + it.hue">
          <svg class="qg-svg" viewBox="0 0 24 24" aria-hidden="true">
            <path
              v-for="(sh, k) in it.shapes"
              :key="k"
              :d="sh.d"
              :fill="sh.stroke ? 'none' : (sh.hi ? 'var(--qg-hi)' : 'var(--qg-on)')"
              :fill-rule="sh.eo ? 'evenodd' : 'nonzero'"
              :stroke="sh.stroke ? (sh.hi ? 'var(--qg-hi)' : 'var(--qg-on)') : 'none'"
              :stroke-width="sh.sw || 0"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </span>
        <span class="qg-lb">{{ it.label }}</span>
        <i v-if="editing" class="fa fa-exchange qg-swap"></i>
      </button>
    </div>

    <!-- 编辑态：候选池（点一个格子选中它，再点下面的候选换入） -->
    <div v-if="editing" class="qg-pool">
      <div class="qg-pool-hint">
        {{ pickIndex >= 0 ? '选一个替换第 ' + (pickIndex + 1) + ' 格：' : '点上方任一格子后选择替换项：' }}
      </div>
      <div class="qg-pool-list">
        <button
          v-for="c in candidates"
          :key="c.key"
          class="qg-chip"
          :disabled="pickIndex < 0 || isUsed(c.key)"
          :data-qg-cand="c.key"
          @click="replaceWith(c.key)"
        >
          <span class="qg-ic qg-ic-sm" :class="'qg-h-' + c.hue">
            <svg class="qg-svg" viewBox="0 0 24 24" aria-hidden="true">
              <path
                v-for="(sh, k) in c.shapes"
                :key="k"
                :d="sh.d"
                :fill="sh.stroke ? 'none' : (sh.hi ? 'var(--qg-hi)' : 'var(--qg-on)')"
                :fill-rule="sh.eo ? 'evenodd' : 'nonzero'"
                :stroke="sh.stroke ? (sh.hi ? 'var(--qg-hi)' : 'var(--qg-on)') : 'none'"
                :stroke-width="sh.sw || 0"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </span>
          {{ c.label }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

/**
 * 自绘图标（24×24 viewBox）。每项 = 若干 path：默认白色主形；`hi:1` ⇒ 高光次形（var(--qg-hi)）；
 * `stroke:1` ⇒ 描边不填充（线类图形用它）。
 */
const ICONS = {
  // 竞价选股：漏斗（筛选）+ 上箭头（选出）
  pick: [{ d: 'M2.6 3.6h14.2l-5.4 6.6v7.4l-3.4 1.9v-9.3z' },
         { d: 'M17.4 2.2l3.6 3.1-3.6 3.1V6.3h-3.4V4.1h3.4z', hi: 1 }],
  // 实时动态：脉冲折线 + 峰顶高光点
  spot: [{ d: 'M2 12.6h3.2l2.1-6.1 3.1 11.4 2.4-7.6 1.6 3.6h7.6', stroke: 1, sw: 3.8, hi: 1 },
         { d: 'M2 12.6h3.2l2.1-6.1 3.1 11.4 2.4-7.6 1.6 3.6h7.6', stroke: 1, sw: 1.9 }],
  // AI金睛：眼形（实心，evenodd 挖出眼白）+ 瞳孔高光（小尺寸下比描边清楚得多）
  aipick: [{ d: 'M1.8 11.6C3.4 8 7.3 5.1 12 5.1s8.6 2.9 10.2 6.5c-1.6 3.6-5.5 6.5-10.2 6.5S3.4 15.2 1.8 11.6z'
              + 'M12 7.4a4.2 4.2 0 1 0 0 8.4 4.2 4.2 0 0 0 0-8.4z', eo: 1 },
           { d: 'M12 9.2a2.4 2.4 0 1 1 0 4.8 2.4 2.4 0 0 1 0-4.8z', hi: 1 }],
  // AI火眼：火焰 + 内焰
  aipick_lgb: [{ d: 'M12 2.2c2.7 3.4 4.6 6.1 5.4 9 .8 2.9-.7 5.7-3.2 6.7-2.5 1-5.4 0-6.7-2.3-1.2-2.1-.6-4.6 1.1-6.5.4 1.2 1.3 2 2.3 2.3-.3-3 .1-6.1 1.1-9.2z' },
                { d: 'M12 10.6c1.2 1.7 2 3.1 2.1 4.5.1 1.4-1 2.4-2.1 2.4s-2.2-1-2.1-2.4c.1-1.4.9-2.8 2.1-4.5z', hi: 1 }],
  // 竞价一进二：两级台阶 + 上行箭头
  yijiner: [{ d: 'M3 18.6h6.2v-5.2H3zm7.6 0H17v-9.2h-6.4z' },
            { d: 'M17.6 3.4l4 3.6-4 3.6V8h-3.2V6h3.2z', hi: 1 }],
  // 竞价异动：铃铛 + 声波弧 + 铃舌
  auc: [{ d: 'M10.4 2.6a5.9 5.9 0 0 0-5.9 5.9v3.6L2.6 15.4h15.6l-1.9-3.3V8.5a5.9 5.9 0 0 0-5.9-5.9z' },
        { d: 'M8.2 17.4a2.4 2.4 0 0 0 4.4 0z', hi: 1 },
        { d: 'M19.4 4.4a8 8 0 0 1 0 9.2', stroke: 1, sw: 2.2, hi: 1 }],
  // 连板梯队：递增柱 + 柱顶高光
  ladder: [{ d: 'M3 19.4h4.2v-6.2H3zm7.3 0h4.2V9.2h-4.2zm7.3 0h4.2V4.4h-4.2z' },
           { d: 'M3 12h4.2v1.5H3zm7.3-4h4.2v1.5h-4.2zm7.3-4h4.2v1.5h-4.2z', hi: 1 }],
  // 龙虎榜：奖杯 + 星
  lhb: [{ d: 'M7.4 3h9.2v4.2h2.6v1.6a4.6 4.6 0 0 1-4.1 4.6A5.6 5.6 0 0 1 13 15.6v2.2h3v2.2H8v-2.2h3v-2.2a5.6 5.6 0 0 1-2.1-2.2 4.6 4.6 0 0 1-4.1-4.6V7.2h2.6z' },
        { d: 'M12 5.6l1 2.1 2.3.3-1.7 1.7.4 2.3-2-1.1-2 1.1.4-2.3L8.7 8l2.3-.3z', hi: 1 }],
  // 盘前资讯：报纸 + 文字行
  news: [{ d: 'M4 4.2h12.4l3.6 3.6v12H4z' },
         { d: 'M6.4 8.4h7.2v1.6H6.4zm0 4h9.2v1.6H6.4zm0 4h6v1.6h-6z', hi: 1 }],
  // 自选池：星标 + 内星
  pool: [{ d: 'M12 2.4l2.9 6.1 6.7.9-4.9 4.7 1.2 6.6L12 17.5l-5.9 3.2 1.2-6.6-4.9-4.7 6.7-.9z' },
         { d: 'M12 7.4l1.4 2.9 3.2.4-2.3 2.2.6 3.2-2.9-1.5-2.9 1.5.6-3.2-2.3-2.2 3.2-.4z', hi: 1 }],
  // ↓ 候选池
  zhpick: [{ d: 'M12 2.6a6.2 6.2 0 1 1 0 12.4 6.2 6.2 0 0 1 0-12.4z', stroke: 1, sw: 2 },
           { d: 'M12 6.4a2.4 2.4 0 1 1 0 4.8 2.4 2.4 0 0 1 0-4.8z', hi: 1 },
           { d: 'M8.6 14.4L7 22l5-2.4 5 2.4-1.6-7.6', stroke: 1, sw: 2, hi: 1 }],
  yidong: [{ d: 'M12 2.4l8 2.8v6.2c0 4.9-3.3 9.2-8 10.6-4.7-1.4-8-5.7-8-10.6V5.2z' },
           { d: 'M11 15.8l-3.4-3.4 1.6-1.6L11 12.6l4.2-4.2 1.6 1.6z', hi: 1 }],
  history: [{ d: 'M12 3.4a8.6 8.6 0 1 1-8.3 10.7h2.2A6.4 6.4 0 1 0 12 5.6c-1.9 0-3.6.8-4.8 2.1l2 2H3.4V3.9l2 2A8.5 8.5 0 0 1 12 3.4z' },
            { d: 'M12.9 8.2v4.4l3.2 1.9-.8 1.4-4-2.4V8.2z', hi: 1 }],
  temper: [{ d: 'M20.4 12H18.2A6.2 6.2 0 0 0 12 5.8V3.6A8.4 8.4 0 0 1 20.4 12z' },
           { d: 'M3.6 12A8.4 8.4 0 0 1 9.8 3.7v2.2A6.2 6.2 0 0 0 5.8 12z', hi: 1 },
           { d: 'M11.2 9.2l4.4 6-1.8 1.3-3.4-5.4z', hi: 1 }],
  bigv: [{ d: 'M3.4 4.4h17.2v11.2H12l-4.6 4V15.6H3.4z' },
         { d: 'M6.4 7.6h9.2v1.6H6.4zm0 3.4h6.4v1.6H6.4z', hi: 1 }],
  market: [{ d: 'M3.4 3.4h7.2v7.2H3.4zm10 0h7.2v7.2h-7.2zm-10 10h7.2v7.2H3.4z' },
           { d: 'M13.4 13.4h7.2v7.2h-7.2z', hi: 1 }],
  member: [{ d: 'M3.6 7.4l4.8 3.4L12 5l3.6 5.8 4.8-3.4-1.8 10.6H5.4z' },
           { d: 'M5.4 20.2h13.2v1.8H5.4z', hi: 1 }],
}

/** 色系（对应 main.css :root 的 --qg-*-a/--qg-*-b） */
const HUES = {
  pick: 'red', spot: 'cyan', aipick: 'purple', aipick_lgb: 'orange', yijiner: 'green',
  auc: 'pink', ladder: 'teal', lhb: 'gold', news: 'blue', pool: 'lime',
  zhpick: 'gold', yidong: 'orange', history: 'purple', temper: 'teal',
  bigv: 'blue', market: 'cyan', member: 'gold',
}

/**
 * 全部可选入口（key 唯一；path 为路由；`homeTab` 非空 ⇒ 打首页 mode-tab，用 ?t= 传递）。
 */
const ALL_ITEMS = [
  // 第一行：选股主链路（与首页 mode-tabs 一一对应）
  { key: 'pick', label: '竞价选股', path: '/', homeTab: 'auction' },
  { key: 'spot', label: '实时动态', path: '/', homeTab: 'spot' },
  { key: 'aipick', label: 'AI金睛', path: '/aipick' },
  { key: 'aipick_lgb', label: 'AI火眼', path: '/aipick-lgb' },
  { key: 'yijiner', label: '竞价一进二', path: '/', homeTab: 'yijiner' },
  // 第二行：异动与复盘（保持底部 4 格后这几页仍有一级入口）
  { key: 'auc', label: '竞价异动', path: '/auction' },
  { key: 'ladder', label: '连板梯队', path: '/ladder' },
  { key: 'lhb', label: '龙虎榜', path: '/lhb' },
  { key: 'news', label: '盘前资讯', path: '/news' },
  { key: 'pool', label: '自选池', path: '/pool' },
  // 候选池（默认不占格）：底部收成 4 格后这些页失去一级入口 ⇒ 靠"长按可换入"保证可达
  { key: 'zhpick', label: '竞价精选', path: '/', homeTab: 'zhpick' },
  { key: 'yidong', label: '异动监管', path: '/yidong' },
  { key: 'history', label: '历史回看', path: '/history' },
  { key: 'temper', label: '股性', path: '/temper' },
  { key: 'bigv', label: '大V复盘', path: '/bigv' },
  { key: 'market', label: '板块', path: '/market' },
  { key: 'member', label: '会员', path: '/member' },
].map((x) => ({ ...x, hue: HUES[x.key] || 'red', shapes: ICONS[x.key] || [] }))

/** 主人确认的默认 10 格 */
const DEFAULT_KEYS = ['pick', 'spot', 'aipick', 'aipick_lgb', 'yijiner',
                      'auc', 'ladder', 'lhb', 'news', 'pool']
const SLOTS = 10
const LS_KEY = 'kx_quickgrid_v1'
const byKey = (k) => ALL_ITEMS.find((x) => x.key === k) || ALL_ITEMS[0]

const router = useRouter()
const route = useRoute()
const editing = ref(false)
const pickIndex = ref(-1)
const keys = ref(loadKeys())

/**
 * 安全取 localStorage：SSR(冒烟用例)/隐私模式下它是**桩对象甚至不存在**，
 * 直接调 getItem 会抛 "localStorage.getItem is not a function" 把整页渲染带崩
 * （2026-10-01 nav.spec 实测抓到）。取不到就回落默认 10 格。
 */
function _ls() {
  try {
    if (typeof localStorage === 'undefined' || !localStorage) return null
    if (typeof localStorage.getItem !== 'function') return null
    return localStorage
  } catch (e) { return null }
}

function loadKeys() {
  try {
    const ls = _ls()
    const raw = ls ? ls.getItem(LS_KEY) : null
    const arr = raw ? JSON.parse(raw) : null
    if (Array.isArray(arr) && arr.length === SLOTS && arr.every((k) => ALL_ITEMS.some((x) => x.key === k))) {
      return arr
    }
  } catch (e) { /* 解析失败 ⇒ 用默认 */ }
  return DEFAULT_KEYS.slice()
}

function saveKeys() {
  try {
    const ls = _ls()
    if (ls && typeof ls.setItem === 'function') ls.setItem(LS_KEY, JSON.stringify(keys.value))
  } catch (e) { /* 隐私模式/配额满: 忽略（配置不持久化不影响使用） */ }
}

const slots = computed(() => keys.value.map(byKey))
const usedKeys = computed(() => new Set(keys.value))
const isUsed = (k) => usedKeys.value.has(k)
const candidates = computed(() => ALL_ITEMS.filter((x) => !usedKeys.value.has(x.key)))

/** 点击格子：编辑态=选中待换；正常态=跳转（首页 mode-tab 用 ?t= 带参） */
function onTap(it, i) {
  if (editing.value) {
    pickIndex.value = (pickIndex.value === i ? -1 : i)
    return
  }
  if (it.homeTab) {
    // 已在首页 ⇒ 只改 query（StockView 监听后切 mode-tab，不整页重载）
    router.push({ path: '/', query: { ...route.query, t: it.homeTab } })
  } else {
    router.push(it.path)
  }
}

function replaceWith(candKey) {
  if (pickIndex.value < 0 || isUsed(candKey)) return
  const next = keys.value.slice()
  next[pickIndex.value] = candKey
  keys.value = next
  pickIndex.value = -1
  saveKeys()
}

function beginEdit(i) {
  editing.value = true
  pickIndex.value = typeof i === 'number' ? i : -1
}

function finishEdit() {
  editing.value = false
  pickIndex.value = -1
}

function resetDefaults() {
  keys.value = DEFAULT_KEYS.slice()
  pickIndex.value = -1
  saveKeys()
}
</script>

<style scoped>
.qg-root { display: none; }          /* 桌面端不渲染；≤768 打开（顶部导航已含全部入口） */

.qg-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 2px 6px;
}
.qg-title { font-size: 0.72rem; color: var(--text-secondary); font-weight: 600; }
.qg-edit {
  margin-left: auto;
  background: transparent;
  border: none;
  color: var(--text-muted);
  font-size: 0.7rem;
  padding: 2px 4px;
  cursor: pointer;
}
.qg-edit-done { color: var(--accent); font-weight: 600; }

.qg-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 8px 2px;
}
.qg-item {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  background: transparent;
  border: none;
  padding: 0;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}
.qg-item:active { transform: scale(0.94); }

/* 图标块：同色系渐变 + 内高光，营造同类 App 那种"立体色块"观感 */
.qg-ic {
  width: 42px;
  height: 42px;
  border-radius: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(150deg, var(--qg-a), var(--qg-b));
  box-shadow: var(--qg-tile-shadow), inset 0 1px 0 var(--qg-hi);
}
.qg-ic-sm { width: 20px; height: 20px; border-radius: 6px; }
.qg-svg { width: 24px; height: 24px; }
.qg-ic-sm .qg-svg { width: 14px; height: 14px; }

.qg-lb {
  font-size: 0.625rem;             /* 10px */
  color: var(--text-secondary);
  line-height: 1.1;
  letter-spacing: -0.2px;
  white-space: nowrap;
}
.qg-swap {
  position: absolute;
  top: -2px;
  right: 8px;
  font-size: 0.6rem;
  color: var(--accent);
}
.qg-picking .qg-ic { outline: 2px solid var(--accent); outline-offset: 1px; }

/* 色系别名（色值只在 main.css 的 :root 定义 —— 组件里不写裸值） */
.qg-h-red { --qg-a: var(--qg-red-a); --qg-b: var(--qg-red-b); }
.qg-h-cyan { --qg-a: var(--qg-cyan-a); --qg-b: var(--qg-cyan-b); }
.qg-h-purple { --qg-a: var(--qg-purple-a); --qg-b: var(--qg-purple-b); }
.qg-h-orange { --qg-a: var(--qg-orange-a); --qg-b: var(--qg-orange-b); }
.qg-h-green { --qg-a: var(--qg-green-a); --qg-b: var(--qg-green-b); }
.qg-h-pink { --qg-a: var(--qg-pink-a); --qg-b: var(--qg-pink-b); }
.qg-h-teal { --qg-a: var(--qg-teal-a); --qg-b: var(--qg-teal-b); }
.qg-h-gold { --qg-a: var(--qg-gold-a); --qg-b: var(--qg-gold-b); }
.qg-h-blue { --qg-a: var(--qg-blue-a); --qg-b: var(--qg-blue-b); }
.qg-h-lime { --qg-a: var(--qg-lime-a); --qg-b: var(--qg-lime-b); }

.qg-pool {
  margin-top: 10px;
  padding: 8px 9px;
  background: var(--bg-card);
  border: 1px solid var(--border-soft);
  border-radius: 10px;
}
.qg-pool-hint { font-size: 0.68rem; color: var(--text-muted); margin-bottom: 7px; }
.qg-pool-list { display: flex; flex-wrap: wrap; gap: 6px; }
.qg-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 0.7rem;
  color: var(--text-secondary);
  background: var(--bg-input);
  border: 1px solid var(--border-soft);
  border-radius: 14px;
  padding: 3px 9px 3px 4px;
  cursor: pointer;
}
.qg-chip:disabled { opacity: 0.4; cursor: default; }

@media (max-width: 768px) {
  .qg-root { display: block; }
}
</style>
