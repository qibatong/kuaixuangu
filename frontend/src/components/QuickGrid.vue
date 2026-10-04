<template>
  <!--
    手机端首页「快捷入口」宫格（2026-10-01 主人拍板版：10 格内容 + 图标重设计）
    ⚠️ 2026-10-03 起**没有格子再带子项**：竞价异动改为直跳 /auction（与电脑端一致），
       子项面板机制(QuickGrid 的 sheet)保留但 CHILDREN 为空 ⇒ 当前无格子会弹面板。

    10 格（主人指定）:
      上排: 竞价选股 / 竞价精选 / 竞价优选(=竞价一进二) / **竞价异动(直跳，版块在页内 tab 切)** / **超智研判(原 AI预测; 直跳聚合页)**
      下排: 动态选股 / 连板天梯 / **异动计算器** / 盘前资讯 / **题材库(复盘已移到手机底部 tab)**

    · 常驻展开（5×2），紧凑规格（图标 42px / 标签 10px）—— 实测 390×844 首屏仍完整可见 4 行名单。
    · 10 格**可编辑**（长按或「编辑」）：其余入口进候选池，换入即可；编辑结果存 localStorage。
    · 2026-10-03 前：带子项的格子点一下弹**底部子项面板**（子项是深链到目标页指定 tab）。
      已按主人指令取消（手机端要"和电脑一样"）；机制未删，将来要恢复只需往 CHILDREN 加回条目。

    🎨 图标：自绘多色 SVG（10 色系，走 main.css :root 的 --qg-* token），
      白色主形(--qg-on) + 白色高光次形(--qg-hi) 叠在同色系渐变方块上（同类型 App 的图标语言）。
      ⚠️ 组件内**不写裸色值**（_verify/color_guard.js 棘轮）；图标必须在自托管 FA 子集内
      （本文件用到的 fa 类仅 `fa-exchange`，在集内 ✓）。
    ⚠️ 只在 ≤768px 渲染（桌面顶部导航已含全部入口）。
  -->
  <div class="qg-root">
    <!-- 2026-10-04 主人指令：删「快捷入口」标题与「编辑」按钮（qg-head 整块），
         长按进编辑的 contextmenu 入口也一并去掉 —— 宫格变成纯入口，
         编辑机制(editing/slots/candidates)保留在 script（不再有任何触发路径）。 -->

    <div class="qg-grid">
      <button
        v-for="(it, i) in slots"
        :key="it.key"
        class="qg-item"
        :class="{ 'qg-picking': editing && pickIndex === i }"
        :data-qg="it.key"
        :data-qg-children="it.children ? it.children.length : 0"
        :aria-label="it.label"
        @click="onTap(it, i)"
      >
        <span class="qg-ic" :class="'qg-h-' + it.hue">
          <span v-if="it.text" class="qg-txt" :class="{ 'qg-txt-sm': it.text.length > 1 }">{{ it.text }}</span>
          <svg v-else class="qg-svg" viewBox="0 0 24 24" aria-hidden="true">
            <path v-for="(sh, k) in it.shapes" :key="k" :d="sh.d" :style="pathStyle(sh)" />
          </svg>
          <!-- 有子项 ⇒ 右下角小三角，提示"点开还有内容" -->
          <i v-if="it.children" class="qg-more" aria-hidden="true"></i>
        </span>
        <span class="qg-lb">{{ it.label }}</span>
        <i v-if="editing" class="fa fa-exchange qg-swap"></i>
      </button>
    </div>

    <!-- 编辑态：候选池 -->
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
            <span v-if="c.text" class="qg-txt qg-txt-sm">{{ c.text }}</span>
            <svg v-else class="qg-svg" viewBox="0 0 24 24" aria-hidden="true">
              <path v-for="(sh, k) in c.shapes" :key="k" :d="sh.d" :style="pathStyle(sh)" />
            </svg>
          </span>
          {{ c.label }}
        </button>
      </div>
    </div>

    <!-- 子项面板（底部弹出）：竞价异动 / AI预测 / 复盘 -->
    <teleport to="body">
      <div v-if="sheet" class="qg-sheet-mask" @click.self="closeSheet">
        <div class="qg-sheet" role="dialog" :aria-label="sheet.label + ' 子项'">
          <div class="qg-sheet-h">
            <span class="qg-ic qg-ic-sm" :class="'qg-h-' + sheet.hue">
              <span v-if="sheet.text" class="qg-txt qg-txt-sm">{{ sheet.text }}</span>
              <svg v-else class="qg-svg" viewBox="0 0 24 24" aria-hidden="true">
                <path v-for="(sh, k) in sheet.shapes" :key="k" :d="sh.d" :style="pathStyle(sh)" />
              </svg>
            </span>
            <span class="qg-sheet-t">{{ sheet.label }}</span>
            <button class="qg-sheet-x" @click="closeSheet">关闭</button>
          </div>
          <div class="qg-sheet-list" :class="{ 'qg-grid2': sheet.children.length === 2 }">
            <button
              v-for="c in sheet.children"
              :key="c.label"
              class="qg-sheet-row"
              :data-qg-sub="c.label"
              @click="goChild(c)"
            >
              <i class="qg-dot" :class="'qg-dot-' + c.hue"></i>
              <span class="qg-sheet-lb">{{ c.label }}</span>
              <span class="qg-sheet-ar">›</span>
            </button>
          </div>
        </div>
      </div>
    </teleport>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

