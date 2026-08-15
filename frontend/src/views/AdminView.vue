<template>
  <div class="admin-wrap">
    <div class="admin-head">
      <router-link to="/" class="tdx-export-btn nav-btn nav-pool-import"><i class="fa fa-arrow-left"></i> 返回选股</router-link>
      <h2 style="margin:0 auto;color:var(--accent-text);"><i class="fa fa-shield"></i> 管理后台</h2>
      <span style="width:120px;"></span>
    </div>

    <!-- 403 提示 -->
    <div v-if="denied" class="admin-card" style="text-align:center;padding:40px;color:#ff9a9a;">
      <i class="fa fa-lock" style="font-size:36px;"></i>
      <p style="margin-top:12px;">无管理员权限，请联系管理员开通</p>
      <router-link to="/" class="tdx-export-btn nav-btn nav-history">返回主页</router-link>
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
        <div class="card-title">
<i class="fa fa-users"></i> 用户列表
          <div style="display:flex;gap:8px;margin-left:auto;">
            <input v-model="keyword" class="admin-input" placeholder="搜索用户名/手机/邮箱" @keyup.enter="loadUsers(1)" />
            <button class="admin-search-btn" @click="loadUsers(1)"><i class="fa fa-search"></i> 搜索</button>
          </div>
        </div>
        <div class="table-scroll">
          <table class="admin-table">
            <thead>
              <tr>
                <th class="sortable" :class="{ active: userSort.keyOf('id') }" @click="userSort.onSort('id')">ID<span class="sort-ind">{{ userSort.ind('id') }}</span></th>
                <th class="sortable" :class="{ active: userSort.keyOf('username') }" @click="userSort.onSort('username', 'string')">用户名<span class="sort-ind">{{ userSort.ind('username') }}</span></th>
                <th class="sortable" :class="{ active: userSort.keyOf('phone') }" @click="userSort.onSort('phone', 'string')">手机/邮箱<span class="sort-ind">{{ userSort.ind('phone') }}</span></th>
                <th class="sortable" :class="{ active: userSort.keyOf('created_at') }" @click="userSort.onSort('created_at')">注册时间<span class="sort-ind">{{ userSort.ind('created_at') }}</span></th>
                <th class="sortable" :class="{ active: userSort.keyOf('expire_at') }" @click="userSort.onSort('expire_at')">到期时间<span class="sort-ind">{{ userSort.ind('expire_at') }}</span></th>
                <th class="sortable" :class="{ active: userSort.keyOf('invited_count') }" @click="userSort.onSort('invited_count')">邀请人数<span class="sort-ind">{{ userSort.ind('invited_count') }}</span></th>
                <th class="sortable" :class="{ active: userSort.keyOf('batch_count') }" @click="userSort.onSort('batch_count')">选股次数<span class="sort-ind">{{ userSort.ind('batch_count') }}</span></th>
                <th class="sortable" :class="{ active: userSort.keyOf('is_admin') }" @click="userSort.onSort('is_admin')">角色<span class="sort-ind">{{ userSort.ind('is_admin') }}</span></th>
                <th style="min-width:150px;">设置使用期限</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="u in userSort.sorted(rows, userVal)" :key="u.id">
                <td>{{ u.id }}</td>
                <td>{{ u.username }}</td>
                <td>{{ u.phone || u.email || '-' }}</td>
                <td>{{ fmtTime(u.created_at) }}</td>
                <td>
                  <span v-if="expireState(u) === 'forever'" class="user-tag">永久</span>
                  <span v-else-if="expireState(u) === 'expired'" class="expired-tag">已过期 {{ fmtDate(u.expire_at) }}</span>
                  <span v-else class="ok-tag">{{ fmtDate(u.expire_at) }}</span>
                </td>
                <td>{{ u.invited_count }}</td>
                <td>{{ u.batch_count }}</td>
                <td><span v-if="u.is_admin" class="admin-tag">管理员</span><span v-else class="user-tag">普通用户</span></td>
                <td>
                  <div v-if="u.is_admin" style="color:#888;font-size:12px;">管理员永久有效</div>
                  <div v-else class="expire-cell">
                    <button class="mini-btn" @click.stop="togglePanel(u)">⚙️ 设置期限</button>
                    <button class="mini-btn pwd-btn" style="margin-left:6px;" @click.stop="openPwdReset(u)">🔑 重置密码</button>
                    <div v-if="openUid === u.id" class="expire-popover" @click.stop>
                      <div class="pop-label">延长时长</div>
                      <div class="pop-row">
                        <button class="mini-btn" @click="extendUser(u, 'week')">+1周</button>
                        <button class="mini-btn" @click="extendUser(u, 'month')">+1月</button>
                        <button class="mini-btn" @click="extendUser(u, 'quarter')">+1季</button>
                        <button class="mini-btn" @click="extendUser(u, 'year')">+1年</button>
                      </div>
                      <div class="pop-label">自定义到期日</div>
                      <div class="pop-row">
                        <input v-model="u._expireDate" type="date" class="mini-date" :max="'2099-12-31'" />
                        <button class="mini-btn" @click="extendUser(u, 'date')">设为该日</button>
                      </div>
                      <div class="pop-row">
                        <button class="mini-btn danger" title="永久有效" @click="extendUser(u, 'forever')">设为永久</button>
                      </div>
                    </div>
                  </div>
                </td>
              </tr>
              <tr v-if="!rows.length"><td colspan="9" style="text-align:center;color:#888;padding:20px;">暂无用户</td></tr>
            </tbody>
          </table>
        </div>
        <div class="pager">
          <div class="pager-left">
            <span style="color:#888;font-size:12px;">每页</span>
            <select v-model.number="pageSize" class="admin-input" style="width:70px;padding:5px 8px;" @change="changePageSize">
              <option :value="10">10</option>
              <option :value="20">20</option>
              <option :value="50">50</option>
              <option :value="100">100</option>
            </select>
            <span style="color:#888;font-size:12px;">条</span>
          </div>
          <div class="pager-right">
            <button class="page-btn" :disabled="page <= 1" @click="loadUsers(page - 1)">上一页</button>
            <span style="color:#bbb;">第 {{ page }} / {{ totalPages }} 页 · 共 {{ total }} 人</span>
            <button class="page-btn" :disabled="page >= totalPages" @click="loadUsers(page + 1)">下一页</button>
          </div>
        </div>
      </div>

      <!-- 重置密码弹层 -->
      <div v-if="pwdTarget" class="pwd-mask" @click.self="closePwdReset">
        <div class="pwd-pop">
          <div class="pwd-title">🔑 重置密码：{{ pwdTarget.username }}<span style="color:#999;font-size:12px;margin-left:8px;">({{ pwdTarget.phone || pwdTarget.email || '-' }})</span></div>
          <input v-model="pwdNew" type="text" class="admin-input" style="width:100%;box-sizing:border-box;" placeholder="输入新密码(至少6位)" @keyup.enter="doResetPwd" />
          <div style="display:flex;gap:10px;margin-top:14px;justify-content:flex-end;">
            <button class="mini-btn" @click="closePwdReset">取消</button>
            <button class="mini-btn danger" :disabled="pwdSaving" @click="doResetPwd">{{ pwdSaving ? '重置中...' : '确认重置' }}</button>
          </div>
        </div>
      </div>

      <!-- 评分权重配置(竞价, 盘中已合并为同一套逻辑) -->
      <div class="admin-card">
        <div class="card-title">
          <i class="fa fa-sliders"></i> 竞价评分·权重配置
          <span class="admin-tip">保存后立即生效（影响后续选股评分）</span>
        </div>
        <table class="admin-table weight-table">
          <thead><tr><th style="width:140px;">因子</th><th>权重(0~1)</th><th>说明</th></tr></thead>
          <tbody>
            <tr v-for="wk in wKeys" :key="wk[0]">
              <td>{{ wk[1] }}</td>
              <td><input v-model.number="scoring[wk[0]]" type="number" step="0.01" min="0" max="1" class="admin-input" style="width:90px;" /></td>
              <td class="weight-desc">{{ wk[2] }}</td>
            </tr>
            <tr>
              <td colspan="2" class="weight-total">权重合计：{{ weightSum.toFixed(2) }} <span v-if="Math.abs(weightSum - 1) > 0.03" class="weight-warn">（需约等于 1 才能保存）</span></td>
              <td></td>
            </tr>
            <tr v-for="ck in confKeys" :key="ck[0]">
              <td>置信度·{{ ck[1] }}</td>
              <td><input v-model.number="scoring[ck[0]]" type="number" step="1" min="0" max="30" class="admin-input" style="width:90px;" /></td>
              <td class="weight-desc">{{ ck[2] }}</td>
            </tr>
          </tbody>
        </table>
        <div style="display:flex;gap:10px;margin-top:14px;align-items:center;">
          <button class="tdx-export-btn admin-save-btn" :disabled="saving" @click="saveScoring">
            <i class="fa fa-save"></i> {{ saving ? '保存中...' : '保存并生效' }}
          </button>
          <span v-if="saveMsg" :class="saveErr ? 'admin-msg-err' : 'admin-msg-ok'">{{ saveMsg }}</span>
        </div>
      </div>

      <!-- 打分明细配置(因子 Tab 切换) -->
      <div class="admin-card">
        <div class="card-title"><i class="fa fa-table"></i> 竞价评分·打分明细 <span class="admin-tip">命中区间 [下限, 上限) 得对应分，未命中取默认分</span></div>
        <div class="factor-tabs">
          <button v-for="fk in factorOrder" :key="fk" class="factor-tab" :class="{ active: activeFactor === fk }" @click="activeFactor = fk">
            {{ factors[fk] ? factors[fk].label : fk }}
          </button>
        </div>
        <div v-if="factors[activeFactor] && factors[activeFactor].buckets" class="factor-box">
          <div class="factor-title">
{{ factors[activeFactor].label }} <span style="color:#888;font-size:12px;">（{{ factors[activeFactor].unit }}）</span>
            <span style="margin-left:auto;display:flex;align-items:center;gap:6px;">
              默认分 <input v-model.number="factors[activeFactor].default" type="number" step="0.05" min="0" max="1" class="admin-input" style="width:70px;" />
            </span>
          </div>
          <table class="admin-table bucket-table">
            <thead><tr><th style="width:120px;">下限</th><th style="width:120px;">上限</th><th>得分(0~1)</th><th style="width:70px;"></th></tr></thead>
            <tbody>
              <tr v-for="(b, idx) in factors[activeFactor].buckets" :key="idx">
                <td><input v-model.number="b[0]" type="number" step="0.1" class="admin-input" style="width:100px;" /></td>
                <td><input v-model.number="b[1]" type="number" step="0.1" class="admin-input" style="width:100px;" /></td>
                <td><input v-model.number="b[2]" type="number" step="0.05" min="0" max="1" class="admin-input" style="width:100px;" /></td>
                <td><button class="del-btn" @click="delBucket(activeFactor, idx)"><i class="fa fa-trash-o"></i></button></td>
              </tr>
              <tr><td colspan="4"><button class="add-btn" @click="addBucket(activeFactor)"><i class="fa fa-plus"></i> 新增分档</button></td></tr>
            </tbody>
          </table>
        </div>
        <div v-else class="empty-state" style="padding:16px;">该因子暂未加载</div>
      </div>

      <!-- 全局默认筛选参数(所有用户未自定义时使用) -->
      <div class="admin-card">
        <div class="card-title"><i class="fa fa-filter"></i> 全局默认筛选参数 <span class="admin-tip">所有用户未自定义偏好时的默认值；「保存并强制生效」会清除所有用户已保存的筛选偏好(保留背景/字号)，全量立即生效</span></div>
        <div style="display:flex;flex-wrap:wrap;gap:14px;align-items:flex-end;padding:6px 0 2px;">
          <label class="field-label" style="display:flex;flex-direction:column;gap:4px;">
            竞价金额下限(万) <input v-model.number="adminDefaults.bidAmtFloor" type="number" min="0" class="admin-input" style="width:110px;" />
          </label>
          <label class="field-label" style="display:flex;flex-direction:column;gap:4px;">
            竞价涨幅上限(%) <input v-model.number="adminDefaults.bidGt" type="number" min="0" class="admin-input" style="width:110px;" />
          </label>
          <label class="field-label" style="display:flex;flex-direction:column;gap:4px;">
            流通市值下限(亿) <input v-model.number="adminDefaults.floatMvFloor" type="number" min="0" class="admin-input" style="width:110px;" />
          </label>
          <label class="field-label" style="display:flex;flex-direction:column;gap:4px;">
            流通市值上限(亿) <input v-model.number="adminDefaults.floatMvGt" type="number" min="0" class="admin-input" style="width:110px;" />
          </label>
          <label class="field-label" style="display:flex;flex-direction:column;gap:4px;">
            股价上限(元) <input v-model.number="adminDefaults.priceGt" type="number" min="0" class="admin-input" style="width:110px;" />
          </label>
          <label class="field-label" style="display:flex;flex-direction:column;gap:4px;">
            评分下限 <input v-model.number="adminDefaults.probLt" type="number" min="0" max="100" class="admin-input" style="width:110px;" />
          </label>
          <label class="field-label" style="display:flex;align-items:center;gap:6px;font-size:13px;">
            <input v-model="adminDefaults.limitUp" type="checkbox" /> 剔除昨日涨停
          </label>
          <label class="field-label" style="display:flex;align-items:center;gap:6px;font-size:13px;">
            <input v-model="adminDefaults.stSuspend" type="checkbox" /> 剔除ST/停牌
          </label>
          <button class="tdx-export-btn admin-save-btn" :disabled="savingDefaults" @click="saveDefaults(false)">
            <i class="fa fa-save"></i> {{ savingDefaults ? '保存中...' : '保存默认值' }}
          </button>
          <button class="tdx-export-btn admin-save-btn" style="background:#A32D2D;border-color:#A32D2D;" :disabled="savingDefaultsForce" @click="saveDefaults(true)">
            <i class="fa fa-bolt"></i> {{ savingDefaultsForce ? '生效中...' : '保存并强制生效' }}
          </button>
          <span v-if="defaultsMsg" :class="defaultsErr ? 'admin-msg-err' : 'admin-msg-ok'" style="font-size:12px;">{{ defaultsMsg }}</span>
        </div>
      </div>

      <!-- 历史竞价回放 -->
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
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { adminScoring, adminUsers, bidSnapshot, getAdminDefaults, resetUserPassword, saveAdminDefaults, saveScoring as apiSaveScoring, setUserExpire } from '../api/admin'
import { showToast as toast } from '../utils/toast'
import { useSortable } from '../composables/useSortable'

