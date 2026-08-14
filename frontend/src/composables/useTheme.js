// 主题 composable: 背景明暗(body[data-bg]) + 字号(html zoom) 切换
// 持久化到账号级 prefs: { bg, font }
import { ref } from 'vue'
import { getPrefs, savePrefs } from '../api/stocks'

export const BGS = [
  { key: 'dark', label: '黑色背景', color: '#0f1219' },
  { key: 'light', label: '白色背景', color: '#f0f2f7' },
]

// 字号档位: key 存 prefs, zoom 应用到 <html>
export const FONTS = [
  { key: 'sm', label: '小号', zoom: '0.92' },
  { key: 'md', label: '标准', zoom: '1' },
  { key: 'lg', label: '大号', zoom: '1.12' },
]

const STORE_KEY = 'kuaixuan_bg'   // 本地兜底 { bg, font }, 登录后以 prefs 为准

function applyBg(key) {
  document.body.dataset.bg = key || ''
}
function applyFont(key) {
  const f = FONTS.find(x => x.key === key)
  document.documentElement.style.zoom = f ? f.zoom : '1'
}

export function useTheme() {
  const bg = ref('dark')
  const font = ref('md')

  // 启动时加载: prefs 优先, 无则读本地
  async function load() {
    let b = null, ft = null
    try {
      const raw = JSON.parse(localStorage.getItem(STORE_KEY) || '{}')
      b = raw.bg
      ft = raw.font
    } catch (e) { /* ignore */ }
    try {
      const data = await getPrefs()
      if (data.settings) {
        if (data.settings.bg) b = data.settings.bg
        if (data.settings.font) ft = data.settings.font
      }
    } catch (e) { /* 未登录/失败用本地 */ }
    if (!BGS.some(x => x.key === b)) b = 'dark'
    if (!FONTS.some(x => x.key === ft)) ft = 'md'
    bg.value = b
    font.value = ft
    applyBg(b)
    applyFont(ft)
  }

  function persist() {
    try { localStorage.setItem(STORE_KEY, JSON.stringify({ bg: bg.value, font: font.value })) } catch (e) { /* ignore */ }
    try { savePrefs({ bg: bg.value, font: font.value }).catch(() => {}) } catch (e) { /* ignore */ }
  }

  // 切换背景
  function setBg(key) {
    if (!BGS.some(x => x.key === key)) return
    bg.value = key
    applyBg(key)
    persist()
  }

  // 切换字号
  function setFont(key) {
    if (!FONTS.some(x => x.key === key)) return
    font.value = key
    applyFont(key)
    persist()
  }

  return { bg, font, setBg, setFont, load }
}