/**
 * 图标 path 样式（2026-10-03 去 AI 化改造）
 *  ① 从 presentation attribute 改为 **:style** —— SVG 属性里的 `var()` 不被解析，
 *     此前 `fill="var(--qg-on)"` 实际无效（`--qg-on` 甚至未定义）⇒ 图形会落到默认黑。
 *  ② 新增 `mid` 层（同色暗面）：**主形(on) / 高光(hi) / 暗面(mid)** 三层明暗，
 *     用材质层次替代"渐变 + 发光"；描边粗细按语义给（1.6/2.0/2.4 有节奏，不再统一）。
 */
function pathStyle(sh) {
  const tone = sh.mid ? 'var(--qg-mid)' : (sh.hi ? 'var(--qg-hi)' : 'var(--qg-on)')
  if (sh.stroke) {
    return {
      fill: 'none', stroke: tone, strokeWidth: (sh.sw || 0) + 'px',
      strokeLinecap: 'round', strokeLinejoin: 'round',
    }
  }
  return { fill: tone, fillRule: sh.eo ? 'evenodd' : 'nonzero' }
}

/**
 * 自绘图标（24×24 viewBox）。每项 = 若干 path：
 *   默认主形(var(--qg-on))；`hi:1` ⇒ 高光层；`mid:1` ⇒ 暗面层；
 *   `stroke:1` ⇒ 描边不填充（`sw` 粗细）；`eo:1` ⇒ evenodd(挖洞)。
 */
