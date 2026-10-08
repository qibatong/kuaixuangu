<template>
  <!--
    股票快速搜索结果面板（纯展示，2026-09-27 v4.11.63《快选股移动端追加清单》§三）
    —— **零状态、零请求**：数据全部由 props 灌入，事件向外抛。
    —— 这样拆分不是为了好看，是为了能被 SSR 冒烟测试用夹具直接渲染
      （沿用本仓 G1~G6「纯展示组件只吃 props，SSR 下不触发任何网络请求」的范式）：
      否则 `rows` 只能靠真请求填，模板里的自由变量错拼就永远抓不到。
    —— ★ 空态三态必须互斥且都说人话：
       loading（搜索中）/ empty（未找到，且回显关键词）/ err（服务不可用 + 重试）。
       「静默」是本项目的头号缺陷类型，搜索空白绝不能长得像「没有这只票」。
  -->
  <div class="ss-panel" :class="'ss-v-' + variant" data-no-chart>
    <!-- 底部 tabbar 变体：触发器只是个图标，输入框得长在面板里 -->
    <div v-if="variant === 'tabbar'" class="ss-panel-search">
      <i class="fa fa-search"></i>
      <input
        ref="inputEl"
        class="ss-input"
        type="search"
        :value="kw"
        placeholder="代码 / 名称 / 拼音首字母"
        autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false"
        aria-label="搜索股票"
        @input="$emit('input', $event.target.value)"
      />
      <button class="ss-panel-close" type="button" aria-label="关闭搜索" @click="$emit('close')">
        <i class="fa fa-times"></i>
      </button>
    </div>

    <!-- ① 未输入：给用法示例（三种输入方式都能命中） -->
    <div v-if="!kw.trim()" class="ss-hint">
      <i class="fa fa-lightbulb-o"></i>
      输入 <b>代码</b> / <b>名称</b> / <b>拼音首字母</b>，如「605058」「澳弘」「ahdz」
    </div>

    <!-- ② 搜索中（已有旧结果时不盖掉旧结果，避免列表闪空） -->
    <div v-else-if="phase === 'loading' && !rows.length" class="ss-state">
      <i class="fa fa-spinner fa-spin"></i> 搜索中…
    </div>

    <!-- ③ 失败：明说是"服务不可用"，不许静默成空列表 -->
    <div v-else-if="phase === 'err'" class="ss-state ss-err">
      <i class="fa fa-exclamation-triangle"></i> 搜索服务暂不可用：{{ errMsg || '网络异常' }}
      <button class="ss-retry" type="button" @click="$emit('retry')">重试</button>
    </div>

    <!-- ④ 无匹配：回显关键词，让"没有"是明确的 -->
    <div v-else-if="phase === 'empty'" class="ss-state">
      <i class="fa fa-search-minus"></i> 未找到与「{{ kw }}」匹配的股票
    </div>

    <!-- ⑤ 结果列表 -->
    <ul v-else-if="rows.length" class="ss-list" role="listbox" aria-label="搜索结果">
      <li v-for="(r, i) in rows" :key="r.code">
        <button
          :ref="(el) => setRowEl(el, i)"
          class="ss-row"
          :class="{ 'is-active': i === activeIdx }"
          type="button"
          role="option"
          :aria-selected="i === activeIdx ? 'true' : 'false'"
          @click="$emit('pick', r, i)"
        >
          <span class="ss-code">{{ r.code }}</span>
          <span class="ss-name">{{ r.name }}</span>
          <span v-if="r.board" class="ss-board">{{ r.board }}</span>
          <span v-if="r.py" class="ss-py">{{ r.py }}</span>
        </button>
      </li>
    </ul>

    <!-- 有结果时才提示键盘用法 -->
    <div v-if="rows.length" class="ss-foot">
      共 {{ rows.length }} 条 · 点选或回车打开「分时 / 日K / 周K / 月K」
      <span v-if="phase === 'loading'" class="ss-foot-loading"><i class="fa fa-spinner fa-spin"></i></span>
    </div>
  </div>
</template>

<script setup>
// 纯展示组件：只吃 props，只抛事件。不得引入 api / composables（否则 SSR 测试会真发请求）。
import { ref } from 'vue'

defineProps({
  // 'nav'（桌面内联，输入框在导航栏里 ⇒ 面板不重复渲染输入框）
  // 'tabbar'（手机底部入口，面板自带输入框）
  variant: { type: String, default: 'nav' },
  kw: { type: String, default: '' },
  rows: { type: Array, default: () => [] },
  // idle | loading | ok | empty | err
  phase: { type: String, default: 'idle' },
  errMsg: { type: String, default: '' },
  activeIdx: { type: Number, default: -1 },
})
defineEmits(['pick', 'retry', 'close', 'input'])

