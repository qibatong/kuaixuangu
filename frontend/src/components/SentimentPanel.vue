<template>
  <div class="sentiment-panel">
    <!-- 指数带(2026-09-20 新增): A股核心8指数, 涨红跌绿 -->
    <div class="index-strip">
      <div v-for="it in indices" :key="it.code" class="index-card">
        <span class="idx-name">{{ it.name }}</span>
        <span class="idx-px" :class="idxCls(it.pctChg)">{{ fmtIdxPx(it.px) }}</span>
        <!-- 5-3: 百分比与绝对值用 · 分隔 + 绝对值降对比度, 避免 "+0.97%+38.04" 粘连误读 -->
        <span class="idx-chg" :class="idxCls(it.pctChg)">{{ fmtIdxChg(it.pctChg) }}<template v-if="it.chg !== null"><span class="idx-chg-sep"> · </span><span class="idx-chg-pts">{{ fmtIdxPts(it.chg) }}</span></template></span>
      </div>
    </div>
    <div class="senti-vdivider"></div>

    <!-- 情绪卡(2026-09-20 数据源: 猫爪 emoindic 情绪周期)
         2026-09-21 主人方案 A 定稿: 手机端上=指数行、下=市场量能行, **两行都可横滑**。
         实现 = .emo-strip 弹性横排容器:
           桌面端: flex:1 1 auto + min-width:0 → 与指数带**同排平铺**并吃掉剩余宽度
           手机端: width:100% 独占第二行 + overflow-x:auto 可横滑
         ⚠️ 历史坑(勿回退): flex:0 1 0 会让桌面端宽度归零(总需求不溢出→shrink 不触发,
           而 grow:0 又永不长大), 实测 emo.width=0px → 整行被压没了(主人反馈"看不到了")。 -->
    <div v-if="emo && emo.u5 !== undefined" class="emo-strip">
      <div class="emo-card emo-card-mkt">
        <span class="idx-name">市场量能</span>
        <span class="emo-val mkt-amt">{{ fmtAmt(emo.am) }}</span>
        <span class="idx-chg" v-if="emo.am_diff !== null && emo.am_diff !== undefined" :class="emo.am_diff < 0 ? 'mkt-shrink' : 'mkt-grow'">{{ emo.am_diff < 0 ? '缩量' : '放量' }} {{ fmtAmt(Math.abs(emo.am_diff)) }}</span>
        <span class="idx-chg idx-flat" v-else>-</span>
      </div>
      <div class="emo-card">
        <span class="idx-name">涨跌家数</span>
        <span class="emo-val"><span class="mkt-rise">{{ emo.s2 ?? '-' }}</span>/<span class="mkt-fall">{{ emo.s6 ?? '-' }}</span></span>
        <span class="idx-chg idx-flat">涨/跌</span>
      </div>
      <div class="emo-card">
        <span class="idx-name">涨停/跌停</span>
        <span class="emo-val"><span class="zt">{{ emo.u5 ?? '-' }}</span>/<span class="dt">{{ emo.d3 ?? '-' }}</span></span>
        <span class="idx-chg idx-flat">炸板 {{ emo.u12 ?? '-' }}·{{ emo.fp108 != null ? emo.fp108.toFixed(1) + '%' : '-' }}</span>
      </div>
      <div class="emo-card">
        <span class="idx-name">高度板</span>
        <span class="emo-val lbg">{{ emo.l17 ?? '-' }}板</span>
        <span class="idx-chg idx-flat">连板高度</span>
      </div>
      <div class="emo-card">
        <span class="idx-name">亏钱效应</span>
        <span class="emo-val loss">{{ emo.deep_retrace_count ?? '-' }}</span>
        <span class="idx-chg idx-flat">大幅回撤</span>
      </div>
    </div>
    <div v-else-if="loading" class="senti-loading">加载中...</div>
    <div v-else class="senti-loading dim">情绪数据暂不可用</div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { kplIndexBrief } from '../api/kpl'