// 默认回放日期 = 今天(北京时间)
function todayBj() {
  const d = new Date(Date.now() + 8 * 3600 * 1000)
  return d.toISOString().slice(0, 10)
}
const playDate = ref(todayBj())
const playTime = ref('9_25')
const playList = ref([])

// 表格排序实例(用户列表 / 历史回放)
const userSort = useSortable()
const playSort = useSortable()

// 用户表取值函数: "手机/邮箱"列实际存 phone 或 email, 需合并取
function userVal(u) {
  const k = userSort.sortState.value ? userSort.sortState.value.key : null
  if (k === 'phone') return u.phone || u.email || ''
  return u[k]
}

// 全局默认筛选参数
const adminDefaults = reactive({ bidAmtFloor: 1000, bidGt: 7, floatMvFloor: 30, floatMvGt: 1000, priceGt: 300, probLt: 65, limitUp: true, stSuspend: true })
const savingDefaults = ref(false)
const savingDefaultsForce = ref(false)
const defaultsMsg = ref('')
const defaultsErr = ref(false)

async function loadDefaults() {
  try {
    const d = await getAdminDefaults()
    if (d.defaults) Object.assign(adminDefaults, d.defaults)
  } catch (e) {
    if (e.status === 403) denied.value = true
    else toast(e.message || '加载默认参数失败', 'error')
  }
}