// 供容器在手机端点开面板后自动聚焦（键盘弹出即可直接输入）
const inputEl = ref(null)
function focus() { inputEl.value && inputEl.value.focus() }
// 2026-09-30 v4.11.84 (P2-5): 暴露行元素(按索引), 供父组件键盘上下键时 scrollIntoView。
//   原先父组件用 `panel 内 querySelector('.ss-row.is-active')` **全局查询**定位高亮行 ——
//   面板复用时(或页面上存在别的高亮行)会定位到别人的元素。改成按 activeIdx 取本组件的节点。
const rowEls = ref([])
function setRowEl(el, i) {
  if (el) rowEls.value[i] = el
}
defineExpose({ focus, rowEls, setRowEl })
</script>

<style scoped>
.ss-panel {
  background: var(--bg-panel-solid);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-lg);
  box-shadow: var(--sh-3);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  max-height: inherit;
}
body[data-bg="light"] .ss-panel { box-shadow: 0 10px 30px rgba(16, 24, 40, 0.16); }

/* 面板顶部输入框（仅 tabbar 变体） */
.ss-panel-search {
  display: flex; align-items: center; gap: var(--s2);
  padding: var(--s2) var(--s2);
  border-bottom: 1px solid var(--border-soft);
  background: var(--bg-card);
}
.ss-panel-search > i { color: var(--text-muted); }
.ss-input {
  flex: 1 1 auto; min-width: 0;
  background: var(--bg-input);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-md);
  color: var(--text-main);
  font-size: var(--fs-lg);            /* ≥16px：iOS 聚焦时才不会自动放大页面 */
  padding: var(--s2) var(--s2);
  outline: none;
  -webkit-appearance: none; appearance: none;
}
.ss-input:focus { border-color: var(--accent); }
/* 去掉 iOS/Chrome 自带的那个小 ✕（我们自己有清空按钮/关闭按钮） */
.ss-input::-webkit-search-cancel-button { display: none; }
.ss-panel-close {
  background: transparent; border: 1px solid var(--border-soft);
  color: var(--text-secondary); border-radius: var(--r-md);
  width: 32px; height: 32px; flex: 0 0 auto; cursor: pointer;
}
.ss-panel-close:hover { background: var(--bg-hover); color: var(--text-main); }

/* 提示 / 空态 / 错误态 */
.ss-hint, .ss-state {
  padding: var(--s4) var(--s4);
  font-size: var(--fs-sm);
  color: var(--text-muted);
  line-height: 1.7;
}
.ss-hint b { color: var(--text-secondary); }
.ss-state i { margin-right: var(--s2); }
.ss-err { color: var(--warn); }
body[data-bg="light"] .ss-err { color: #b87220; }
.ss-retry {
  margin-left: var(--s2); padding: var(--s1) var(--s3);
  background: transparent; color: var(--accent);
  border: 1px solid var(--accent); border-radius: var(--r-md);
  font-size: var(--fs-xs); cursor: pointer;
}
.ss-retry:hover { background: var(--accent-bg); }

/* 结果列表 */
.ss-list {
  list-style: none; margin: 0; padding: var(--s1);
  overflow-y: auto;
  overscroll-behavior: contain;   /* 列表内滚到底不再带动整页回弹（《移动端清单》§二·1） */
  -webkit-overflow-scrolling: touch;
}
.ss-row {
  display: flex; align-items: center; gap: var(--s2);
  width: 100%; text-align: left;
  padding: var(--s2) var(--s2);
  background: transparent; border: none; border-radius: var(--r-md);
  color: var(--text-main);
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  transition: background 0.12s;
}
.ss-row:hover, .ss-row.is-active { background: var(--bg-hover); }
.ss-row.is-active { box-shadow: inset 0 0 0 1px var(--accent-border); }
.ss-code {
  flex: 0 0 auto;
  font-variant-numeric: tabular-nums; font-feature-settings: "tnum";
  color: var(--accent); font-weight: 600; font-size: var(--fs-sm);
}
.ss-name {
  flex: 0 0 auto; min-width: 0;
  font-size: var(--fs-base); font-weight: 600; color: var(--text-main);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.ss-board {
  flex: 0 0 auto;
  font-size: var(--fs-xs); color: var(--text-muted);
  border: 1px solid var(--border-soft); border-radius: var(--r-sm);
  padding: 0 var(--s1);
}
.ss-py {
  flex: 0 0 auto;
  font-size: var(--fs-xs); color: var(--text-muted);
  font-family: inherit;
  letter-spacing: 0.5px;
}
.ss-foot {
  padding: var(--s2) var(--s3);
  border-top: 1px solid var(--border-soft);
  font-size: var(--fs-xs); color: var(--text-muted);
  display: flex; align-items: center; gap: var(--s2);
}
.ss-foot-loading { color: var(--accent); }
</style>