import { isIntradayNow } from '../utils/time'

const indices = ref([])
const emo = ref(null)
const loading = ref(true)

// 指数涨跌配色(红涨绿跌)
function idxCls(v) {
  if (v === null || v === undefined) return ''
  return v > 0 ? 'idx-up' : v < 0 ? 'idx-down' : 'idx-flat'
}
function fmtIdxPx(v) {
  return (v === null || v === undefined) ? '--' : v.toFixed(2)
}
function fmtIdxChg(v) {
  if (v === null || v === undefined) return '--'
  return (v > 0 ? '+' : '') + v.toFixed(2) + '%'
}
function fmtIdxPts(v) {
  return (v > 0 ? '+' : '') + v.toFixed(2)
}
function fmtAmt(v) {
  return (v === null || v === undefined) ? '--' : (v / 1e8).toFixed(0) + '亿'
}

// 指数带 + 情绪周期(同一接口一次取全): 盘中每 30s 刷新
let idxTimer = null
async function loadIndices() {
  try {
    const d = await kplIndexBrief()
    if (d && Array.isArray(d.list)) indices.value = d.list
    if (d && d.emo) emo.value = d.emo
  } catch (e) { /* 失败静默 */ } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadIndices()
  idxTimer = setInterval(() => { if (isIntradayNow()) loadIndices() }, 30000)
})
onBeforeUnmount(() => { if (idxTimer) clearInterval(idxTimer) })
</script>

