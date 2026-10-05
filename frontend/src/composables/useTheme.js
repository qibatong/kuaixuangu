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
// 2026-10-05 (S5): 新增 **system(系统字体)** 并设为默认 —— 零下载。
//   原先默认 sans(思源黑体) 走全局静态 import, 首访要下 459KB 中文子集;
//   现在只有**显式选中** sans/serif/lxgw 时才动态加载对应网络字体, 默认路径零字体请求。
export const FONT_FAMILIES = [
  { key: 'system', label: '系统字体', desc: '秒开·推荐',        family: '系统中文黑体' },
  { key: 'sans',  label: '思源黑体', desc: '现代无衬线·需下载', family: '"Noto Sans SC"' },
  { key: 'serif', label: '思源宋体', desc: '传统有衬线·需下载', family: '"Noto Serif SC"' },
  { key: 'lxgw',  label: '霞鹜等宽', desc: '0带斜杠·数字清晰', family: '"LXGW WenKai Mono"' },
]

const STORE_KEY = 'kuaixuan_bg'   // 本地兜底 { bg, font, fontFam }, 登录后以 prefs 为准
// 2026-10-05: 「字体族统一到系统字体」的一次性迁移标记（打上后永不再强行覆盖用户选择）
const FONT_UNIFIED_KEY = 'kuaixuan_font_unified_v1'

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
const fontFam = ref('system')     // 2026-10-05 (S5): 默认「系统字体」(零下载); 原默认 sans 见下方说明

// 可选字体按需加载: 默认正文 = 思源黑体(Noto Sans SC, 在 main.js 全局加载)。
// 另外两个可选字体 —— 霞鹜等宽(lxgw, 数字列「去楷体」后已 inherit, 不再默认用) 与
// 思源宋体(serif) 都只在设置里手动切换时才需要。若在 main.js 全局 import, 会连累所有
// 用户首屏下载数百个 @font-face + 对应 woff(两字体合计约 22MB)。这里改为首次切换时
// 才动态 import, Vite 会把它们拆成独立 chunk, 默认用户零开销。
const _FONT_CSS = {
  // 2026-09-30 v4.11.83 (P2-3 部分采纳): 可选字体只保留 **regular** 一个字重。
  //   理由(与体检报告建议不同, 这里修正): 这两个字体的 woff2 本来就是**懒加载**(选了才下载),
  //   对用户首屏零开销 ⇒ 整族删掉等于拿功能换部署体积, 不划算。但它们各自 196 个 unicode 子集、
  //   粗体变体再翻一倍(serif 6.7MB→3.3MB、lxgw 9.4MB→4.7MB), 而这些**构造产物**每次部署都要传/占盘。
  //   只留 regular: 部署体积 −8MB, 且粗体由浏览器**合成**(对装饰性可选字体完全够用), 功能不丢。
  lxgw: [
    () => import('lxgw-wenkai-webfont/lxgwwenkaimono-regular.css'),
  ],
  serif: [
    () => import('@fontsource/noto-serif-sc/400.css'),
  ],
  // 2026-10-05 (S5): 思源黑体由「main.js 全局静态 import」改为**按需加载** ——
  //   从此不再进入口 CSS; 只有用户在「我的会员 → 字体」里主动选它才下载(400 + 700 两套子集)。
  //   ⚠️ 保留 700: 表格/标题大量用 font-weight:700, 缺了会由浏览器合成粗体(中文合成发糊, 观感差)。
  sans: [
    () => import('@fontsource/noto-sans-sc/400.css'),
    () => import('@fontsource/noto-sans-sc/700.css'),
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
  document.body.dataset.font = ff ? ff.key : 'system'   // 2026-10-05 (S5): 兜底也改「系统字体」
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
        // 2026-10-05 (S5): 账号从未设过字体族时默认「系统字体」(零下载网络字体)。
        //   老用户若**显式**选过 sans/serif/lxgw, settings.fontFam 有值 ⇒ 尊重其选择(按需加载)。
        ff = FONT_FAMILIES.some(x => x.key === settings.fontFam) ? settings.fontFam   : 'system'
      } else {
        // 服务器返回空偏好(新账号) -> 背景跟随系统, 字号/字体用默认
        b  = systemBg()
        ft = 'md'
        ff = 'system'
      }
    } catch (e) { /* 未登录/请求失败: 保留本地兜底值 */ }
    if (!BGS.some(x => x.key === b))                b  = systemBg()
    if (!FONTS.some(x => x.key === ft))             ft = 'md'
    if (!FONT_FAMILIES.some(x => x.key === ff))     ff = 'system'
    bg.value      = b
    font.value    = ft
    fontFam.value = ff
    applyBg(b)
    applyFont(ft)
    applyFontFam(ff)
    // 2026-10-05 (主人指令「统一」): **一次性**把所有账号的字体族收敛到「系统字体」——
    //   包含此前显式选过网络字体(sans/serif/lxgw)的老账号(它们的 settings.fontFam 有值,
    //   上面会"尊重其选择" ⇒ 这里必须再兜一层)。只做一次(本地打标记), 之后用户在本页的
    //   手动切换照常生效、不会被反复覆盖。收益: 老账号也不再下载数百个 @font-face/woff。
    try {
      if (localStorage.getItem(FONT_UNIFIED_KEY) !== '1') {
        if (ff !== 'system') {
          ff = 'system'
          fontFam.value = 'system'
          applyFontFam('system')
          persist()          // 写回本地 + 账号偏好(未登录时只写本地)
        }
        localStorage.setItem(FONT_UNIFIED_KEY, '1')
      }
    } catch (e) { /* 隐私模式等: 收敛失败不影响渲染, 只是该次仍按账号原偏好 */ }
    // 2026-10-05 (S5): 只有账号偏好是**网络字体**(sans/serif/lxgw)时加载; system 零请求
    if (ff !== 'system') ensureFontCss(ff)
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
