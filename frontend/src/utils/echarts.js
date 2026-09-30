// echarts 按需注册 · **单点收口**（2026-09-30 v4.11.83 · P2-2）
//
// 背景：`StockChartModal.vue` 早就按需注册了，但 `LhbSankey` / `LhbPanel` / `StockDetailPanel`
//   仍是 `import * as echarts from 'echarts'`（全量）⇒ 产物里出现一个 1.13MB 的共享块，
//   打开任一图表弹窗都要下载它。
//
// 做法：把**全仓实际用到的图表类型 + 组件**集中在这里 `use()` 一次，其余组件统一从这里 import。
//   Rollup 会把这份注册提成同一个共享 chunk，不会重复打包。
//
// 🔴 新增图表类型 / 组件时**必须**同步加到这里 —— echarts 按需注册缺项是**静默失效**
//    （图表空白或缺少 tooltip），不会报错。全仓当前实际用到（2026-09-30 grep 实测）：
//    series: line ×8 / bar ×3 / candlestick ×1 / sankey ×1 / tree ×1
//    axis:   category / value / linear（+ dataZoom slider|inside、axisPointer cross）
//    option: grid / tooltip / dataZoom / legend / title / markLine / markArea
import * as echarts from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart, BarChart, CandlestickChart, SankeyChart, TreeChart } from 'echarts/charts'
import {
  GridComponent, TooltipComponent, DataZoomComponent, LegendComponent,
  TitleComponent, MarkLineComponent, MarkAreaComponent
} from 'echarts/components'

echarts.use([
  CanvasRenderer,
  LineChart, BarChart, CandlestickChart, SankeyChart, TreeChart,
  GridComponent, TooltipComponent, DataZoomComponent, LegendComponent,
  TitleComponent, MarkLineComponent, MarkAreaComponent
])

export default echarts
