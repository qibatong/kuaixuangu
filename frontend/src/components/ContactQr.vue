<template>
  <!-- 客服微信二维码（2026-10-05 主人提供）。
       🔴 设计要点：图片缺失时**整块不渲染**（@error → failed=true）—— 在主人把图放进仓库之前，
          页面上不会出现裂图/空框，旁边原有的「复制客服微信」按钮照常可用，功能不退化。
       图片来源 = /wechat-qr.png，文件位置 = frontend/public/wechat-qr.png
         （Vite 会把 public/ 原样拷进 dist；用 public 是为了主人随时丢图即可生效、不必改代码）
       ⚠️ 部署后必须验一次 content-type=image/png（历史上出现过静态资源被 SPA 回退吃成 text/html）。 -->
  <figure v-if="!failed" class="wxqr" :class="{ 'wxqr--compact': compact }">
    <a
      class="wxqr-shot"
      :href="SUPPORT_WECHAT_QR"
      target="_blank"
      rel="noopener"
      title="在新标签打开原图：手机可长按保存，电脑可放大后扫码"
    >
      <img
        :src="SUPPORT_WECHAT_QR"
        :style="{ width: width + 'px' }"
        :alt="'客服微信二维码：' + SUPPORT_WECHAT"
        loading="lazy"
        @error="failed = true"
      />
    </a>
    <figcaption class="wxqr-cap">
      微信扫码加客服
      <small>点图放大 · 长按或右键可保存</small>
    </figcaption>
  </figure>
</template>

<script setup>
// 全站唯一的客服二维码组件：我的会员 / 付费墙 / 落地页 / 合规页 / 页脚 共 5 处复用，
// 避免"换个图要改五个地方"。
import { ref } from 'vue'
import { SUPPORT_WECHAT, SUPPORT_WECHAT_QR } from '../utils/contact'

defineProps({
  width: { type: Number, default: 190 },   // 显示宽度(px)；二维码本体约占其中 2/3 ⇒ 默认 ≥150px 可扫
  compact: { type: Boolean, default: false },
})

const failed = ref(false)
</script>

<style scoped>
.wxqr { margin: 0; display: inline-flex; flex-direction: column; align-items: center; gap: var(--s2); }
/* 二维码本体是白底图：外面再垫一层白 + 细边，深色主题下不会糊成一团 */
.wxqr-shot {
  display: block; padding: 6px; background: #fff;
  border: 1px solid var(--border-soft); border-radius: var(--r-md); line-height: 0;
  transition: transform 0.15s;
}
.wxqr-shot:hover { transform: translateY(-1px); }
.wxqr-shot img { display: block; height: auto; max-width: 100%; border-radius: 4px; }
.wxqr-cap { font-size: var(--fs-xs); color: var(--text-muted); text-align: center; line-height: 1.6; }
.wxqr-cap small { display: block; color: var(--text-dim); }
.wxqr--compact .wxqr-cap small { display: none; }
</style>