const ICONS = {
  // 竞价选股：漏斗（筛选）+ 上行箭头（选出）
  pick: [{ d: 'M2.6 3.6h14.2l-5.4 6.6v7.4l-3.4 1.9v-9.3z' },
         { d: 'M17.4 2.2l3.6 3.1-3.6 3.1V6.3h-3.4V4.1h3.4z', hi: 1 }],
  // 竞价精选：奖章（绶带 + 星心）
  // 内芯用**星**而不是圆点 —— 圆点缩到 42px 块里会被读成"人形/未知符号"(实测截图对比后改)
  zhpick: [{ d: 'M12 2.6a6.2 6.2 0 1 1 0 12.4 6.2 6.2 0 0 1 0-12.4z', stroke: 1, sw: 2 },
           { d: 'M12 6.6l1.2 2.5 2.8.4-2 2 .5 2.8L12 13l-2.5 1.3.5-2.8-2-2 2.8-.4z', hi: 1 },
           { d: 'M8.6 14.4L7 22l5-2.4 5 2.4-1.6-7.6', stroke: 1, sw: 2, hi: 1 }],
  // 竞价优选（=竞价一进二）：两级台阶 + 上行箭头
  yijiner: [{ d: 'M3 18.6h6.2v-5.2H3zm7.6 0H17v-9.2h-6.4z' },
            { d: 'M17.6 3.4l4 3.6-4 3.6V8h-3.2V6h3.2z', hi: 1 }],
  // 竞价异动（父格，带子版块）：铃铛 + 声波 + 铃舌
  auc: [{ d: 'M12 2.4a1.2 1.2 0 0 1 1.2 1.1 6 6 0 0 1 4.8 5.9v3.4c0 .9.3 1.7.9 2.4l.9 1.1H4.2l.9-1.1c.6-.7.9-1.5.8-2.4V9.4a6 6 0 0 1 4.9-5.9A1.2 1.2 0 0 1 12 2.4z' },
        { d: 'M8.9 17.3h6.2c0 1.7-1.4 3.1-3.1 3.1s-3.1-1.4-3.1-3.1z', mid: 1 },
        { d: 'M19.2 4.9c1.5 1.3 2.5 3.2 2.6 5.2', stroke: 1, sw: 2.9, hi: 1 },
        { d: 'M21.5 12.9c-.2 1.5-.8 2.8-1.8 3.9', stroke: 1, sw: 2.8 }],
  // AI预测（父格）：六边芯片 + 三节点
  ai: [{ d: 'M12 2.8l7.8 4.5v9.4L12 21.2l-7.8-4.5V7.3z', stroke: 1, sw: 2.4 },
       { d: 'M12 7.6a1.7 1.7 0 1 1 0 3.4 1.7 1.7 0 0 1 0-3.4z', hi: 1 },
       { d: 'M8.4 14.8a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3zm7.2 0a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3z', hi: 1 }],
  // 动态选股：发光脉冲线
  spot: [{ d: 'M2.2 12.9h2.6l2.3-5.6 3 12.1 2.6-8.2 1.5 3.2h7.7', stroke: 1, sw: 3.5 },
         { d: 'M2.2 12.9h2.6l2.3-5.6', stroke: 1, sw: 2.8, mid: 1 },
         { d: 'M20.5 11.7a1.5 1.5 0 1 1 0 2.9 1.5 1.5 0 0 1 0-2.9z', hi: 1 }],
  // 连板天梯：递增柱 + 柱顶高光
  ladder: [{ d: 'M2.6 19.4h4.7v-6.2H2.6z' },
           { d: 'M8.1 19.4h4.8V9.6H8.1z' },
           { d: 'M13.6 19.4h4.9V4.5h-4.9z' },
           { d: 'M2.6 13.2l4.7-.5.1 1.1-4.7.5z', mid: 1 },
           { d: 'M8.1 9.6l4.8-.5.1 1.1-4.8.5z', mid: 1 },
           { d: 'M13.6 4.5l4.9-.4v1.1l-4.9.4z', mid: 1 },
           { d: 'M2.1 20.4l19.3-.6v1.2l-19.3.6z', hi: 1 }],
  // 异动计算器：**线性机身**(白描边) + 屏幕 + 键盘全白实心 —— 2026-10-04 主人反馈
  //   旧版"白色实心机身叠白键"不清晰，改线性风格后橙底上白形对比拉开（同题材库等线性图标语言）
  calc: [{ d: 'M6.4 2.9h11.2c.9 0 1.6.7 1.6 1.6v15c0 .9-.7 1.6-1.6 1.6H6.4c-.9 0-1.6-.7-1.6-1.6v-15c0-.9.7-1.6 1.6-1.6z', stroke: 1, sw: 2.2 },
         { d: 'M7.8 5.7h8.4v3.1H7.8z', hi: 1 },
         { d: 'M8 11.3h2v2H8zm3 0h2v2h-2zm3 0h2v2h-2zM8 14.9h2v2H8zm3 0h2v2h-2zm3 0h2v2h-2z', hi: 1 }],
  // 盘前资讯：**喇叭**（2026-10-01 主人指定：改用喇叭图形，不再用报纸）
  news: [{ d: 'M3.4 9.1l3.5-.1 5.9-3.7c.4-.2.8.1.8.5v12.4c0 .4-.5.7-.9.4l-5.8-3.9-3.5-.1c-.4 0-.7-.3-.7-.7V9.8c0-.4.3-.7.7-.7z' },
         { d: 'M3.4 12.2h3.5v2.2H3.4z', mid: 1 },
         { d: 'M15.6 8.6c1.1 1.8 1.1 4.1 0 5.9', stroke: 1, sw: 2.9, hi: 1 },
         { d: 'M18.4 6.2c1.7 2.8 1.7 6.7 0 9.5', stroke: 1, sw: 2.8 }],
  // 复盘（父格）：文档 + 对勾 + 顶部夹
  review: [{ d: 'M5.6 4.6h12.8v15.8H5.6z' },
           { d: 'M9.4 4.6h5.2v1.8H9.4z', hi: 1 },
           { d: 'M8.6 13.2l2.2 2.2 4.6-4.8', stroke: 1, sw: 2, hi: 1 }],
  // ↓ 候选池
  aipick: [{ d: 'M1.8 11.6C3.4 8 7.3 5.1 12 5.1s8.6 2.9 10.2 6.5c-1.6 3.6-5.5 6.5-10.2 6.5S3.4 15.2 1.8 11.6z'
              + 'M12 7.4a4.2 4.2 0 1 0 0 8.4 4.2 4.2 0 0 0 0-8.4z', eo: 1 },
           { d: 'M12 9.2a2.4 2.4 0 1 1 0 4.8 2.4 2.4 0 0 1 0-4.8z', hi: 1 }],
  aipick_lgb: [{ d: 'M12 2.2c2.7 3.4 4.6 6.1 5.4 9 .8 2.9-.7 5.7-3.2 6.7-2.5 1-5.4 0-6.7-2.3-1.2-2.1-.6-4.6 1.1-6.5.4 1.2 1.3 2 2.3 2.3-.3-3 .1-6.1 1.1-9.2z' },
                { d: 'M12 10.6c1.2 1.7 2 3.1 2.1 4.5.1 1.4-1 2.4-2.1 2.4s-2.2-1-2.1-2.4c.1-1.4.9-2.8 2.1-4.5z', hi: 1 }],
  pool: [{ d: 'M12 2.4l2.9 6.1 6.7.9-4.9 4.7 1.2 6.6L12 17.5l-5.9 3.2 1.2-6.6-4.9-4.7 6.7-.9z' },
         { d: 'M12 7.4l1.4 2.9 3.2.4-2.3 2.2.6 3.2-2.9-1.5-2.9 1.5.6-3.2-2.3-2.2 3.2-.4z', hi: 1 }],
  lhb: [{ d: 'M7.4 3h9.2v4.2h2.6v1.6a4.6 4.6 0 0 1-4.1 4.6A5.6 5.6 0 0 1 13 15.6v2.2h3v2.2H8v-2.2h3v-2.2a5.6 5.6 0 0 1-2.1-2.2 4.6 4.6 0 0 1-4.1-4.6V7.2h2.6z' },
        { d: 'M12 5.6l1 2.1 2.3.3-1.7 1.7.4 2.3-2-1.1-2 1.1.4-2.3L8.7 8l2.3-.3z', hi: 1 }],
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
  yidong: [{ d: 'M12 2.4l8 2.8v6.2c0 4.9-3.3 9.2-8 10.6-4.7-1.4-8-5.7-8-10.6V5.2z' },
           { d: 'M11 15.8l-3.4-3.4 1.6-1.6L11 12.6l4.2-4.2 1.6 1.6z', hi: 1 }],
}

