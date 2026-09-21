// 主题 composable: 背景明暗(body[data-bg]) + 字号(html 根字号百分比) + 字体族(body[data-font]) 切换
// 持久化到账号级 prefs: { bg, font, fontFam }
import { ref } from 'vue'
import { getPrefs, savePrefs } from '../api/stocks'

export const BGS = [
  { key: 'dark', label: '黑色背景', color: '#0f1219' },
  { key: 'light', label: '白色背景', color: '#f0f2f7' },
]

// 字号档位: key 存 prefs, fontSize 应用到 <html> (根字号百分比, 全站字号已用 rem 描述).
// 2026-09-21 由非标准 html.zoom 迁移到标准 font-size —— zoom 在部分浏览器(旧 Firefox 等)
// 支持不一致, 且是物理整体缩放(会把固定列宽/卡片一起放大); font-size+rem 只缩放文字.
// 档位百分比与旧 zoom 值一致(92/100/112), 切换视觉零漂移。
export const FONTS = [
  { key: 'sm', label: '小号', fsRoot: '92%' },
  { key: 'md', label: '标准', fsRoot: '100%' },
  { key: 'lg', label: '大号', fsRoot: '112%' },
]

// 字体族: 控制中文正文显示的"主角字体", 对应 body[data-font=xxx].
// 数字列/代码列固定霞鹜等宽(不随此处切换), 保证表格对齐不歪.
// 所有字体都是 SIL OFL 1.1 授权, 完全免费商用, 无任何风险.
export const FONT_FAMILIES = [
  { key: 'lxgw',  label: '霞鹜等宽', desc: '0带斜杠·数字清晰', family: '"LXGW WenKai Mono"' },
  { key: 'sans',  label: '思源黑体', desc: '现代无衬线',         family: '"Noto Sans SC"' },
  { key: 'serif', label: '思源宋体', desc: '传统有衬线',         family: '"Noto Serif SC"' },
]

const STORE_KEY = 'kuaixuan_bg'   // 本地兜底 { bg, font, fontFam }, 登录后以 prefs 为准

// 系统配色偏好: 未登录/无明确偏好时, 跟随操作系统明暗主题作为初始值 (2026-09-21)
// 此前一律强推深色; 现在首次访问的用户若系统是浅色, 自动落到浅色主题。
function systemBg() {
  try {
    if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) return 'light'
  } catch (e) { /* ignore */ }
  return 'dark'
}

// 模块级单例 state: 避免多次 useTheme() 各持独立 ref, 导致跨组件读不到最新主题
const bg      = ref('dark')       // 默认黑色(深色)主题, 2026-08-22 主人确认
const font    = ref('md')         // 默认标准字号
const fontFam = ref('sans')       // 默认思源黑体 (2026-08-23 主人 A/B 对比后确认, 之前默认 lxgw 改 sans)

// 可选字体按需加载: 默认正文 = 思源黑体(Noto Sans SC, 在 main.js 全局加载)。
// 另外两个可选字体 —— 霞鹜等宽(lxgw, 数字列「去楷体」后已 inherit, 不再默认用) 与
// 思源宋体(serif) 都只在设置里手动切换时才需要。若在 main.js 全局 import, 会连累所有
// 用户首屏下载数百个 @font-face + 对应 woff(两字体合计约 22MB)。这里改为首次切换时
// 才动态 import, Vite 会把它们拆成独立 chunk, 默认用户零开销。
const _FONT_CSS = {
  lxgw: [
    () => import('lxgw-wenkai-webfont/lxgwwenkaimono-regular.css'),
    () => import('lxgw-wenkai-webfont/lxgwwenkaimono-bold.css'),
  ],
  serif: [
    () => import('@fontsource/noto-serif-sc/400.css'),
    () => import('@fontsource/noto-serif-sc/700.css'),
  ],
}
const _fontLoaded = {}
function ensureFontCss(key) {
  if (_fontLoaded[key] || !_FONT_CSS[key]) return
  _fontLoaded[key] = true
  // 动态 import CSS(副作用加载), 触发时才开始拉字体; 失败不阻塞主流程
  _FONT_CSS[key].forEach((fn) => fn().catch(() => {}))
}

function applyBg(key) {
  document.body.dataset.bg = key || ''
}
function applyFont(key) {
  const f = FONTS.find(x => x.key === key)
  // 标准 CSS: 根字号百分比缩放(全站 font-size 已迁移 rem, 随根字号联动)
  document.documentElement.style.fontSize = f ? f.fsRoot : '100%'
}
function applyFontFam(key) {
  const ff = FONT_FAMILIES.find(x => x.key === key)
  document.body.dataset.font = ff ? ff.key : 'lxgw'
}

export function useTheme() {

  // 启动时加载: prefs 优先, 无则读本地
  async function load() {
    let b = null, ft = null, ff = null
    try {
      const raw = JSON.parse(localStorage.getItem(STORE_KEY) || '{}')
      b  = raw.bg
      ft = raw.font
      ff = raw.fontFam
    } catch (e) { /* ignore */ }
    try {
      const data = await getPrefs()
      const settings = data && data.settings
      if (settings && typeof settings === 'object') {
        // 已登录且拿到偏好: 有字段用之; 无字段说明账号从未设过 ->
        // 忽略 localStorage 残留, 强制默认
        b  = BGS.some(x => x.key === settings.bg)                ? settings.bg        : systemBg()
        ft = FONTS.some(x => x.key === settings.font)            ? settings.font      : 'md'
        ff = FONT_FAMILIES.some(x => x.key === settings.fontFam) ? settings.fontFam   : 'sans'   // 2026-08-23 默认改思源黑体
      } else {
        // 服务器返回空偏好(新账号) -> 背景跟随系统, 字号/字体用默认
        b  = systemBg()
        ft = 'md'
        ff = 'sans'   // 2026-08-23 默认改思源黑体
      }
    } catch (e) { /* 未登录/请求失败: 保留本地兜底值 */ }
    if (!BGS.some(x => x.key === b))                b  = systemBg()
    if (!FONTS.some(x => x.key === ft))             ft = 'md'
    if (!FONT_FAMILIES.some(x => x.key === ff))     ff = 'sans'   // 2026-08-23 最终兜底也改成思源黑体
    bg.value      = b
    font.value    = ft
    fontFam.value = ff
    applyBg(b)
    applyFont(ft)
    applyFontFam(ff)
    if (ff !== 'sans') ensureFontCss(ff)   // 账号偏好是可选字体(lxgw/serif)时, 恢复加载对应字体
  }

  function persist() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({
        bg: bg.value, font: font.value, fontFam: fontFam.value
      }))
    } catch (e) { /* ignore */ }
    try {
      savePrefs({ bg: bg.value, font: font.value, fontFam: fontFam.value }).catch(() => {})
    } catch (e) { /* ignore */ }
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

  // 切换字体族
  function setFontFam(key) {
    if (!FONT_FAMILIES.some(x => x.key === key)) return
    fontFam.value = key
    applyFontFam(key)
    ensureFontCss(key)   // 切到可选字体(lxgw/serif)时才按需加载; sans 已全局加载自动跳过
    persist()
  }

  return { bg, font, fontFam, setBg, setFont, setFontFam, load }
}
