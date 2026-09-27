<template>
  <div ref="chartRef" style="width:100%;height:600px"></div>
</template>

<script setup>
import { onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { kplLhbDetail } from '../api/kpl'

const props = defineProps({ list: { type: Array, default: () => [] } })
const chartRef = ref(null)
let chart = null

async function buildSankey() {
  if (!chartRef.value) return
  chart = chart || echarts.init(chartRef.value)
  if (!props.list.length) {
    chart.clear()
    chart.setOption({ title: { text: '暂无数据', left: 'center', top: 'center', textStyle: { color: '#888' } } })
    return
  }
  const nodesMap = new Map()
  const links = []
  nodesMap.set('龙虎榜', { name: '龙虎榜', itemStyle: { color: '#ff9500' } })

  // 取前20只票的明细
  const stocks = props.list.slice(0, 20)
  for (const s of stocks) {
    try {
      const d = await kplLhbDetail(s.code)
      if (!d || !d.detail) continue
      const det = d.detail
      // 个股节点
      const stockColor = s.change > 0 ? '#d03030' : '#2ea82e'
      nodesMap.set(s.code, { name: `${s.name} ${s.code}`, itemStyle: { color: stockColor } })
      // 买入营业部 -> 个股
      for (const b of (det.buyList || []).slice(0, 5)) {
        const key = 'B_' + b.name
        if (!nodesMap.has(key)) nodesMap.set(key, { name: b.name, itemStyle: { color: '#555' } })
        links.push({ source: b.name, target: `${s.name} ${s.code}`, value: Math.round(b.buy / 1e4) })
      }
    } catch (e) {}
  }

  chart.clear()
  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item', triggerOn: 'mousemove' },
    series: [{
      type: 'sankey',
      left: 40, right: 120, top: 20, bottom: 20,
      nodeWidth: 14, nodeGap: 8,
      data: Array.from(nodesMap.values()),
      links: links,
      lineStyle: { color: 'gradient', curveness: 0.5, opacity: 0.4 },
      label: { color: '#ddd', fontSize: 11 },
    }]
  })
}

onMounted(() => { setTimeout(buildSankey, 100) })
watch(() => props.list, () => buildSankey())
window.addEventListener('resize', () => chart && chart.resize())
</script>