/** 色系（对应 main.css :root 的 --qg-*-a/--qg-*-b） */
/**
 * **文字图标**（2026-10-01 主人指定：竞价选股=「选」、竞价精选=「精」、竞价优选=「优」、
 *   超智研判=「智」（原 AI预测）、题材库=「题材」）—— 不再画 SVG，直接落在同色系渐变块上（更直白、零字形风险）。
 */
const TEXT_ICON = {
  pick: '选',
  zhpick: '精',
  yijiner: '优',
  ai: '智',
  themelib: '题材',
}

const HUES = {
  pick: 'red', zhpick: 'gold', yijiner: 'indigo', auc: 'pink', ai: 'purple',
  spot: 'magenta', ladder: 'brown', calc: 'orange', news: 'blue', review: 'brown',
  aipick: 'purple', aipick_lgb: 'orange', pool: 'indigo', lhb: 'gold',
  history: 'purple', temper: 'slate', bigv: 'blue', market: 'magenta',
  member: 'gold', yidong: 'pink', themelib: 'slate',
}

/**
 * 子项（底部面板）：全部**深链到目标页的指定 tab**，落地即命中该版块。
 *
 * ⚠️ 2026-10-03 主人指令后**本表已清空**：手机端宫格点「竞价异动」不再弹子版块面板，
 *   改为**直跳 `/auction`**（默认「竞价封单」），9 个版块在页内 tab 切换 ——
 *   **与电脑端完全一致**（电脑端本来就只从导航/页内 tab 进，没有"先选子版块"这一步；
 *   宫格 `.qg-root` 桌面 `display:none`，是手机专属入口，故差异只出现在手机）。
 *   原话：「手机端竞价异动板块，打开后需要选择子版块，可否和电脑一样？」
 *   同例：2026-10-01 超智研判也是这样从"子项面板"改成"直跳聚合页"。
 *   代价：失去"一键直达某子版块"；但 /auction 页内 9 个 tab 在 ≤768px 会**自动换行全显示**，
 *   点一下即可，不再多一层弹窗。
 *   将来若要恢复深链（如宫格长按直达 s3），把条目加回本表 + 目标格补 `path` 即可。
 */
