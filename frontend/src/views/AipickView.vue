<template>
  <div class="page-shell">
    <!-- 会员门禁(2026-08-27): AI 竞价预测仅 VIP/付费会员可用 -->
    <VipGate v-if="!user.isVipOrPaid" title="AI竞价预测" :required-level="1" />

    <template v-else>
      <div class="ap-head">
        <span class="ap-title"><i class="fa fa-robot"></i> AI竞价预测</span>
        <span class="ap-sub">9:25 竞价快照出榜 · 仅限 VIP/付费会员</span>
        <span class="ap-head-spacer"></span>
        <!-- 日期回看: 下拉/选择历史报告 -->
        <select v-model="selDate" class="ap-date" title="选择历史预测报告" @change="loadReport">
          <option value="">最新({{ latestDate || '--' }})</option>
          <option v-for="d in dates" :key="d" :value="d">{{ d }}</option>
        </select>
        <button class="rot-reset-btn" title="回到最新" @click="selDate=''; loadReport()"><i class="fa fa-bolt"></i></button>
      </div>

      <div class="ap-panel">
        <div v-if="loading" class="loading-placeholder"><div class="spinner"></div><div>加载预测报告...</div></div>
        <div v-else-if="empty" class="empty-state">暂无预测报告，交易日 9:30 前自动生成</div>
        <iframe
          v-else-if="html"
          ref="frame"
          class="ap-frame"
          :srcdoc="html"
          sandbox="allow-same-origin"
          @load="autoHeight"
        ></iframe>
      </div>
    </template>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useUserStore } from '../stores/user'
import VipGate from '../components/VipGate.vue'
import { aipickDates, aipickReportUrl } from '../api/aipick'

const user = useUserStore()
const dates = ref([])
const latestDate = ref('')
const selDate = ref('')
const html = ref('')
const loading = ref(false)
const empty = ref(false)
const frame = ref(null)

async function loadDates() {
  try {
    const d = await aipickDates()
    dates.value = d.dates || []
    latestDate.value = dates.value[0] || ''
  } catch (e) { /* 403/网络由 request 处理 */ }
}

async function loadReport() {
  loading.value = true
  empty.value = false
  html.value = ''
  try {
    const h = await aipickReportUrl(selDate.value)
    html.value = h
  } catch (e) {
    empty.value = true
  } finally {
    loading.value = false
    // 等待渲染后自适应高度
    requestAnimationFrame(() => setTimeout(autoHeight, 50))
  }
}

// srcdoc 与父页面同源, 可直接读取内容高度实现自适应
function autoHeight() {
  const f = frame.value
  if (!f) return
  try {
    const d = f.contentDocument
    if (d && d.body) {
      f.style.height = (d.body.scrollHeight + 12) + 'px'
    }
  } catch (e) {
    f.style.height = '80vh'
  }
}

onMounted(() => {
  loadDates()
  loadReport()
})
</script>

<style scoped>
.ap-head {
  display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 12px;
}
.ap-title { font-size: 20px; font-weight: 700; color: #ffe0a0; }
.ap-title .fa { color: #ffb400; }
.ap-sub { color: var(--text-muted); font-size: 13px; }
.ap-head-spacer { flex: 1; }
.ap-date {
  background: var(--bg-hover); color: var(--text-main); border: 1px solid var(--border-soft);
  border-radius: 8px; padding: 6px 10px; font-size: 13px;
}
.ap-panel {
  background: var(--bg-hover); border: 1px solid var(--border-soft);
  border-radius: 10px; padding: 6px; overflow: hidden;
}
.ap-frame {
  width: 100%; height: 80vh; border: 0; display: block; background: transparent;
}
.loading-placeholder { text-align: center; padding: 40px; color: var(--text-muted); }
.spinner {
  width: 28px; height: 28px; border: 3px solid rgba(255, 180, 0, 0.3);
  border-top-color: #ffb400; border-radius: 50%;
  animation: spin 0.8s linear infinite; margin: 0 auto 10px;
}
@keyframes spin { to { transform: rotate(360deg); } }
.empty-state { text-align: center; padding: 40px; color: var(--text-muted); }

@media (max-width: 768px) {
  .ap-title { font-size: 17px; }
  .ap-sub { font-size: 11px; width: 100%; }
}
</style>