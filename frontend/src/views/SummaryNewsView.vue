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
  border-radius: var(--r-lg);
  padding: var(--s4);
  box-shadow: var(--sh-1);
}
.sn-head { margin-bottom: var(--s4); }
.sn-title-row {
  display: flex; align-items: center; justify-content: space-between;
  flex-wrap: wrap; gap: var(--s2);
}
.sn-title { font-size: var(--fs-lg); font-weight: 700; color: var(--text-main, #1a1a1a); }
.sn-icon { color: var(--brand-deep); margin-right: var(--s2); }
.sn-sub {
  font-size: var(--fs-xs); font-weight: 400; color: var(--text-muted, var(--text-muted));
  margin-left: var(--s2);
}
.sn-toolbar { display: flex; gap: var(--s2); }
.rot-date-btn {
  position: relative; display: inline-flex; align-items: center; gap: var(--s2);
  background: var(--bg-input, #f2f3f5); border: 1px solid var(--border, #e5e6eb);
  border-radius: var(--r-md); padding: var(--s2) var(--s3); font-size: var(--fs-sm);
  color: var(--text-main, #333); cursor: pointer; overflow: hidden;
}
.rot-date-hidden {
  position: absolute; inset: 0; width: 100%; height: 100%;
  opacity: 0; cursor: pointer;
}
.rot-reset-btn {
  background: var(--bg-input, #f2f3f5); border: 1px solid var(--border, #e5e6eb);
  border-radius: var(--r-md); padding: var(--s2) var(--s3); cursor: pointer; color: var(--text-main, #333);
}
.sn-day { border-top: 2px solid var(--brand-deep); padding-top: var(--s3); }
.sn-day-badge {
  display: inline-block; background: var(--brand-deep); color: #fff; font-size: var(--fs-sm);
  font-weight: 600; padding: var(--s1) var(--s4); border-radius: var(--r-lg); margin-bottom: var(--s3);
}
.sn-slot {
  display: flex; align-items: center; justify-content: space-between; gap: var(--s3);
  background: var(--bg-input, #f7f8fa); border: 1px solid var(--border, #e8e9ed);
  border-left: 4px solid var(--text-muted); border-radius: var(--r-lg); padding: var(--s3) var(--s4); margin-bottom: var(--s2);
}
.sn-slot.slot-night { border-left-color: #4a4a4a; }
.sn-slot.slot-morning { border-left-color: #f5a623; }
.sn-slot.slot-noon { border-left-color: #34a853; }
.sn-slot.slot-close { border-left-color: #d93025; }
.sn-slot.empty { justify-content: center; color: var(--text-muted, var(--text-muted)); border-left-color: var(--text-secondary); }
.sn-slot-left { display: flex; align-items: center; gap: var(--s3); min-width: 0; }
.sn-slot-tag {
  flex-shrink: 0; font-size: var(--fs-xs); font-weight: 700; color: #fff;
  background: var(--text-faint); border-radius: var(--r-lg); padding: var(--s1) var(--s2);
}
.slot-night .sn-slot-tag { background: #4a4a4a; }
.slot-morning .sn-slot-tag { background: #f5a623; }
.slot-noon .sn-slot-tag { background: #34a853; }
.slot-close .sn-slot-tag { background: #d93025; }
.sn-slot-info { min-width: 0; }
.sn-slot-title {
  font-size: var(--fs-base); font-weight: 600; color: var(--text-main, #1a1a1a);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.sn-slot-meta { font-size: var(--fs-xs); color: var(--text-muted, var(--text-muted)); margin-top: var(--s1); display: flex; gap: var(--s2); flex-wrap: wrap; }
.sn-view-btn {
  flex-shrink: 0; background: var(--brand-deep); color: #fff; font-size: var(--fs-sm);
  text-decoration: none; padding: var(--s2) var(--s4); border-radius: 16px; font-weight: 600;
}
.sn-view-btn:hover { opacity: 0.88; }
.sn-past { margin-top: var(--s4); border-top: 1px dashed var(--border, #e0e0e0); padding-top: var(--s3); }
.sn-past-title { font-size: var(--fs-sm); font-weight: 600; color: var(--text-muted, var(--text-muted)); margin-bottom: var(--s2); }
.sn-past-list { display: flex; flex-wrap: wrap; gap: var(--s2); }
.sn-past-day {
  background: var(--bg-input, #f2f3f5); border: 1px solid var(--border, #e5e6eb);
  border-radius: var(--r-md); padding: var(--s1) var(--s2); font-size: var(--fs-xs); cursor: pointer;
  color: var(--text-main, #333); display: inline-flex; align-items: center; gap: var(--s2);
}
.sn-past-day.active { background: var(--brand-deep); color: #fff; border-color: var(--brand-deep); }
.sn-past-n {
  background: rgba(0, 0, 0, 0.12); border-radius: var(--r-md); font-size: var(--fs-xs);
  padding: 0 var(--s2); line-height: 16px;
}
.sn-past-day.active .sn-past-n { background: rgba(255, 255, 255, 0.25); }
.sn-past-empty { font-size: var(--fs-xs); color: var(--text-muted, var(--text-muted)); }
</style>