async function saveDefaults(force = false) {
  // force=true: 保存默认值 + 强制清除所有用户筛选偏好(保留背景/字号), 全量立即生效
  if (force && !window.confirm('「保存并强制生效」将清除所有用户已保存的筛选偏好(保留背景/字号设置)，改为使用新的全局默认值。确定继续？')) return
  const saving = force ? savingDefaultsForce : savingDefaults
  defaultsMsg.value = ''
  saving.value = true
  try {
    const d = await saveAdminDefaults({ ...adminDefaults }, force)
    defaultsMsg.value = d.msg || '已保存'
    defaultsErr.value = false
    toast(force ? '已保存并强制所有用户生效' : '全局默认筛选参数已保存', 'success')
  } catch (e) {
    defaultsMsg.value = e.message || '保存失败'
    defaultsErr.value = true
  } finally {
    saving.value = false
  }
}

async function loadPlayback() {
  try {
    const d = await bidSnapshot(playDate.value, playTime.value, 50)
    playList.value = d.list || []
  } catch (e) {
    toast(e.message || '查询失败', 'error')
  }
}

const openUid = ref(null)
function togglePanel(u) { openUid.value = openUid.value === u.id ? null : u.id }
function closePanel() { openUid.value = null }
function onDocClick(e) { if (!e.target.closest('.expire-cell')) openUid.value = null }
onMounted(() => document.addEventListener('click', onDocClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))

