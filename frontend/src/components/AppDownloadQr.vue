<template>
  <!-- 「快选股」安卓 App 下载二维码（未登录访客可见，落地页首屏弹层 + 中部下载区块共用）。
       🔴 设计要点与 ContactQr.vue 同套：图片缺失时**整块不渲染**（@error ⇒ failed=true）——
          图还没放进仓库就先上线时，页面上不会出现裂图/空框，旁边「下载安卓版」按钮照常可用。
       图片来源 = /app-qr.png，文件位置 = frontend/public/app-qr.png
         （Vite 把 public/ 原样拷进 dist；用 public 是为了换码不用改代码）。
       ⚠️ 码内编码的是**下载页 URL**（不是 APK 直链）—— 微信只拦 APK，不拦网页。
       ⚠️ 部署后必须验一次 content-type=image/png（历史上静态资源被 SPA 回退吃成 text/html 出过事故）。 -->
  <figure v-if="!failed" class="appqr">
    <a
      class="appqr-shot"
      :href="APP_QR"
      target="_blank"
      rel="noopener"
      title="在新标签打开原图：手机可长按保存，电脑可放大后扫码"
    >
      <img
        :src="APP_QR"
        :style="{ width: width + 'px' }"
        :alt="'快选股安卓版下载二维码：扫码打开下载页 ' + APP_PAGE_URL"
        loading="lazy"
        @error="failed = true"
      />
    </a>
    <figcaption class="appqr-cap">
      手机扫码下载安卓版
      <small>{{ APP_VERSION }} · {{ APP_SIZE }} · {{ APP_MIN_OS }} 及以上</small>
    </figcaption>
  </figure>
</template>

<script setup>
// 落地页两处共用（首屏弹层 / 中部下载区块）⇒ 收成一个组件，换码只改 public/app-qr.png。
import { ref } from 'vue'
import { APP_QR, APP_PAGE_URL, APP_VERSION, APP_SIZE, APP_MIN_OS } from '../utils/appDownload'

defineProps({
  width: { type: Number, default: 190 },   // 显示宽度(px)；码本体约占 2/3 ⇒ 默认 ≥150px 可扫
})

const failed = ref(false)
</script>

<style scoped>
.appqr { margin: 0; display: inline-flex; flex-direction: column; align-items: center; gap: var(--s2); }
/* 二维码本体是白底图：外面再垫一层白 + 细边，深色主题下不会糊成一团 */
.appqr-shot {
  display: block; padding: 6px; background: #fff;
  border: 1px solid var(--border-soft); border-radius: var(--r-md); line-height: 0;
  transition: transform 0.15s;
}
.appqr-shot:hover { transform: translateY(-1px); }
.appqr-shot img { display: block; height: auto; max-width: 100%; border-radius: 4px; }
.appqr-cap { font-size: var(--fs-xs); color: var(--text-muted); text-align: center; line-height: 1.6; }
.appqr-cap small { display: block; color: var(--text-dim); }
</style>