<style scoped>
.sentiment-panel {
  display: flex;
  align-items: stretch;
  gap: 10px;
  /* 🔴 桌面端不换行(2026-09-21): 指数带 963 + 情绪带 449 = 1412 与容器 1413 **只差 1px**,
     wrap 模式下任何内容微增都会把情绪带顶到第二行。改为 nowrap 后由两个带各自
     overflow-x:auto 消化溢出, 布局恒定同排。手机端断点内显式覆盖为 column/nowrap。 */
  flex-wrap: nowrap;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 10px;
  padding: 8px 12px;
  margin: 10px 0;
}
.senti-title {
  color: #ffe0a0;
  font-size: 0.8125rem;
  font-weight: 600;
  white-space: nowrap;
  align-self: center;
}
.senti-title .fa { color: #ffb400; margin-right: 4px; }
/* 指数带: 紧凑靠左不拉伸(2026-09-20 主人反馈"情绪卡太靠右"), 空间不够时内部横滑。
   🔴 桌面端总宽算术(1440 视口): 指数带自然 963 + 情绪带自然 449 + gap 10 + padding 24 = 1446,
      而容器仅 1413 → **必然溢出 33px, 必然发生 shrink**。此时必须让**指数带独自承担全部收缩**
      (它内部 overflow-x:auto, 收窄只是多滑一点, 信息不丢); 情绪带 shrink:0 **完全不让位**,
      否则它的 449px 会被压缩 → 自身溢出(scrollW 449 > clientW 434) → 最后一两张卡被裁。
      实测: shrink 5/1 → 情绪带被压到 445(溢 4px); 1/1 → 被压到 434(溢 15px, 更糟)。
      故: 指数带 shrink:1(默认) 且情绪带 shrink:0 —— 由指数带吃掉全部 33px。 */
.index-strip {
  flex: 0 1 auto;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  overflow-x: auto;
  scrollbar-width: none;
  -ms-overflow-style: none;
}
.index-strip::-webkit-scrollbar { display: none; width: 0; height: 0; }
/* 情绪带: 桌面端 = 指数带的延续, 与指数卡**同排平铺**, 按内容自然宽度占位。
   🔴 2026-09-21 修复史(两次踩坑, 勿回退):
      v1 `flex: 0 1 0`  → basis 0 + grow 0: 容器 1413 / 指数带 963 时总需求 963 < 1413
                          **不触发 shrink**, 而 grow:0 又永不长大 → 宽度被钉死 **0px**,
                          实测 emo.width=0 / scrollWidth=449 → 整行 5 张情绪卡不可见。
                          (主人反馈"电脑端市场量能这一行看不到了")
      v2 `flex: 1 1 auto` → basis auto(449) + grow 1: 吃掉全部剩余空间长到 **1387px**,
                          把指数带挤到第二行(实测 indexTop=101 vs marketTop=177) → 换行回归。
      v3 `flex: 0 1 auto` → basis auto(449) + grow 0, 但 shrink 1: 仍与指数带**均摊**收缩,
                          实测情绪带被压到 434px → 自身溢出 15px(最后一两张卡被裁)。
      v4 `flex: 0 0 auto` (当前) → basis auto + grow 0 + **shrink 0**: 情绪带宽度锁定为
                          内容自然宽(449px)**绝不让位**; 桌面端溢出的 33px 全部由
                          .index-strip 独自承担(它含 overflow-x:auto, 收窄仅意味着多滑一点,
                          8 张指数卡信息完整不丢)。这是唯一能保证"情绪卡完整 + 不换行"的组合。
      结论: 情绪带 basis auto / grow 0 / **shrink 0**; 指数带承担全部收缩。 */
.emo-strip {
  flex: 0 0 auto;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  overflow-x: auto;
  scrollbar-width: none;
  -ms-overflow-style: none;
}
.emo-strip::-webkit-scrollbar { display: none; width: 0; height: 0; }
.index-card {
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
  padding: 3px 8px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.04);
  min-width: 78px;
}
.idx-name { font-size: 0.75rem; font-weight: 600; color: var(--text-muted); white-space: nowrap; }
.idx-px { font-size: 0.9375rem; font-weight: 700; font-family: inherit; }
.idx-chg { font-size: 0.75rem; font-weight: 600; font-family: inherit; white-space: nowrap; font-variant-numeric: tabular-nums; }
.idx-chg .idx-chg-sep { color: var(--text-muted); font-weight: 400; }
.idx-chg .idx-chg-pts { color: var(--text-secondary); font-weight: 500; }
.idx-px { font-variant-numeric: tabular-nums; }
.idx-up { color: #ff5252; }
.idx-down { color: #00c864; }
.idx-flat { color: var(--text-muted); }
/* 情绪卡: 与指数卡同款竖排卡片 */
.emo-card {
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
  padding: 3px 8px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.04);
  min-width: 78px;
}
.emo-val { font-size: 0.9375rem; font-weight: 700; white-space: nowrap; font-variant-numeric: tabular-nums; }
/* 亏钱效应: 绿色(负面指标, 主人指定) */
.emo-val.loss { color: #00c864; }
.senti-vdivider { width: 1px; align-self: stretch; background: rgba(255, 255, 255, 0.1); flex-shrink: 0; }
.senti-loading { color: var(--text-muted); font-size: 0.8125rem; align-self: center; }
.mkt-amt { color: #ffd76a; }      /* 成交额: 金色 */
.mkt-shrink { color: #6ad66a; }   /* 缩量: 绿 */
.mkt-grow { color: #ff8a5a; }     /* 放量: 橙红 */
.mkt-rise { color: #ff6a6a; }     /* 涨家数: 红 */
.mkt-fall { color: #6ad66a; }     /* 跌家数: 绿 */
.senti-val.zt, .emo-val .zt { color: #ff6a6a; }
.emo-val.lbg { color: #ffb400; }
.senti-val.dt, .emo-val .dt { color: var(--accent-text); }

/* 手机端(2026-09-21 主人指令, 方案 A 定稿):
     上 = 指数行, 下 = 市场量能行, **两行都允许横滑**。
   🔴 上一版把指数行做成「8 张卡平分整行 + 溢出裁掉」是错的:
      390px 下每卡仅 ~44px, 而「中证1000」名称需 40px+、「13730.02」数值需 55px+,
      装不下只能裁 → 实测出现 `3949.9113730.03` 数值粘连、名称首尾相连(主人截图反馈"太拥挤、不完整")。
      正解 = 卡片**恢复自然宽度**(min-width:78px, 同桌面端) + 容器 overflow-x:auto,
      内容完整不裁切, 超出部分左右滑动查看。
   ⚠️ 不要再对 .index-card 用 flex:1 1 0(平分压扁) 或隐藏 .idx-chg-pts(丢信息)。 */
@media (max-width: 576px) {
  .sentiment-panel {
    flex-direction: column;
    flex-wrap: nowrap;
    align-items: stretch;
    padding: 6px 8px;
    gap: 5px;
  }
  /* 第一行: 指数带占满整行, 卡片自然宽度, 溢出 → 横滑 */
  .index-strip {
    order: -1;
    flex: 0 0 auto;
    width: 100%;
    max-width: 100%;
    overflow-x: auto;
    gap: 6px;
    -webkit-overflow-scrolling: touch;
  }
  .senti-vdivider { display: none; }
  /* 卡片保持自然宽度, 不参与压缩(名称/数值完整显示) */
  .index-card {
    flex: 0 0 auto;
    padding: 3px 7px;
    gap: 0;
    overflow: visible;
  }
  .index-card .idx-name,
  .index-card .idx-px,
  .index-card .idx-chg {
    max-width: none;
    overflow: visible;
    text-overflow: clip;
    white-space: nowrap;
  }
  /* 绝对值段恢复显示(横滑空间足够, 不再丢信息) */
  .index-card .idx-chg-pts,
  .index-card .idx-chg-sep { display: inline; }
  .idx-name { font-size: 0.6875rem; }    /* 11px */
  .idx-px   { font-size: 0.8125rem; }    /* 13px */
  .idx-chg  { font-size: 0.6875rem; }    /* 11px */
  /* 第二行: 情绪卡横排, 总宽超出屏宽 → 可横滑 */
  .emo-strip {
    flex: 0 0 auto;
    width: 100%;
    max-width: 100%;
    gap: 8px;
    -webkit-overflow-scrolling: touch;
  }
  .emo-card { min-width: 88px; padding: 3px 8px; flex: 0 0 auto; }
  .emo-val { font-size: 0.875rem; }
  .senti-loading { align-self: flex-start; }
}

/* 超窄屏(<=480px, iPhone SE 等): 同样横滑, 仅微调间距与内边距 */
@media (max-width: 480px) {
  .sentiment-panel { padding: 5px 6px; gap: 4px; }
  .index-strip { gap: 5px; }
  .index-card { padding: 3px 6px; }
  .idx-name { font-size: 0.625rem; }     /* 10px */
  .idx-px   { font-size: 0.78125rem; }   /* 12.5px */
  .idx-chg  { font-size: 0.625rem; }     /* 10px */
  .emo-strip { gap: 7px; }
  .emo-card { min-width: 84px; padding: 3px 7px; }
  .emo-val { font-size: 0.8125rem; }
}

/* 浅色主题覆盖 */
body[data-bg="light"] .index-card { background: rgba(0, 0, 0, 0.04); }
body[data-bg="light"] .emo-card { background: rgba(0, 0, 0, 0.04); }
body[data-bg="light"] .senti-vdivider { background: rgba(0, 0, 0, 0.1); }
body[data-bg="light"] .idx-up { color: #c62828; }
body[data-bg="light"] .idx-down { color: #1a7a2a; }
body[data-bg="light"] .emo-val.loss { color: #1a7a2a; }
body[data-bg="light"] .mkt-amt { color: #8a6a00; }
body[data-bg="light"] .mkt-shrink { color: #2a7a2a; }
body[data-bg="light"] .mkt-grow { color: #b83010; }
body[data-bg="light"] .mkt-rise { color: #b83010; }
body[data-bg="light"] .mkt-fall { color: #2a7a2a; }
body[data-bg="light"] .senti-val.zt, body[data-bg="light"] .emo-val .zt { color: #b83010; }
body[data-bg="light"] .emo-val.lbg { color: #8a5500; }
</style>