// ---------- 管理员重置密码 ----------
const pwdTarget = ref(null)
const pwdNew = ref('')
const pwdSaving = ref(false)
function openPwdReset(u) {
  pwdTarget.value = u
  pwdNew.value = ''
  closePanel()
}
function closePwdReset() {
  pwdTarget.value = null
  pwdNew.value = ''
}
async function doResetPwd() {
  if (!pwdTarget.value) return
  const pw = pwdNew.value.trim()
  if (pw.length < 6) { toast('新密码至少 6 位', 'error'); return }
  pwdSaving.value = true
  try {
    await resetUserPassword(pwdTarget.value.id, pw)
    toast(`${pwdTarget.value.username} 密码已重置`, 'success')
    closePwdReset()
  } catch (e) {
    toast(e.message || '重置失败', 'error')
  } finally {
    pwdSaving.value = false
  }
}

const stats = ref({})
const rows = ref([])
const page = ref(1)
const total = ref(0)
const pageSize = ref(10)
const keyword = ref('')
const denied = ref(false)

const scoring = reactive({})        // 竞价评分配置(盘中已合并为同一套)
const wKeys = ref([])
const confKeys = ref([])
const activeFactor = ref('bid')     // 打分明细当前激活的因子Tab
const saving = ref(false)
const saveMsg = ref('')
const saveErr = ref(false)

