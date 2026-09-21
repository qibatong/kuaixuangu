// 异动监管标签集合: 首页选股 / 竞价异动 中标记股票所属的异动监管类型。
// 数据来自异动监管接口:
//   - 严重异动(GetPianLiZhi_Index)      -> 标签 "严重异动"
//   - 热门股偏离值(GetPianLiZhi_Hot)    -> 标签 "偏离较大"
//   - 重点监控(GetYDTP_ZDJK_Today)      -> 标签 "重点监控"
// 优先级: 若一只股票既属于重点监控又属于其他类型, 显示 "重点监控"。
// 模块级单例: 任一页面刷新后全应用共享, 避免各组件重复请求。
import { ref } from 'vue'
import { kplYidongRealtime, kplYidongHot, kplYidongMonitor } from '../api/kpl'

const Label = {
  REALTIME: '严重异动',
  HOT: '偏离较大',
  MONITOR: '重点监控',
}

// 标签含义说明(悬浮 title + 术语图例共用): 报告 5-4/5-7 要求给无解释的标签补语义
const LabelTitle = {
  '严重异动': '全市场严重异动（交易所认定的涨跌幅/换手等异动）',
  '偏离较大': '热门股偏离值过大（近10日/30日累计偏离超阈值）',
  '重点监控': '当日交易所重点监控股票',
}

// code -> 标签; 按优先级覆盖: monitor 优先生效, 其次 hot, 再次 realtime
const ydTagMap = ref(new Map())
const loading = ref(false)

// 抓取异动监管接口, 汇总 code 到标签的映射(带优先级)
async function refreshYidongCodes() {
  if (loading.value) return
  loading.value = true
  try {
    const results = await Promise.all([
      kplYidongRealtime().catch(() => null),
      kplYidongHot().catch(() => null),
      kplYidongMonitor().catch(() => null)
    ])

    // monitor 优先级最高: 先填入, 后续低优先级不覆盖
    const map = new Map()
    for (const it of (results[2] && results[2].list) || []) {
      if (it && it.code) map.set(String(it.code), Label.MONITOR)
    }
    for (const it of (results[1] && results[1].list) || []) {
      const c = String(it.code)
      if (c && !map.has(c)) map.set(c, Label.HOT)
    }
    for (const it of (results[0] && results[0].list) || []) {
      const c = String(it.code)
      if (c && !map.has(c)) map.set(c, Label.REALTIME)
    }
    ydTagMap.value = map
  } finally {
    loading.value = false
  }
}

// 返回股票所属异动监管标签; 不属于任何监管返回 ''
function yidongTag(code) {
  if (code === null || code === undefined) return ''
  return ydTagMap.value.get(String(code)) || ''
}

// 返回标签的悬浮说明文案; 无标签返回 ''
function yidongTagTitle(code) {
  const t = yidongTag(code)
  return t ? (LabelTitle[t] || t) : ''
}

export function useYidongMonitor() {
  return { ydTagMap, loading, refreshYidongCodes, yidongTag, yidongTagTitle }
}