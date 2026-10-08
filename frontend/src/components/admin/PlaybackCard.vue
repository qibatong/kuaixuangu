<template>
  <div class="admin-card">
    <div class="card-title">
      <i class="fa fa-video-camera"></i> 历史竞价回放 <span class="admin-tip">9:15/9:20/9:25 全市场快照(每个交易日自动归档)</span>
      <div style="display:flex;gap:8px;margin-left:auto;align-items:center;">
        <input v-model="playDate" type="date" class="admin-input" :max="'2099-12-31'" />
        <select v-model="playTime" class="admin-input">
          <option value="9_15">9:15</option>
          <option value="9_20">9:20</option>
          <option value="9_25">9:25</option>
        </select>
        <button class="tdx-export-btn admin-save-btn" @click="loadPlayback"><i class="fa fa-play"></i> 回放</button>
      </div>
    </div>
    <div class="table-scroll">
      <table class="admin-table">
        <thead>
          <tr>
            <th style="width:60px;">排名</th>
            <th class="sortable" :class="{ active: playSort.keyOf('code') }" @click="playSort.onSort('code', 'string')">代码<span class="sort-ind">{{ playSort.ind('code') }}</span></th>
            <th class="sortable" :class="{ active: playSort.keyOf('bid_change') }" @click="playSort.onSort('bid_change')">竞价涨幅<span class="sort-ind">{{ playSort.ind('bid_change') }}</span></th>
            <th class="sortable" :class="{ active: playSort.keyOf('bid_amt') }" @click="playSort.onSort('bid_amt')">竞价额(万)<span class="sort-ind">{{ playSort.ind('bid_amt') }}</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(s, i) in playSort.sorted(playList)" :key="s.code">
            <td>{{ i + 1 }}</td>
            <td>{{ s.code }}</td>
            <td :class="s.bid_change >= 0 ? 'up' : 'down'">{{ s.bid_change >= 0 ? '+' : '' }}{{ s.bid_change.toFixed(2) }}%</td>
            <td>{{ s.bid_amt ? s.bid_amt.toFixed(0) : '-' }}</td>
          </tr>
          <tr v-if="!playList.length"><td colspan="4" style="text-align:center;color:#888;padding:16px;">该日期该时点暂无快照（需交易日 9:15/9:20/9:25 自动采集后才有）</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
// 历史竞价回放卡片: 自管日期/时点选择 + 快照表格(可排序)
import { ref } from 'vue'
import { bidSnapshot } from '../../api/admin'
import { showToast as toast } from '../../utils/toast'
import { useSortable } from '../../composables/useSortable'
import { todayBj } from '../../utils/time'

const playDate = ref(todayBj())
const playTime = ref('9_25')
const playList = ref([])
const playSort = useSortable()

async function loadPlayback() {
  try {
    const d = await bidSnapshot(playDate.value, playTime.value, 50)
    playList.value = d.list || []
  } catch (e) {
    toast(e.message || '查询失败', 'error')
  }
}
</script>

<style scoped>
.admin-card { background: var(--bg-subtle); border: 1px solid var(--border-soft); border-radius: var(--r-lg); padding: var(--s4); margin-bottom: var(--s4); }
.card-title { display: flex; align-items: center; font-size: var(--fs-md); color: var(--warn-text); margin-bottom: var(--s3); }
.admin-tip { color: var(--text-muted); font-size: var(--fs-xs); margin-left: var(--s2); }
.admin-input { background: var(--bg-input); border: 1px solid var(--border-soft); border-radius: var(--r-md); color: var(--text-main); padding: var(--s2) var(--s2); font-size: var(--fs-sm); }
.admin-input:focus { outline: none; border-color: var(--star); }
.admin-save-btn { background: rgba(120,200,80,0.2); border: 1px solid var(--success); color: var(--success-text); padding: var(--s2) var(--s4); border-radius: var(--r-md); font-size: var(--fs-sm); cursor: pointer; transition: opacity 0.15s; }
.admin-save-btn:hover:not(:disabled) { background: rgba(120,200,80,0.32); }
.table-scroll { overflow-x: auto; }
.admin-table { width: 100%; border-collapse: collapse; font-size: var(--fs-sm); }
.admin-table th, .admin-table td { border-bottom: 1px solid var(--border-soft); padding: var(--s2) var(--s2); text-align: center; color: var(--text-secondary); }
.admin-table th { color: var(--text-muted); font-weight: 400; }

/* 浅色主题覆盖 */
body[data-bg="light"] .card-title { color: var(--watermark); }
body[data-bg="light"] .admin-tip { color: var(--text-faint); }
body[data-bg="light"] .admin-save-btn { background: rgba(34,139,34,0.1); border: 1px solid #228722; color: #1a6b1a; }
body[data-bg="light"] .admin-save-btn:hover { background: #228722; color: #fff; }
</style>