const weightSum = computed(() => {
  let s = 0
  wKeys.value.forEach(([k]) => { const v = Number(scoring[k]); if (!isNaN(v)) s += v })
  return s
})

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))

const factors = reactive({})        // 竞价打分明细
const factorOrder = ['bid', 'activity', 'warn', 'market', 'yesterday']
const scoringLoaded = ref(false)

function fmtTime(ts) {
  if (!ts) return '-'
  if (typeof ts === 'number' && ts > 1000000000) {
    const d = new Date((ts + 8 * 3600) * 1000)
    return d.toISOString().replace('T', ' ').slice(0, 16)
  }
  return String(ts)
}

function fmtDate(ts) {
  if (!ts) return '-'
  const d = new Date((Number(ts) + 8 * 3600) * 1000)
  return d.toISOString().slice(0, 10)
}

function expireState(u) {
  if (!u.expire_at) return 'forever'
  return Date.now() / 1000 > u.expire_at ? 'expired' : 'active'
}

async function extendUser(u, action) {
  const label = { week: '+1周', month: '+1月', quarter: '+1季', year: '+1年', forever: '永久', date: '设日期' }[action]
  try {
    let payload
    if (action === 'forever') payload = { days: 0 }
    else if (action === 'date') {
      if (!u._expireDate) { toast('请先选择日期', 'error'); return }
      payload = { expire_at: u._expireDate }
    } else {
      payload = { duration: action }
    }
    const d = await setUserExpire(u.id, payload)
    toast(`${u.username} ${label}设置成功，到期 ${fmtDate(d.expire_at)}`, 'success')
    u._expireDate = ''
    closePanel()
    loadUsers(page.value)
  } catch (e) {
    toast(e.message || '设置失败', 'error')
  }
}

async function loadUsers(p) {
  try {
    const d = await adminUsers({ page: p, pageSize: pageSize.value, keyword: keyword.value })
    rows.value = d.rows || []
    total.value = d.total || 0
    page.value = d.page || 1
    stats.value = d.stats || {}
  } catch (e) {
    if (e.status === 403) denied.value = true
    else toast(e.message || '加载失败', 'error')
  }
}

