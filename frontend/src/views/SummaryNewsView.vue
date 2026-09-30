<template>
  <div class="page-shell">
    <h1 class="visually-hidden">大V资讯</h1>
    <div class="sn-panel">
      <!-- 顶部: 标题 + 日期选择 -->
      <div class="sn-head">
        <div class="sn-title-row">
          <div class="sn-title">
            <i class="fa fa-newspaper-o sn-icon"></i>
            大V资讯 · 飞书群消息总结
            <span class="sn-sub">每日 凌晨盘后 / 早间 / 午间 / 收盘 四个时段</span>
          </div>
          <div class="sn-toolbar">
            <button class="rot-date-btn" title="选择日期回看往日总结">
              <i class="fa fa-calendar"></i> {{ selDate || '选择日期' }}
              <input
                v-model="selDate"
                type="date"
                class="rot-date-hidden"
                :max="maxDate"
                title="回看往日总结"
                @change="onDateChange"
                @click.stop
              >
            </button>
            <button class="rot-reset-btn" title="回到最新" @click="resetLatest">
              <i class="fa fa-bolt"></i>
            </button>
          </div>
        </div>
      </div>

      <!-- 加载 / 空态 -->
      <div v-if="loading" class="loading-placeholder">
        <div class="spinner"></div>
        <div>加载大V资讯...</div>
      </div>
      <div v-else-if="!days.length" class="empty-state">
        暂无总结，定时任务生成后将自动出现在这里
      </div>

      <!-- 日期列表 -->
      <template v-else>
        <!-- 当前选中日期 -->
        <div class="sn-day">
          <div class="sn-day-badge">{{ curDay.date }}</div>
          <div v-if="!curDay.items.length" class="sn-slot empty">该日暂无总结</div>
          <!-- 4 个时段卡片 -->
          <div
            v-for="it in curDay.items"
            :key="it.id"
            class="sn-slot"
            :class="'slot-' + it.slot"
          >
            <div class="sn-slot-left">
              <span class="sn-slot-tag">{{ it.label }}</span>
              <div class="sn-slot-info">
                <div class="sn-slot-title">{{ it.title }}</div>
                <div class="sn-slot-meta">
                  <i class="fa fa-clock-o"></i> {{ it.time }}
                  <template v-if="it.pages"><i class="fa fa-file-text-o"></i> {{ it.pages }} 页</template>
                  <template v-if="it.size"><i class="fa fa-database"></i> {{ it.size }}</template>
                </div>
              </div>
            </div>
            <a class="sn-view-btn" :href="it.url" target="_blank" title="在线查看/下载">
              <i class="fa fa-external-link"></i> 查看
            </a>
          </div>
        </div>

        <!-- 往日列表(点击切换日期) -->
        <div class="sn-past">
          <div class="sn-past-title">往日总结</div>
          <div class="sn-past-list">
            <button
              v-for="d in pastDays"
              :key="d.date"
              class="sn-past-day"
              :class="{ active: d.date === selDate }"
              @click="selDate = d.date; onDateChange()"
            >
              {{ d.date }}
              <span class="sn-past-n">{{ d.items.length }}</span>
            </button>
            <div v-if="!pastDays.length" class="sn-past-empty">暂无更早的记录</div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { summaryHistory } from '../api/summary'

const loading = ref(false)
const days = ref([])
const selDate = ref('')

const maxDate = computed(() => (days.value.length ? days.value[0].date : ''))
const curDay = computed(() => days.value.find(d => d.date === selDate.value) ||
  { date: selDate.value || '', items: [] })
const pastDays = computed(() => days.value.filter(d => d.date !== selDate.value))

function onDateChange() {
  // 输入框可能为空 → 回退最新
  if (!selDate.value && days.value.length) selDate.value = days.value[0].date
}

function resetLatest() {
  if (days.value.length) selDate.value = days.value[0].date
}

