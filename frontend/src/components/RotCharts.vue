<template>
  <div class="rot-charts">
    <div class="rot-chart-block">
      <div class="rot-chart-title">板块强度(每日第 1 名)</div>
      <div class="rot-svg-wrap" v-html="strengthLineSvg"></div>
    </div>
    <div class="rot-chart-block">
      <div class="rot-chart-title">板块量能(每日第 1 名成交额, 亿元)</div>
      <div class="rot-svg-wrap" v-html="amountBarSvg"></div>
    </div>
  </div>
  <div class="rot-windows">
    <div class="rot-chart-title">多窗口排名(不同时间窗口 Top10 累计强度, 虚线越大窗口)</div>
    <div class="rot-svg-wrap" v-html="windowLineSvg"></div>
    <div class="rot-window-legend">
      <span v-for="(w, idx) in windows" :key="w.window" :style="{ color: windowColors[idx] }">
        <i class="fa fa-circle"></i> 近 {{ w.window }} 日
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  dates: { type: Array, default: () => [] },
  rotMap: { type: Object, default: () => ({}) },
  windows: { type: Array, default: () => [] },
  commonNames: { type: Array, default: () => [] }
})

const windowColors = ['#E24B4A', '#EF9F27', '#378ADD', '#888780']

// 强度趋势线: 每日第 1 名板块强度连线
const strengthLineSvg = computed(() => {
  const days = props.dates
  const data = days.map(d => {
    const b = (props.rotMap[d] || []).find(x => x.rank === 1)
    return b ? Number(b.strength) || 0 : 0
  })
  if (!data.length || data.every(x => x === 0)) {
    return '<svg viewBox="0 0 600 120" preserveAspectRatio="none" class="rot-svg"><text x="300" y="60" text-anchor="middle" fill="#888">暂无强度数据</text></svg>'
  }
  const W = 600, H = 120, padL = 40, padR = 10, padT = 10, padB = 18
  const maxV = Math.max(...data, 1)
  const minV = Math.min(...data, 0)
  const range = maxV - minV || 1
  const xs = data.map((_, i) => padL + i * (W - padL - padR) / Math.max(1, data.length - 1))
  const ys = data.map(v => padT + (H - padT - padB) * (1 - (v - minV) / range))
  const points = xs.map((x, i) => `${x},${ys[i]}`).join(' ')
  const labels = data.map((v, i) => `<text x="${xs[i]}" y="${ys[i] - 4}" font-size="9" fill="#E24B4A" text-anchor="middle">${Math.round(v)}</text>`).join('')
  const axisY = `<line x1="${padL}" y1="${padT}" x2="${padL}" y2="${H - padB}" stroke="#888" stroke-width="0.5"/>` +
                `<line x1="${padL}" y1="${H - padB}" x2="${W - padR}" y2="${H - padB}" stroke="#888" stroke-width="0.5"/>`
  const xLabels = days.map((d, i) => `<text x="${xs[i]}" y="${H - 4}" font-size="8" fill="#888" text-anchor="middle">${d.slice(5)}</text>`).join('')
  return `<svg viewBox="0 0 600 120" preserveAspectRatio="none" class="rot-svg">` +
         `<polyline points="${points}" fill="none" stroke="#E24B4A" stroke-width="1.5"/>` + labels + axisY + xLabels +
         `<circle cx="${xs[0]}" cy="${ys[0]}" r="2.5" fill="#E24B4A"/>` +
         `<circle cx="${xs[xs.length - 1]}" cy="${ys[ys.length - 1]}" r="2.5" fill="#E24B4A"/>` +
         `</svg>`
})

