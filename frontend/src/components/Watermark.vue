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
    const text = '快选 · ' + user.username
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

// 防删除: 监控 body 子树, 水印节点被外部移除时强制重建
function startGuard() {
  if (obs) return
  obs = new MutationObserver(() => {
    if (show.value && !document.querySelector('.wm-layer')) {
      force.value = false
      requestAnimationFrame(() => { force.value = true })
    }
  })
  obs.observe(document.body, { childList: true, subtree: true })
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
  z-index: 2147483000;
  pointer-events: none;
  /* 深色/浅色背景都用较高不透明度, 保证水印可见; pointer-events:none 不挡交互 */
  opacity: 0.18;
}
</style>
