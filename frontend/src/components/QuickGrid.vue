<template>
  <!--
    手机端首页「快捷入口」宫格（2026-10-01 主人拍板版）

    背景（主人参考竞品「短线侠」手机端后拍板）:
      · **10 格常驻展开**（5×2），不做折叠；为此用紧凑规格（图标 40px / 标签 10px / 行距收紧），
        并把「市场情绪」面板压成一行（见 StockView.vue），实测 390×844 下首屏仍完整可见 4 行名单。
      · 10 格**可编辑**（长按或点右上「编辑」）：其余页面进"候选池"，长按换入。
        🔴 这不是锦上添花 —— 底部 tab 收成 4 格（首页/竞价/盘中/我的）后，
        盘前资讯/连板梯队/龙虎榜/自选池靠本宫格保证有一级入口，历史回看/大V复盘/股性/异动监管
        靠"候选池可换入"保证可达（详见 docs/移动端规划-参考短线侠-20261001.md §〇）。
      · 编辑结果存 localStorage（kx_quickgrid_v1），默认 10 格 = 主人确认的那套。

    ⚠️ 只在 ≤768px 渲染（桌面端顶部导航已含全部入口，不重复占位）。
    ⚠️ 图标必须来自 src/styles/fontawesome-subset.css 的那 108 个（自托管子集）——
       本文件用的都是子集内的；`fa-home` 不在子集里，首页用 `fa-th-large` 代替。
       `_verify/fa_guard.js` 会检查"有没有字形"，选了集外图标会红。
    ⚠️ 配色只用现有 token（--accent-bg/--success-bg/--warn-bg/--bg-card + 语义前景色），
       不新增裸色值 —— `_verify/color_guard.js` 是"只许减不许增"的棘轮闸门。
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
        <i class="fa qg-ic" :class="[it.icon, it.tint]"></i>
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
          <i class="fa" :class="[c.icon, c.tint]"></i> {{ c.label }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

/**
 * 全部可选入口（key 唯一；path 为路由；`homeTab` 非空 ⇒ 打首页的 mode-tab，用 ?t= 传递）。
 * 🔴 图标全部取自自托管 FA 子集（见文件顶部注释）。
 * tint 只用 token：qg-c-accent / qg-c-up / qg-c-down / qg-c-warn / qg-c-star / qg-c-plain
 */
const ALL_ITEMS = [
  // 第一行：选股主链路（与首页 mode-tabs 一一对应）
  { key: 'pick', label: '竞价选股', icon: 'fa-sun-o', tint: 'qg-c-accent', path: '/', homeTab: 'auction' },
  { key: 'spot', label: '实时动态', icon: 'fa-bolt', tint: 'qg-c-up', path: '/', homeTab: 'spot' },
  { key: 'aipick', label: 'AI金睛', icon: 'fa-android', tint: 'qg-c-plain', path: '/aipick' },
  { key: 'aipick_lgb', label: 'AI火眼', icon: 'fa-flask', tint: 'qg-c-warn', path: '/aipick-lgb' },
  { key: 'yijiner', label: '竞价一进二', icon: 'fa-level-up', tint: 'qg-c-down', path: '/', homeTab: 'yijiner' },
  // 第二行：异动与复盘（保持底部 4 格后这几页仍有一级入口）
  { key: 'auc', label: '竞价异动', icon: 'fa-bullhorn', tint: 'qg-c-accent', path: '/auction' },
  { key: 'ladder', label: '连板梯队', icon: 'fa-signal', tint: 'qg-c-plain', path: '/ladder' },
  { key: 'lhb', label: '龙虎榜', icon: 'fa-trophy', tint: 'qg-c-star', path: '/lhb' },
  { key: 'news', label: '盘前资讯', icon: 'fa-newspaper-o', tint: 'qg-c-up', path: '/news' },
  { key: 'pool', label: '自选池', icon: 'fa-star', tint: 'qg-c-star', path: '/pool' },
  // 候选池（默认不占格）：底部收成 4 格后这些页失去一级入口 ⇒ 靠"长按可换入"保证可达
  { key: 'zhpick', label: '竞价精选', icon: 'fa-certificate', tint: 'qg-c-star', path: '/', homeTab: 'zhpick' },
  { key: 'yidong', label: '异动监管', icon: 'fa-shield', tint: 'qg-c-warn', path: '/yidong' },
  { key: 'history', label: '历史回看', icon: 'fa-history', tint: 'qg-c-plain', path: '/history' },
  { key: 'temper', label: '股性', icon: 'fa-dashboard', tint: 'qg-c-up', path: '/temper' },
  { key: 'bigv', label: '大V复盘', icon: 'fa-sitemap', tint: 'qg-c-down', path: '/bigv' },
  { key: 'market', label: '板块', icon: 'fa-line-chart', tint: 'qg-c-accent', path: '/market' },
  { key: 'member', label: '会员', icon: 'fa-user', tint: 'qg-c-plain', path: '/member' },
]

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
.qg-ic {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 17px;                 /* 图标本体大小（tile 40px 见上） */
  line-height: 1;
  box-shadow: var(--shadow-card);
}
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

/* tint 只用现有 token（不新增裸色值） */
.qg-c-accent { background: var(--accent-bg); color: var(--accent); }
.qg-c-up { background: var(--bg-card); color: var(--up); }
.qg-c-down { background: var(--bg-card); color: var(--down); }
.qg-c-warn { background: var(--warn-bg); color: var(--warn-amber); }
.qg-c-star { background: var(--bg-card); color: var(--star); }
.qg-c-plain { background: var(--bg-card); color: var(--text-secondary); }

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
  gap: 4px;
  font-size: 0.7rem;
  color: var(--text-secondary);
  background: var(--bg-input);
  border: 1px solid var(--border-soft);
  border-radius: 12px;
  padding: 3px 9px;
  cursor: pointer;
}
.qg-chip:disabled { opacity: 0.4; cursor: default; }
.qg-chip .fa { font-size: 0.72rem; }

@media (max-width: 768px) {
  .qg-root { display: block; }
}
</style>
