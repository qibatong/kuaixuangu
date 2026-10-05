<template>
  <!--
    数据来源 / 口径脚注（2026-10-05 A2「主表统一数据来源与口径」）

    ★ 为什么必须存在：审查实测全站 21 条路由的页面上**没有一处**可见的「数据来源」字样
      （唯一相关的 StockDetailPanel.vue:87 那句还是写死的静态字面量）。
      用户看完一份股票名单，无法知道数据来自谁、属于哪个交易日、按什么口径算的 ——
      对金融工具来说这是信任缺口，也是「数据是不是假的/是不是昨天的」这类质疑的来源。

    ★ 为什么是**脚注**（表内容下方）而不是页头条：
      主人 2026-09-30 / 10-01 连续三次删掉同类标注，且都写明理由是「不再占用版面」：
        · StockView.vue:98-107   删「定格来源标注条」
        · AuctionView.vue:117-119 删「顶部口径提示」
        · LadderView.vue:6-8     删「时间 + 更新于」整块
      所以本次**一律不碰页头、不做常驻条**：只在表格内容下方放一行 --fs-xs 的浅灰小字，
      与仓库里既有的脚注范式（StockDetailPanel `.sd-foot` / LhbPanel `.lhb-foot`）同形。

    ★ 语义边界：本组件只讲「来源 + 交易日 + 口径」，**不碰新鲜度**。
      「更新于 HH:MM:SS / 每 30s 自动刷新 / 已超 5 分钟未更新」属于 DataStamp.vue（useDataStamp），
      两者不要混用 —— 主人删掉的「更新于」不要从这里复活。

    ★ 兜底纪律：`source` 为空 ⇒ 整个节点不渲染。宁可没有，也不许编一个来源糊上去。
  -->
  <div v-if="source" class="src-note" :class="{ 'src-note-inline': inline }">
    <i class="fa fa-info-circle" aria-hidden="true"></i>
    <span class="sn-item">数据来源：<b>{{ source }}</b></span>
    <span v-if="date" class="sn-item">交易日 <b>{{ date }}</b></span>
    <span v-if="caliber" class="sn-item">{{ caliber }}</span>
  </div>
</template>

<script setup>
defineProps({
  // 上游数据源（必填；空则不渲染）。例：'开盘啦' / '东方财富' / '选股宝'
  source: { type: String, default: '' },
  // 该表数据所属交易日 'YYYY-MM-DD'。没有可靠来源时**留空**，不要用当前时间凑。
  date: { type: String, default: '' },
  // 口径说明：这条数据是怎么算出来的。例：'净买入 = 列表 buyIn'
  caliber: { type: String, default: '' },
  // 贴边内联（用在已经有左右留白的面板里）；默认左对齐、无上边距
  inline: { type: Boolean, default: false },
})
</script>

<style scoped>
.src-note {
  display: flex;
  flex-wrap: wrap;               /* ★ 390px 必须能换行，绝不 nowrap（会横向撑破页面） */
  align-items: baseline;
  gap: var(--s1) var(--s2);
  margin-top: var(--s3);
  font-size: var(--fs-xs);
  line-height: 1.6;
  color: var(--text-muted);
  text-align: left;
}
.src-note i { opacity: 0.7; }
.sn-item b { font-weight: 700; color: var(--text-secondary); }
</style>