// 每页条数变更: 回第 1 页重载
function changePageSize() {
  loadUsers(1)
}

async function loadScoring() {
  try {
    const d = await adminScoring()
    Object.assign(scoring, d.scoring || {})
    wKeys.value = d.w_keys || []
    confKeys.value = d.conf_keys || []
    const fac = (d.scoring && d.scoring.factors) || {}
    factorOrder.forEach((fk) => {
      factors[fk] = fac[fk] || { label: fk, unit: '', buckets: [], default: 0.1 }
      // buckets 行转数组, 便于 v-model.number 双向绑定
      factors[fk].buckets = (factors[fk].buckets || []).map((b) => [Number(b[0]), Number(b[1]), Number(b[2])])
    })
    scoringLoaded.value = true
  } catch (e) {
    if (e.status === 403) denied.value = true
    else toast(e.message || '加载失败', 'error')
  }
}

function addBucket(fk) {
  factors[fk].buckets.push([0, 1, 0.5])
}

function delBucket(fk, idx) {
  factors[fk].buckets.splice(idx, 1)
}

async function saveScoring() {
  saveMsg.value = ''
  const payload = {}
  Object.keys(scoring).forEach((k) => {
    if (!k.startsWith('factors')) payload[k] = Number(scoring[k])
  })
  payload.factors = {}
  factorOrder.forEach((fk) => {
    const f = factors[fk]
    payload.factors[fk] = {
      label: f.label, unit: f.unit, default: Number(f.default),
      buckets: f.buckets.map((b) => [String(b[0]), String(b[1]), Number(b[2])])
    }
  })
  saving.value = true
  try {
    const d = await apiSaveScoring(payload)
    saveMsg.value = d.msg || '已保存'
    saveErr.value = false
    toast('竞价权重与打分明细已保存并生效', 'success')
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
  loadDefaults()
})
</script>

