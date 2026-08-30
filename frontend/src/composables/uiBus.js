// 全局 UI 交互总线(超轻量 reactive store)
// 目前用于: 点击股票 → 触发图表弹窗
// 用法: 表格中调用 openStockChart(code, name); StockView 中 watch chartModal 控制弹框
import { reactive } from 'vue'

export const uiBus = reactive({
  chartModal: { visible: false, code: '', name: '' },
  // 计数器变化也能触发 watch(防止相同 stock code 连续点击无事件)
  _seq: 0,
})

export function openStockChart(code, name = '') {
  if (!code) return
  uiBus.chartModal.code = String(code)
  uiBus.chartModal.name = String(name || '')
  uiBus.chartModal.visible = true
  uiBus._seq++
}

export function closeStockChart() {
  uiBus.chartModal.visible = false
}