const CHILDREN = {
  // auc: [ { label: '竞价封单', hue: 'red', path: '/auction?tab=s3' }, ... ]  ← 2026-10-03 清空
  // 2026-10-01: 原 review(复盘) 子项已随「复盘」移到底部 tab 一并删除
  //   —— 复盘组各页仍由页面内的 GroupNav 二级 pill 提供（连板天梯/龙虎榜/异动监管/大V复盘/历史回看/股性）
}

/** 全部可选入口（默认 10 格的来源 + 候选池） */
const ALL_ITEMS = [
  // 上排（主人指定）
  { key: 'pick', label: '竞价选股', path: '/', homeTab: 'auction', wb: 1 },
  { key: 'zhpick', label: '竞价精选', path: '/', homeTab: 'zhpick', wb: 1 },
  { key: 'yijiner', label: '竞价优选', path: '/', homeTab: 'yijiner', wb: 1 },
  // 2026-10-03: 与电脑端一致 —— 直跳 /auction（默认竞价封单），版块在**页内 tab** 切，不再弹子项面板
  { key: 'auc', label: '竞价异动', path: '/auction' },
  { key: 'ai', label: '超智研判', path: '/chaozhi' },        // 原「AI预测」；2026-10-01 起直跳聚合页（原为火眼/金睛子项面板）
  // 下排（主人指定）
  { key: 'spot', label: '动态选股', path: '/', homeTab: 'spot', wb: 1 },
  { key: 'ladder', label: '连板天梯', path: '/ladder' },
  { key: 'calc', label: '异动计算器', path: '/yidong?tab=calc' },
  { key: 'news', label: '盘前资讯', path: '/news' },
  // 2026-10-01 主人: 「复盘」移到手机底部 tab（盘中与我的之间）⇒ 本格换成「题材库」
  { key: 'themelib', label: '题材库', path: '/theme' },
  // 候选池（默认不占格）：换入即可常驻
  { key: 'pool', label: '自选池', path: '/pool' },
  { key: 'aipick', label: 'AI预测·金睛', path: '/aipick' },
  { key: 'aipick_lgb', label: 'AI预测·火眼', path: '/aipick-lgb' },
  { key: 'lhb', label: '龙虎榜', path: '/lhb' },
  { key: 'yidong', label: '异动监管', path: '/yidong' },
  { key: 'history', label: '历史回看', path: '/history' },
  { key: 'temper', label: '股性', path: '/temper' },
  { key: 'bigv', label: '大V复盘', path: '/bigv' },
  { key: 'market', label: '板块', path: '/market' },
  { key: 'member', label: '会员', path: '/member' },
].map((x) => ({
  ...x,
  hue: HUES[x.key] || 'red',
  text: TEXT_ICON[x.key] || '',
  shapes: ICONS[x.key] || [],
  children: CHILDREN[x.key] || null,
}))

/** 主人确认的默认 10 格（上排 5 + 下排 5） */
const DEFAULT_KEYS = ['pick', 'zhpick', 'yijiner', 'auc', 'ai',
                      'spot', 'ladder', 'calc', 'news', 'themelib']
const SLOTS = 10
const LS_KEY = 'kx_quickgrid_v3'   // v3: 复盘移出宫格、新增题材库(内容变了, 旧的本地编排作废)        // v2: 10 格内容按主人新指定重排(v1 的旧编排作废)
const byKey = (k) => ALL_ITEMS.find((x) => x.key === k) || ALL_ITEMS[0]