// 量能柱状: 每日第 1 名成交额(亿元)
const amountBarSvg = computed(() => {
  const days = props.dates
  const data = days.map(d => {
    const b = (props.rotMap[d] || []).find(x => x.rank === 1)
    return b ? Number(b.amount) / 1e8 : 0
  })
  if (!data.length || data.every(x => x === 0)) {
    return '<svg viewBox="0 0 600 120" preserveAspectRatio="none" class="rot-svg"><text x="300" y="60" text-anchor="middle" fill="#888">暂无量能数据</text></svg>'
  }
  const W = 600, H = 120, padL = 40, padR = 10, padT = 10, padB = 18
  const maxV = Math.max(...data, 1)
  const barW = Math.max(4, (W - padL - padR) / data.length - 2)
  let bars = ''
  for (let i = 0; i < data.length; i++) {
    const x = padL + i * (W - padL - padR) / data.length + 1
    const h = data[i] / maxV * (H - padT - padB)
    const y = H - padB - h
    bars += `<rect x="${x}" y="${y}" width="${barW}" height="${h}" fill="#378ADD"/>`
    bars += `<text x="${x + barW / 2}" y="${y - 2}" font-size="8" fill="#378ADD" text-anchor="middle">${Math.round(data[i])}</text>`
  }
  const axisY = `<line x1="${padL}" y1="${padT}" x2="${padL}" y2="${H - padB}" stroke="#888" stroke-width="0.5"/>` +
                `<line x1="${padL}" y1="${H - padB}" x2="${W - padR}" y2="${H - padB}" stroke="#888" stroke-width="0.5"/>`
  const xLabels = days.map((d, i) => {
    const x = padL + i * (W - padL - padR) / data.length + barW / 2 + 1
    return `<text x="${x}" y="${H - 4}" font-size="8" fill="#888" text-anchor="middle">${d.slice(5)}</text>`
  }).join('')
  return `<svg viewBox="0 0 600 120" preserveAspectRatio="none" class="rot-svg">` + bars + axisY + xLabels + `</svg>`
})

// 多窗口排名折线: X=板块(近 N 日 Top 板块并集), Y=窗口累计强度
const windowLineSvg = computed(() => {
  const wins = props.windows
  if (!wins.length) {
    return '<svg viewBox="0 0 600 220" preserveAspectRatio="none" class="rot-svg"><text x="300" y="100" text-anchor="middle" fill="#888">暂无多窗口数据</text></svg>'
  }
  const names = props.commonNames.slice(0, 6)
  if (!names.length) {
    return '<svg viewBox="0 0 600 220" preserveAspectRatio="none" class="rot-svg"><text x="300" y="100" text-anchor="middle" fill="#888">暂无多窗口数据</text></svg>'
  }
  let maxV = 0
  for (const w of wins) {
    for (const t of (w.top || [])) {
      if ((t.strengthSum || 0) > maxV) maxV = t.strengthSum
    }
  }
  if (maxV <= 0) {
    return '<svg viewBox="0 0 600 220" preserveAspectRatio="none" class="rot-svg"><text x="300" y="100" text-anchor="middle" fill="#888">暂无强度数据</text></svg>'
  }
  const W = 600, H = 220, padL = 38, padR = 10, padT = 22, padB = 64
  let lines = ''
  const dashes = ['', '6,4', '3,3', '1,3']
  for (let wi = 0; wi < wins.length; wi++) {
    const top = wins[wi].top || []
    const color = windowColors[wi % windowColors.length]
    const pts = names.map((nm, ni) => {
      const t = top.find(x => x.name === nm)
      const v = t ? t.strengthSum : 0
      const x = padL + ni * (W - padL - padR) / Math.max(1, names.length - 1)
      const y = padT + (1 - v / maxV) * (H - padT - padB - 8) + 4
      return `${x},${y}`
    }).join(' ')
    lines += `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5" stroke-dasharray="${dashes[wi % 4]}"/>`
    lines += pts.split(' ').map((p) => {
      const c = p.split(',')
      return `<circle cx="${c[0]}" cy="${c[1]}" r="3" fill="${color}"/>`
    }).join('')
  }
  const axisX = `<line x1="${padL}" y1="${padT}" x2="${padL}" y2="${H - padB}" stroke="#888" stroke-width="0.5"/>`
              + `<line x1="${padL}" y1="${H - padB}" x2="${W - padR}" y2="${H - padB}" stroke="#888" stroke-width="0.5"/>`
  const yLabels = [0, 0.5, 1.0].map((p) => {
    const v = Math.round(maxV * p)
    const y = padT + (1 - p) * (H - padT - padB - 8) + 4
    return `<text x="${padL - 6}" y="${y + 3}" font-size="8" fill="#888" text-anchor="end">${v}</text>`
  }).join('')
  const xLabels = names.map((nm, ni) => {
    const x = padL + ni * (W - padL - padR) / Math.max(1, names.length - 1)
    return `<text x="${x}" y="${H - padB + 14}" font-size="9" fill="#888" text-anchor="middle">${nm}</text>`
  }).join('')
  return `<svg viewBox="0 0 600 220" preserveAspectRatio="none" class="rot-svg">` + axisX + yLabels + xLabels + lines + `</svg>`
})
</script>
