<!--
  🔴 2026-10-08 主人：「需要去掉水印功能」⇒ 本组件**已停用**（App.vue 里的 <Watermark /> 已注释掉）。
     它是"防截图泄露、便于追溯"的特性（登录后全屏铺「快选股 · 用户名 + 时间」的斜纹）。
     停用后截图无法追溯来源；要恢复只需取消 App.vue 里那一行的注释，并同步恢复
     LegalView.vue《隐私政策》"页面带有你账号标识的水印"那句（现已随停用一并改掉）。
     ⚠️ 名字相近但**无关**的东西：main.css 里的 `--watermark` 是**颜色 token**（很多页面的浅色
        主题拿它当文字色），与本功能无关，别一起删。
-->
<template>
  <!-- 全屏水印: 登录后显示用户名+时间, 防截图泄露便于追溯 (pointer-events:none 不挡操作) -->
  <div v-if="show && force" ref="wmEl" class="wm-layer" :style="{ backgroundImage: `url(${bg})` }"></div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useUserStore } from '../stores/user'
import { useTheme } from '../composables/useTheme'

const user = useUserStore()
const { bg: themeBg } = useTheme()
const show = computed(() => user.isLoggedIn && !!user.username)
const bg = ref('')
const force = ref(true)
const wmEl = ref(null)
let obs = null

function makeBg() {
  try {
    const text = '快选股 · ' + user.username
    const sub = new Date().toLocaleString('zh-CN', { hour12: false })
    // 颜色随主题: 深色背景用浅色文字, 浅色背景用深色文字(两种背景下都清晰可见)
    const isDark = themeBg.value !== 'light'
    const mainColor = isDark ? 'rgba(220,220,220,0.65)' : 'rgba(40,40,40,0.55)'
    const subColor  = isDark ? 'rgba(220,220,220,0.45)' : 'rgba(40,40,40,0.40)'
    const c = document.createElement('canvas')
    c.width = 300
    c.height = 180
    const ctx = c.getContext('2d')
    ctx.clearRect(0, 0, c.width, c.height)
    ctx.save()
    ctx.translate(c.width / 2, c.height / 2)
    ctx.rotate(-Math.PI / 6)
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.font = '17px sans-serif'
    ctx.fillStyle = mainColor
    ctx.fillText(text, 0, -8)
    ctx.font = '11px sans-serif'
    ctx.fillStyle = subColor
    ctx.fillText(sub, 0, 16)
    ctx.restore()
    bg.value = c.toDataURL('image/png')
  } catch (e) { /* 忽略, 水印失败不影响主流程 */ }
}

// 防删除: 水印节点被外部移除时强制重建。
// 2026-09-30 v4.11.83 (P2-6): 原实现 `observe(document.body, {childList:true, subtree:true})`
//   —— 全页任何 DOM 增删(每 30s 刷新都在增删)都会回调, 且回调里做**全文档**
//   `document.querySelector('.wm-layer')`。实测这是最频繁的无谓回调之一。
//   改为: 只观察水印的**直接父节点**、只监听 childList(去掉 subtree);
//   回调改用组件自身的 ref 判断 `isConnected`, 不再全文档查询。
//   ⚠️ 不能直接按建议"只观察 body 直系"—— 水印挂在 #app 内(App.vue), 不是 body 直系子节点,
//      那样会**彻底失效**(防删除守卫静默失灵, 比性能问题严重)。
function startGuard() {
  if (obs) return
  const host = (wmEl.value && wmEl.value.parentElement) || document.body
  obs = new MutationObserver(() => {
    if (show.value && (!wmEl.value || !wmEl.value.isConnected)) {
      force.value = false
      requestAnimationFrame(() => { force.value = true })
    }
  })
  obs.observe(host, { childList: true })
}

onMounted(() => {
  if (show.value) {
    makeBg()
    startGuard()
  }
})
watch(show, (v) => {
  if (v) {
    makeBg()
    startGuard()
  }
})
// 主题切换(深/浅)时重新生成背景图, 让文字色适配当前背景
watch(themeBg, () => {
  if (show.value) makeBg()
})
onBeforeUnmount(() => {
  if (obs) obs.disconnect()
})
</script>

<style scoped>
.wm-layer {
  position: fixed;
  inset: 0;
  z-index: var(--z-watermark);   /* L10: 具名 token，值见 main.css（刻意极大，须压过所有弹窗） */
  pointer-events: none;
  /* 深色/浅色背景都用较高不透明度, 保证水印可见; pointer-events:none 不挡交互 */
  /* 2026-09-30 v4.11.83 (P1-8): .18 → .10。实测(WCAG 合成计算)水印笔画把底色从 #0a0c12 抬到
     #23242a, 于是「评分/可信/概念」灰字 #9a9a9a 从 6.95:1 掉到 5.50:1, 而 `--text-dim #8a8a8a`
     掉到 **4.48:1(跌破 AA 4.5)**。降到 .10 后: 灰字 6.19:1、--text-dim 5.04:1、涨跌色 7.56/7.83:1。
     防泄露目的仍在(水印照旧覆盖全屏、在内容之上), 只是不再吃掉可读性。 */
  opacity: 0.10;
}
</style>