const router = useRouter()
const route = useRoute()
const editing = ref(false)
const pickIndex = ref(-1)
const sheet = ref(null)
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
  } catch (e) { /* 隐私模式/配额满: 忽略 */ }
}

const slots = computed(() => keys.value.map(byKey))
const usedKeys = computed(() => new Set(keys.value))
const isUsed = (k) => usedKeys.value.has(k)
const candidates = computed(() => ALL_ITEMS.filter((x) => !usedKeys.value.has(x.key)))

function go(path) {
  if (path) router.push(path)
}

/** 点击格子：编辑态=选中待换；有子项=弹面板；否则=跳转 */
function onTap(it, i) {
  if (editing.value) {
    pickIndex.value = (pickIndex.value === i ? -1 : i)
    return
  }
  if (it.children && it.children.length) {
    sheet.value = it
    return
  }
  if (it.homeTab) {
    // 手机首页(盯盘台)不带选股表 => 四格进的是**工作台** `/?wb=1&t=...`；
    // 桌面端这些格子本就不渲染(<=768 才显示) => 不必区分设备。
    const q = { ...route.query, t: it.homeTab }
    if (it.wb) q.wb = '1'
    router.push({ path: '/', query: q })
  } else {
    go(it.path)
  }
}

function goChild(c) {
  closeSheet()
  go(c.path)
}

function closeSheet() { sheet.value = null }

function replaceWith(candKey) {
  if (pickIndex.value < 0 || isUsed(candKey)) return
  const next = keys.value.slice()
  next[pickIndex.value] = candKey
  keys.value = next
  pickIndex.value = -1
  saveKeys()
}

/* 2026-10-04: beginEdit/finishEdit/resetDefaults 随「编辑」按钮撤下而删除 ——
   编辑机制(editing/slots/candidates/replaceWith)保留：将来要恢复只需加回触发入口。 */
</script>

<style scoped>
.qg-root { display: none; }          /* 桌面端不渲染；≤768 打开（顶部导航已含全部入口） */

/* 2026-10-04: qg-head/qg-title/qg-edit 样式随模板一并删除（标题与编辑按钮撤下） */
.qg-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px 2px; }
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

/* 图标块（2026-10-03 参考图口径）：**饱和纯色圆角方块 + 纯白粗字形**
   · 统一圆角 15px、轻投影、顶部 1px 受光 —— 主流股票 App 的通用写法
   · 不用渐变、不用发光、不做纹理与微旋（参考图是干净统一的） */
.qg-ic {
  position: relative;
  width: 42px;
  height: 42px;
  border-radius: 15px;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: var(--qg-a);
  box-shadow: var(--qg-tile-shadow), inset 0 1px 0 var(--qg-top-light);
  overflow: hidden;
}
.qg-svg, .qg-txt { position: relative; z-index: 1; }
.qg-ic-sm { width: 20px; height: 20px; border-radius: 6px; }
.qg-svg { width: 25px; height: 25px; }        /* 与放大的文字图标对齐 */
.qg-ic-sm .qg-svg { width: 14px; height: 14px; }

/* 有子项的小三角（右下角） */
.qg-more {
  position: absolute;
  right: 3px;
  bottom: 3px;
  width: 0;
  height: 0;
  border-left: 5px solid transparent;
  border-bottom: 5px solid var(--qg-on);
  opacity: 0.9;
}

/* 文字图标（参考图 2「选股」写法）：方块上放**白底圆角徽章**，字用同族深色
   —— 比"纯白大字"更精致、更像人工设计的入口图标；对比度按徽章底计算（≥4.5:1） */
.qg-txt {
  color: var(--qg-on);            /* 纯白（不再用白底徽章）*/
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.3rem;              /* 加大：16px → 21px（方块 42px，留边 ~10px）*/
  font-weight: 800;
  line-height: 1;
  letter-spacing: 0;
  font-family: inherit;
}
.qg-txt-sm { font-size: 0.92rem; }                 /* 双字（「题材」）：约 14.7px，占宽 ~30px */
.qg-ic-sm .qg-txt { font-size: 0.8rem; }            /* 小尺寸块（更多面板）*/
.qg-ic-sm .qg-txt-sm { font-size: 0.56rem; }        /* 小尺寸 + 双字 */