async function load() {
  loading.value = true
  try {
    const data = await summaryHistory()
    days.value = data.days || []
    if (days.value.length) selDate.value = days.value[0].date
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.sn-panel {
  background: var(--bg-card, #fff);
  border-radius: 14px;
  padding: 16px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
}
.sn-head { margin-bottom: 14px; }
.sn-title-row {
  display: flex; align-items: center; justify-content: space-between;
  flex-wrap: wrap; gap: 10px;
}
.sn-title { font-size: 1.0625rem; font-weight: 700; color: var(--text-main, #1a1a1a); }
.sn-icon { color: #b83010; margin-right: 6px; }
.sn-sub {
  font-size: 0.75rem; font-weight: 400; color: var(--text-muted, #888);
  margin-left: 10px;
}
.sn-toolbar { display: flex; gap: 8px; }
.rot-date-btn {
  position: relative; display: inline-flex; align-items: center; gap: 6px;
  background: var(--bg-input, #f2f3f5); border: 1px solid var(--border, #e5e6eb);
  border-radius: 8px; padding: 6px 12px; font-size: 0.8125rem;
  color: var(--text-main, #333); cursor: pointer; overflow: hidden;
}
.rot-date-hidden {
  position: absolute; inset: 0; width: 100%; height: 100%;
  opacity: 0; cursor: pointer;
}
.rot-reset-btn {
  background: var(--bg-input, #f2f3f5); border: 1px solid var(--border, #e5e6eb);
  border-radius: 8px; padding: 6px 12px; cursor: pointer; color: var(--text-main, #333);
}
.sn-day { border-top: 2px solid #b83010; padding-top: 12px; }
.sn-day-badge {
  display: inline-block; background: #b83010; color: #fff; font-size: 0.8125rem;
  font-weight: 600; padding: 4px 14px; border-radius: 14px; margin-bottom: 12px;
}
.sn-slot {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  background: var(--bg-input, #f7f8fa); border: 1px solid var(--border, #e8e9ed);
  border-left: 4px solid #999; border-radius: 10px; padding: 12px 14px; margin-bottom: 10px;
}
.sn-slot.slot-night { border-left-color: #4a4a4a; }
.sn-slot.slot-morning { border-left-color: #f5a623; }
.sn-slot.slot-noon { border-left-color: #34a853; }
.sn-slot.slot-close { border-left-color: #d93025; }
.sn-slot.empty { justify-content: center; color: var(--text-muted, #999); border-left-color: #ddd; }
.sn-slot-left { display: flex; align-items: center; gap: 12px; min-width: 0; }
.sn-slot-tag {
  flex-shrink: 0; font-size: 0.75rem; font-weight: 700; color: #fff;
  background: #666; border-radius: 12px; padding: 3px 10px;
}
.slot-night .sn-slot-tag { background: #4a4a4a; }
.slot-morning .sn-slot-tag { background: #f5a623; }
.slot-noon .sn-slot-tag { background: #34a853; }
.slot-close .sn-slot-tag { background: #d93025; }
.sn-slot-info { min-width: 0; }
.sn-slot-title {
  font-size: 0.875rem; font-weight: 600; color: var(--text-main, #1a1a1a);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.sn-slot-meta { font-size: 0.75rem; color: var(--text-muted, #888); margin-top: 3px; display: flex; gap: 10px; flex-wrap: wrap; }
.sn-view-btn {
  flex-shrink: 0; background: #b83010; color: #fff; font-size: 0.8125rem;
  text-decoration: none; padding: 7px 14px; border-radius: 16px; font-weight: 600;
}
.sn-view-btn:hover { opacity: 0.88; }
.sn-past { margin-top: 16px; border-top: 1px dashed var(--border, #e0e0e0); padding-top: 12px; }
.sn-past-title { font-size: 0.8125rem; font-weight: 600; color: var(--text-muted, #888); margin-bottom: 8px; }
.sn-past-list { display: flex; flex-wrap: wrap; gap: 8px; }
.sn-past-day {
  background: var(--bg-input, #f2f3f5); border: 1px solid var(--border, #e5e6eb);
  border-radius: 8px; padding: 5px 10px; font-size: 0.75rem; cursor: pointer;
  color: var(--text-main, #333); display: inline-flex; align-items: center; gap: 6px;
}
.sn-past-day.active { background: #b83010; color: #fff; border-color: #b83010; }
.sn-past-n {
  background: rgba(0, 0, 0, 0.12); border-radius: 9px; font-size: 0.75rem;
  padding: 0 6px; line-height: 16px;
}
.sn-past-day.active .sn-past-n { background: rgba(255, 255, 255, 0.25); }
.sn-past-empty { font-size: 0.75rem; color: var(--text-muted, #999); }
</style>
