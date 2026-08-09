<template>
  <div class="admin-wrap">
    <div class="admin-head">
      <router-link to="/" class="tdx-export-btn" style="background:rgba(120,200,80,0.15);border:1px solid #78c850;color:#c0e8a0;"><i class="fa fa-arrow-left"></i> 返回选股</router-link>
      <h2 style="margin:0 auto;color:#ffe0a0;"><i class="fa fa-shield"></i> 管理后台</h2>
      <span style="width:120px;"></span>
    </div>

    <!-- 403 提示 -->
    <div v-if="denied" class="admin-card" style="text-align:center;padding:40px;color:#ff9a9a;">
      <i class="fa fa-lock" style="font-size:36px;"></i>
      <p style="margin-top:12px;">无管理员权限，请联系管理员开通</p>
      <router-link to="/" class="tdx-export-btn" style="background:rgba(255,180,0,0.18);border:1px solid #ffb400;color:#ffe0a0;">返回主页</router-link>
    </div>

    <template v-else>
      <!-- 用户统计卡片 -->
      <div class="stat-cards">
        <div class="stat-card"><div class="stat-num">{{ stats.total ?? '-' }}</div><div class="stat-label">注册用户</div></div>
        <div class="stat-card"><div class="stat-num">{{ stats.today ?? '-' }}</div><div class="stat-label">今日注册</div></div>
        <div class="stat-card"><div class="stat-num">{{ stats.active ?? '-' }}</div><div class="stat-label">活跃用户(有选股)</div></div>
        <div class="stat-card"><div class="stat-num">{{ stats.invited ?? '-' }}</div><div class="stat-label">受邀注册</div></div>
        <div class="stat-card"><div class="stat-num small">{{ stats.top_inviter ? stats.top_inviter.username : '-' }}</div><div class="stat-label">Top邀请人({{ stats.top_inviter ? stats.top_inviter.n + '人' : '' }})</div></div>
      </div>

      <!-- 用户列表 -->
      <div class="admin-card">
        <div class="card-title"><i class="fa fa-users"></i> 用户列表
          <div style="display:flex;gap:8px;margin-left:auto;">
            <input v-model="keyword" class="admin-input" placeholder="搜索用户名/手机/邮箱" @keyup.enter="loadUsers(1)" />
            <button class="tdx-export-btn" style="background:rgba(0,180,255,0.15);border:1px solid #00b4ff;color:#a0e0ff;" @click="loadUsers(1)"><i class="fa fa-search"></i> 搜索</button>
          </div>
        </div>
        <div class="table-scroll">
          <table class="admin-table">
            <thead>
              <tr>
                <th>ID</th><th>用户名</th><th>手机/邮箱</th><th>注册时间</th>
                <th>邀请人数</th><th>选股次数</th><th>角色</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="u in rows" :key="u.id">
                <td>{{ u.id }}</td>
                <td>{{ u.username }}</td>
                <td>{{ u.phone || u.email || '-' }}</td>
                <td>{{ fmtTime(u.created_at) }}</td>
                <td>{{ u.invited_count }}</td>
                <td>{{ u.batch_count }}</td>
                <td><span v-if="u.is_admin" class="admin-tag">管理员</span><span v-else class="user-tag">普通用户</span></td>
              </tr>
              <tr v-if="!rows.length"><td colspan="7" style="text-align:center;color:#888;padding:20px;">暂无用户</td></tr>
            </tbody>
          </table>
        </div>
        <div class="pager">
          <button class="page-btn" :disabled="page <= 1" @click="loadUsers(page - 1)">上一页</button>
          <span style="color:#bbb;">第 {{ page }} / {{ totalPages }} 页 · 共 {{ total }} 人</span>
          <button class="page-btn" :disabled="page >= totalPages" @click="loadUsers(page + 1)">下一页</button>
        </div>
      </div>

      <!-- 评分权重配置 -->
      <div class="admin-card">
        <div class="card-title"><i class="fa fa-sliders"></i> 评分权重配置 <span style="color:#888;font-size:12px;margin-left:8px;">保存后立即生效（影响后续选股评分）</span></div>
        <table class="admin-table weight-table">
          <thead><tr><th style="width:140px;">因子</th><th>权重(0~1)</th><th>说明</th></tr></thead>
          <tbody>
            <tr v-for="wk in wKeys" :key="wk[0]">
              <td>{{ wk[1] }}</td>
              <td><input v-model.number="scoring[wk[0]]" type="number" step="0.01" min="0" max="1" class="admin-input" style="width:90px;" /></td>
              <td style="color:#999;font-size:12px;">{{ wk[2] }}</td>
            </tr>
            <tr>
              <td colspan="2" style="color:#ffbcbc;font-size:13px;">权重合计：{{ weightSum.toFixed(2) }} <span v-if="Math.abs(weightSum - 1) > 0.03" style="color:#ff6a6a;">（需约等于 1 才能保存）</span></td>
              <td></td>
            </tr>
            <tr v-for="ck in confKeys" :key="ck[0]">
              <td>置信度·{{ ck[1] }}</td>
              <td><input v-model.number="scoring[ck[0]]" type="number" step="1" min="0" max="30" class="admin-input" style="width:90px;" /></td>
              <td style="color:#999;font-size:12px;">{{ ck[2] }}</td>
            </tr>
          </tbody>
        </table>
        <div style="display:flex;gap:10px;margin-top:14px;align-items:center;">
          <button class="tdx-export-btn" style="background:rgba(120,200,80,0.2);border:1px solid #78c850;color:#c0e8a0;" :disabled="saving" @click="saveScoring">
            <i class="fa fa-save"></i> {{ saving ? '保存中...' : '保存并生效' }}
          </button>
          <span v-if="saveMsg" :style="{ color: saveErr ? '#ff6a6a' : '#7ce8a0' }">{{ saveMsg }}</span>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { adminScoring, adminUsers, saveScoring as apiSaveScoring } from '../api/admin'