.qg-lb {
  font-size: 0.625rem;             /* 10px */
  color: var(--text-secondary);
  line-height: 1.1;
  letter-spacing: -0.2px;
  white-space: nowrap;
}
.qg-swap { position: absolute; top: -2px; right: 8px; font-size: 0.6rem; color: var(--accent); }
.qg-picking .qg-ic { outline: 2px solid var(--accent); outline-offset: 1px; }

/* 色系别名（色值只在 main.css 的 :root 定义 —— 组件里不写裸值） */
.qg-h-red { --qg-a: var(--qg-red-a); --qg-b: var(--qg-red-b); }
.qg-h-magenta { --qg-a: var(--qg-magenta-a); --qg-b: var(--qg-magenta-b); }
.qg-h-purple { --qg-a: var(--qg-purple-a); --qg-b: var(--qg-purple-b); }
.qg-h-orange { --qg-a: var(--qg-orange-a); --qg-b: var(--qg-orange-b); }
.qg-h-indigo { --qg-a: var(--qg-indigo-a); --qg-b: var(--qg-indigo-b); }
.qg-h-pink { --qg-a: var(--qg-pink-a); --qg-b: var(--qg-pink-b); }
.qg-h-slate { --qg-a: var(--qg-slate-a); --qg-b: var(--qg-slate-b); }
.qg-h-gold { --qg-a: var(--qg-gold-a); --qg-b: var(--qg-gold-b); }
.qg-h-blue { --qg-a: var(--qg-blue-a); --qg-b: var(--qg-blue-b); }
.qg-h-brown { --qg-a: var(--qg-brown-a); --qg-b: var(--qg-brown-b); }

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

/* ===== 子项面板（底部弹出） ===== */
.qg-sheet-mask {
  position: fixed;
  inset: 0;
  z-index: 1200;                     /* 高于底部 tab 栏(1000) */
  background: var(--bg-card);
  display: flex;
  align-items: flex-end;
}
.qg-sheet {
  width: 100%;
  max-height: 70vh;
  overflow-y: auto;
  padding: 10px 12px calc(12px + env(safe-area-inset-bottom));
  background: var(--bg-panel-solid);
  border-top: 1px solid var(--border-soft);
  border-radius: 16px 16px 0 0;
  box-shadow: var(--qg-tile-shadow);
}
.qg-sheet-h { display: flex; align-items: center; gap: 8px; padding: 2px 2px 10px; }
.qg-sheet-t { font-size: 0.82rem; font-weight: 600; color: var(--text-main); }
.qg-sheet-x {
  margin-left: auto;
  background: transparent;
  border: none;
  color: var(--text-muted);
  font-size: 0.72rem;
  cursor: pointer;
}
/* 只有两项的子项（AI预测）⇒ **左右并排**（左火眼 / 右金睛），而不是上下两行 */
.qg-grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; padding-top: 4px; }
.qg-grid2 .qg-sheet-row { border: 1px solid var(--border-soft); border-radius: 10px; justify-content: center; }
.qg-grid2 .qg-sheet-ar { display: none; }

.qg-sheet-row {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  padding: 11px 6px;
  background: transparent;
  border: none;
  border-top: 1px solid var(--border-soft);
  color: var(--text-secondary);
  font-size: 0.8rem;
  text-align: left;
  cursor: pointer;
}
.qg-sheet-row:active { background: var(--bg-hover); }
.qg-dot { width: 8px; height: 8px; border-radius: 50%; flex: 0 0 auto; }
.qg-dot-red { background: var(--qg-red-a); }
.qg-dot-magenta { background: var(--qg-magenta-a); }
.qg-dot-purple { background: var(--qg-purple-a); }
.qg-dot-orange { background: var(--qg-orange-a); }
.qg-dot-indigo { background: var(--qg-indigo-a); }
.qg-dot-pink { background: var(--qg-pink-a); }
.qg-dot-slate { background: var(--qg-slate-a); }
.qg-dot-gold { background: var(--qg-gold-a); }
.qg-dot-blue { background: var(--qg-blue-a); }
.qg-dot-brown { background: var(--qg-brown-a); }
.qg-sheet-lb { flex: 1; }
.qg-sheet-ar { color: var(--text-dim); font-size: 0.9rem; }

@media (max-width: 768px) {
  .qg-root { display: block; }
}
</style>