<style scoped>
.admin-wrap { max-width: 1500px; margin: 0 auto; padding: 14px 16px; }
.admin-head { display: flex; align-items: center; margin-bottom: 14px; }
.stat-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 14px; }
.stat-card { background: var(--bg-hover); border: 1px solid var(--border-soft); border-radius: 10px; padding: 16px; text-align: center; }
.stat-num { font-size: 26px; font-weight: 700; color: #ffd700; }
.stat-num.small { font-size: 16px; color: #a0e0ff; }
.stat-label { margin-top: 6px; color: #aaa; font-size: 12px; }
.admin-card { background: var(--bg-hover); border: 1px solid var(--border-soft); border-radius: 10px; padding: 16px; margin-bottom: 14px; }
.field-label { color: #bbb; font-size: 12px; }
.admin-tip { color: #888; font-size: 12px; margin-left: 8px; }
.admin-save-btn {
  background: rgba(120,200,80,0.2);
  border: 1px solid #78c850;
  color: #c0e8a0;
  padding: 6px 14px;
  border-radius: 8px;
  font-size: 13px;
  cursor: pointer;
  transition: opacity 0.15s;
}
.admin-save-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.admin-save-btn:hover:not(:disabled) { background: rgba(120,200,80,0.32); }
.admin-msg-ok { color: #7ce8a0; }
.admin-msg-err { color: #ff6a6a; }
.weight-desc { color: #999; font-size: 12px; }
.weight-total { color: var(--accent-text); font-size: 13px; }
.weight-warn { color: #ff6a6a; }
.admin-warn { color: #ff6a6a; }
.admin-ok-text { color: #7ce8a0; }
.card-title { display: flex; align-items: center; font-size: 15px; color: #ffe0a0; margin-bottom: 12px; }
.admin-input { background: var(--bg-input); border: 1px solid var(--border-soft); border-radius: 6px; color: var(--text-main); padding: 6px 10px; font-size: 13px; }
.admin-input:focus { outline: none; border-color: #ffb400; }
.admin-search-btn {
  background: rgba(0,180,255,0.15);
  border: 1px solid #00b4ff;
  color: #a0e0ff;
  border-radius: 6px;
  padding: 6px 14px;
  font-size: 13px;
  cursor: pointer;
}
.factor-tabs {
  display: flex;
  gap: 8px;
  margin: 4px 0 14px;
  flex-wrap: wrap;
}
.factor-tab {
  background: var(--bg-hover);
  border: 1px solid var(--border-soft);
  color: var(--text-secondary);
  border-radius: 8px;
  padding: 6px 14px;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
}
.factor-tab:hover { border-color: #ffb400; color: #ffe0a0; }
.factor-tab.active {
  background: rgba(120,200,80,0.14);
  border-color: #78c850;
  color: #c8f0a8;
}
.table-scroll { overflow-x: auto; }
.admin-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.admin-table th, .admin-table td { border-bottom: 1px solid var(--border-soft); padding: 8px 10px; text-align: center; color: var(--text-secondary); }
.admin-table th { color: var(--text-muted); font-weight: 500; }
.admin-tag { color: #ffd700; border: 1px solid #ffd700; border-radius: 4px; padding: 1px 8px; font-size: 12px; }
.user-tag { color: var(--text-muted); border: 1px solid #666; border-radius: 4px; padding: 1px 8px; font-size: 12px; }
.expired-tag { color: #ff6a6a; border: 1px solid #ff5050; border-radius: 4px; padding: 1px 8px; font-size: 12px; }
.ok-tag { color: #7ce8a0; border: 1px solid #4caf70; border-radius: 4px; padding: 1px 8px; font-size: 12px; }
.expire-cell { position: relative; display: inline-block; }
.expire-popover {
  position: absolute;
  top: 28px;
  right: 0;
  z-index: 10;
  background: var(--bg-panel-solid);
  border: 1px solid var(--border-soft);
  border-radius: 8px;
  padding: 10px 12px;
  min-width: 220px;
  box-shadow: 0 4px 16px rgba(0,0,0,0.6);
}
.pop-label { font-size: 11px; color: var(--text-muted); margin: 6px 0 4px; }
.pop-label:first-child { margin-top: 0; }
.pop-row { display: flex; gap: 4px; margin-bottom: 6px; align-items: center; flex-wrap: wrap; }
.mini-btn { background: rgba(0,180,255,0.12); border: 1px solid #00b4ff; color: #a0e0ff; border-radius: 4px; padding: 3px 10px; font-size: 12px; cursor: pointer; }
.mini-btn:hover { background: rgba(0,180,255,0.25); }
.mini-btn.danger { background: rgba(255,80,80,0.12); border-color: #ff5050; color: #ff9a9a; }
.mini-btn.danger:hover { background: rgba(255,80,80,0.25); }
.pwd-btn { background: rgba(255,180,0,0.12); border: 1px solid #ffb400; color: #ffe0a0; }
.pwd-btn:hover { background: rgba(255,180,0,0.25); }
.pwd-mask { position: fixed; inset: 0; background: rgba(0,0,0,0.55); display: flex; align-items: center; justify-content: center; z-index: 100; }
.pwd-pop { background: var(--bg-panel-solid); border: 1px solid var(--border-soft); border-radius: 10px; padding: 18px 20px; min-width: 320px; box-shadow: 0 6px 24px rgba(0,0,0,0.35); }
.pwd-title { font-size: 14px; color: #ffe0a0; margin-bottom: 12px; }
.mini-date { background: var(--bg-card); border: 1px solid var(--border-soft); border-radius: 4px; color: var(--text-main); padding: 3px 6px; font-size: 12px; color-scheme: light; }
.pager { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 12px; flex-wrap: wrap; }
.pager-left { display: flex; align-items: center; gap: 6px; }
.pager-right { display: flex; align-items: center; gap: 12px; }
.page-btn { background: rgba(0,180,255,0.12); border: 1px solid #00b4ff; color: #a0e0ff; border-radius: 6px; padding: 4px 14px; cursor: pointer; }
.page-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.weight-table input { color: #ffd700; }
.factor-box { border: 1px solid var(--border-soft); border-radius: 8px; padding: 12px; margin-bottom: 12px; }
.factor-title { display: flex; align-items: center; color: #ffd700; font-size: 14px; margin-bottom: 8px; }
.bucket-table input { color: #a0e0ff; }
.add-btn { background: rgba(0,180,255,0.12); border: 1px dashed #00b4ff; color: #a0e0ff; border-radius: 6px; padding: 3px 14px; cursor: pointer; font-size: 12px; }
.del-btn { background: rgba(255,80,80,0.15); border: 1px solid #ff5050; color: #ff9a9a; border-radius: 6px; padding: 2px 8px; cursor: pointer; }

/* 浅色主题覆盖 */
body[data-bg="light"] .card-title {  color: #5a4a3a;  }
body[data-bg="light"] .mini-btn.danger {  color: #b83010; background: rgba(255,80,80,0.12); border-color: rgba(220,50,50,0.5);  }
body[data-bg="light"] .mini-btn.danger:hover {  background: #b83010; color: white;  }
body[data-bg="light"] .del-btn {  color: #b83010; background: rgba(255,80,80,0.1); border-color: rgba(220,50,50,0.5);  }
body[data-bg="light"] .del-btn:hover {  background: #b83010; color: white;  }
body[data-bg="light"] .pwd-pop {  background: rgba(255,255,255,0.98); border-color: var(--border-soft);  }
body[data-bg="light"] .pwd-mask {  background: rgba(0,0,0,0.45);  }
body[data-bg="light"] .pwd-btn {  color: #8a5500; background: rgba(255,180,0,0.15); border-color: #c79100;  }
body[data-bg="light"] .pwd-title {  color: #5a4a3a;  }
body[data-bg="light"] .weight-table input {  color: #1a1d26; background: rgba(255,255,255,0.95);  }
body[data-bg="light"] .bucket-table input {  color: #1a1d26; background: rgba(255,255,255,0.95);  }
body[data-bg="light"] .factor-title {  color: #8a5500;  }
body[data-bg="light"] .factor-tab {  color: #5a6b85; border-color: var(--border-soft);  }
body[data-bg="light"] .factor-tab:hover {  color: #5a4a3a; border-color: #c79100;  }
body[data-bg="light"] .factor-tab.active {  color: #5a4a3a; border-color: #c79100; background: rgba(255,180,0,0.15);  }
body[data-bg="light"] .stat-num {  color: #1a1d26;  }
body[data-bg="light"] .page-btn {  color: #005c5a; background: rgba(0,180,180,0.12); border-color: #0080a0;  }
body[data-bg="light"] .mini-date {  color: #1a1d26; background: rgba(255,255,255,0.95); border-color: var(--border-soft);  }
body[data-bg="light"] .mini-btn {  color: #1a1d26; background: rgba(240,245,250,0.9); border-color: var(--border-soft);  }
body[data-bg="light"] .admin-tag {  color: #8a5500; border-color: #c79100;  }
body[data-bg="light"] .expired-tag {  color: #b83010; border-color: #b83010;  }
body[data-bg="light"] .ok-tag {  color: #2d7020; border-color: #4caf70;  }
body[data-bg="light"] .stat-num { color: #1a1d26; }
body[data-bg="light"] .stat-num.small { color: #1a1d26; }
body[data-bg="light"] .weight-table input { color: #1a1d26; background: rgba(255,255,255,0.95); }
body[data-bg="light"] .bucket-table input { color: #1a1d26; background: rgba(255,255,255,0.95); }
body[data-bg="light"] .factor-title { color: #8a5500; }
body[data-bg="light"] .expire-popover { background: rgba(255,255,255,0.98); border-color: var(--border-soft); }
body[data-bg="light"] .pwd-pop { background: rgba(255,255,255,0.98); border-color: var(--border-soft); }
body[data-bg="light"] .field-label { color: #5a4a3a; }
body[data-bg="light"] .admin-tip { color: #5a6b85; }
body[data-bg="light"] .admin-save-btn { background: rgba(34,139,34,0.1); border: 1px solid #228722; color: #1a6b1a; }
body[data-bg="light"] .admin-save-btn:hover { background: #228722; color: #fff; }
body[data-bg="light"] .admin-msg-ok { color: #1a6b1a; }
body[data-bg="light"] .weight-desc { color: #5a6b85; }
body[data-bg="light"] .weight-total { color: #8a5500; }
body[data-bg="light"] .weight-warn { color: #b83010; }
</style>