import { showToast as toast } from '../utils/toast'

const stats = ref({})
const rows = ref([])
const page = ref(1)
const total = ref(0)
const pageSize = 20
const keyword = ref('')
const denied = ref(false)

const scoring = reactive({})
const wKeys = ref([])
const confKeys = ref([])
const saving = ref(false)
const saveMsg = ref('')
const saveErr = ref(false)

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const weightSum = computed(() => {
  let s = 0
  wKeys.value.forEach(([k]) => { const v = Number(scoring[k]); if (!isNaN(v)) s += v })
  return s
})

function fmtTime(ts) {
  if (!ts) return '-'
  if (typeof ts === 'number' && ts > 1000000000) {
    const d = new Date((ts + 8 * 3600) * 1000)
    return d.toISOString().replace('T', ' ').slice(0, 16)
  }
  return String(ts)
}

async function loadUsers(p) {
  try {
    const d = await adminUsers({ page: p, pageSize, keyword: keyword.value })
    rows.value = d.rows || []
    total.value = d.total || 0
    page.value = d.page || 1
    stats.value = d.stats || {}
  } catch (e) {
    if (e.status === 403) denied.value = true
    else toast(e.message || '加载失败', 'error')
  }
}

async function loadScoring() {
  try {
    const d = await adminScoring()
    Object.assign(scoring, d.scoring || {})
    wKeys.value = d.w_keys || []
    confKeys.value = d.conf_keys || []
  } catch (e) {
    if (e.status === 403) denied.value = true
    else toast(e.message || '加载失败', 'error')
  }
}

async function saveScoring() {
  saveMsg.value = ''
  const payload = {}
  Object.keys(scoring).forEach((k) => { payload[k] = Number(scoring[k]) })
  saving.value = true
  try {
    const d = await apiSaveScoring(payload)
    saveMsg.value = d.msg || '已保存'
    saveErr.value = false
    toast('权重已保存并生效', 'success')
  } catch (e) {
    saveMsg.value = e.message || '保存失败'
    saveErr.value = true
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  loadUsers(1)
  loadScoring()
})
</script>

<style scoped>
.admin-wrap { max-width: 1500px; margin: 0 auto; padding: 14px 16px; }
.admin-head { display: flex; align-items: center; margin-bottom: 14px; }
.stat-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 14px; }
.stat-card { background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.1); border-radius: 10px; padding: 16px; text-align: center; }
.stat-num { font-size: 26px; font-weight: 700; color: #ffd700; }
.stat-num.small { font-size: 16px; color: #a0e0ff; }
.stat-label { margin-top: 6px; color: #aaa; font-size: 12px; }
.admin-card { background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.1); border-radius: 10px; padding: 16px; margin-bottom: 14px; }
.card-title { display: flex; align-items: center; font-size: 15px; color: #ffe0a0; margin-bottom: 12px; }
.admin-input { background: rgba(0,0,0,0.3); border: 1px solid rgba(255,255,255,0.15); border-radius: 6px; color: #eee; padding: 6px 10px; font-size: 13px; }
.admin-input:focus { outline: none; border-color: #ffb400; }
.table-scroll { overflow-x: auto; }
.admin-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.admin-table th, .admin-table td { border-bottom: 1px solid rgba(255,255,255,0.08); padding: 8px 10px; text-align: left; color: #ddd; }
.admin-table th { color: #999; font-weight: 500; }
.admin-tag { color: #ffd700; border: 1px solid #ffd700; border-radius: 4px; padding: 1px 8px; font-size: 12px; }
.user-tag { color: #999; border: 1px solid #666; border-radius: 4px; padding: 1px 8px; font-size: 12px; }
.pager { display: flex; justify-content: flex-end; align-items: center; gap: 12px; margin-top: 12px; }
.page-btn { background: rgba(0,180,255,0.12); border: 1px solid #00b4ff; color: #a0e0ff; border-radius: 6px; padding: 4px 14px; cursor: pointer; }
.page-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.weight-table input { color: #ffd700; }
</style>
