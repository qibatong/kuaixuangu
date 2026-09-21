<template>
  <Teleport to="body">
    <div v-if="open" class="ud-mask" @click.self="close">
      <div class="ud-panel">
        <div class="ud-head">
          <span class="ud-title"><i class="fa fa-user-circle-o"></i> 用户详情 #{{ uid }}</span>
          <button class="ud-close" @click="close"><i class="fa fa-times"></i></button>
        </div>

        <div v-if="loading" class="ud-loading"><div class="spinner"></div></div>
        <div v-else-if="err" class="ud-err">{{ err }}</div>

        <div v-else class="ud-body">
          <!-- 基础信息 -->
          <div class="ud-card">
            <div class="ud-name-row">
              <span class="ud-name">{{ u.username }}</span>
              <span class="ud-badge" :class="'lv-' + u.member_level">{{ levelLabel(u.member_level) }}</span>
              <span v-if="u.is_admin" class="ud-badge admin">管理员</span>
            </div>
            <div class="ud-kv">
              <span><i class="fa fa-mobile"></i> {{ u.phone || '未绑定' }}</span>
              <span><i class="fa fa-weixin"></i> {{ u.wx_name || '-' }}</span>
              <span><i class="fa fa-envelope-o"></i> {{ u.email || '-' }}</span>
            </div>
            <div class="ud-kv">
              <span><i class="fa fa-clock-o"></i> 注册 {{ u.created_date }}</span>
              <span><i class="fa fa-globe"></i> IP {{ u.register_ip || '-' }}</span>
            </div>
            <div class="ud-kv">
              <span>
                <i class="fa fa-calendar"></i>
                <template v-if="u.permanent">永久有效</template>
                <template v-else-if="u.days_left >= 0">到期 {{ u.expire_date }}（剩 {{ u.days_left }} 天）</template>
                <template v-else>已过期 {{ u.expire_date }}</template>
              </span>
              <span><i class="fa fa-ticket"></i> 邀请码 {{ u.invite_code || '-' }}</span>
            </div>
            <div v-if="u.remark || u.pay_remark" class="ud-kv">
              <span v-if="u.remark"><i class="fa fa-sticky-note-o"></i> {{ u.remark }}</span>
              <span v-if="u.pay_remark"><i class="fa fa-jpy"></i> {{ u.pay_remark }}</span>
            </div>
          </div>

          <!-- 配额 + 快捷操作 -->
          <div class="ud-card">
            <div class="ud-card-title">今日配额</div>
            <div class="ud-quota">
              <div v-for="q in quotaList" :key="q.feature" class="ud-q-item">
                <span class="ud-q-name">{{ q.label }}</span>
                <span class="ud-q-val">
                  <template v-if="q.privileged">不限次</template>
                  <template v-else>{{ q.remain }} / {{ q.limit }}</template>
                </span>
              </div>
            </div>
            <div class="ud-acts">
              <button class="ud-btn" @click="resetQuota(null)">重置全部配额</button>
              <button class="ud-btn" @click="extend(30)">+30 天</button>
              <button class="ud-btn" @click="extend(365)">+1 年</button>
              <button class="ud-btn" @click="emit('goto-expire', u.id)">设置到期</button>
            </div>
          </div>

          <!-- 邀请关系 -->
          <div class="ud-card">
            <div class="ud-card-title">邀请关系</div>
            <div class="ud-line">
              <span class="ud-k">邀请人</span>
              <span class="ud-v">{{ inviter ? '#' + inviter.id + ' ' + inviter.username : '无（自然注册）' }}</span>
            </div>
            <div class="ud-line">
              <span class="ud-k">已邀请（{{ invitees.length }}）</span>
              <span class="ud-v">
                <template v-if="invitees.length">
                  <span v-for="iv in invitees" :key="iv.id" class="ud-chip" :title="'IP ' + (iv.register_ip || '-')">
                    {{ iv.username }}
                    <em v-if="iv.register_ip && iv.register_ip === u.register_ip" class="ud-chip-warn" title="与邀请人同 IP">⚠</em>
                  </span>
                </template>
                <template v-else>-</template>
              </span>
            </div>
          </div>

          <!-- 签到 -->
          <div class="ud-card">
            <div class="ud-card-title">最近签到（{{ checkins.length }} 次）</div>
            <div v-if="checkins.length" class="ud-chips">
              <span v-for="c in checkins" :key="c.date" class="ud-chip"> {{ c.date.slice(5) }} <b>+{{ c.reward }}</b></span>
            </div>
            <div v-else class="ud-empty">暂无签到记录</div>
          </div>

          <!-- 手机号台账 -->
          <div class="ud-card">
            <div class="ud-card-title">手机号领取台账</div>
            <div v-if="claims.length" class="ud-claims">
              <div v-for="(c, i) in claims" :key="i" class="ud-line">
                <span class="ud-k">{{ c.phone }}</span>
                <span class="ud-v">
                  领取 {{ c.claim_count }} 次 · 首领 UID {{ c.first_uid }} · IP {{ c.last_ip || '-' }}
                  <em v-if="c.claim_count > 1" class="ud-chip-warn">⚠ 重复领取</em>
                </span>
              </div>
            </div>
            <div v-else class="ud-empty">无台账记录（早于功能上线或未绑定手机号）</div>
          </div>

          <!-- 审计 -->
          <div class="ud-card">
            <div class="ud-card-title">最近被操作记录</div>
            <div v-if="audits.length" class="ud-audits">
              <div v-for="(a, i) in audits" :key="i" class="ud-line">
                <span class="ud-k mono">{{ fmtTs(a.created_at) }}</span>
                <span class="ud-v"><b>{{ a.action }}</b> {{ fmtDetail(a.detail) }}</span>
              </div>
            </div>
            <div v-else class="ud-empty">暂无记录</div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { adminUserDetail, adminExtendPlus, adminResetQuota } from '../api/admin'
import { showToast } from '../utils/toast'

const emit = defineEmits(['goto-expire'])

const open = ref(false)
const loading = ref(false)
const err = ref('')
const uid = ref(0)
const u = ref({})
const inviter = ref(null)
const invitees = ref([])
const checkins = ref([])
const claims = ref([])
const audits = ref([])
const quotaList = ref([])

const LEVELS = { 0: '免费试用', 1: '付费会员', 2: 'VIP老师' }
const FEATURE = { picker: '选股', aipick: 'AI 预测', auction: '竞价异动' }

function levelLabel(l) { return LEVELS[l] || ('等级 ' + l) }
function fmtTs(ts) {
  if (!ts) return '-'
  const d = new Date(Number(ts) * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}
function fmtDetail(d) {
  if (!d) return ''
  if (typeof d === 'string') return d
  try {
    const s = JSON.stringify(d)
    return s.length > 120 ? s.slice(0, 120) + '…' : s
  } catch { return '' }
}

async function load() {
  loading.value = true
  err.value = ''
  try {
    const d = await adminUserDetail(uid.value)
    u.value = d.user || {}
    inviter.value = d.inviter || null
    invitees.value = d.invitees || []
    checkins.value = d.checkins || []
    claims.value = d.claims || []
    audits.value = d.audits || []
    // 配额: 该接口不含配额, 用 reset-quota 的返回补充 —— 改由点「重置」时刷新
    quotaList.value = quotaList.value.length ? quotaList.value : []
  } catch (e) {
    err.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function resetQuota(feature) {
  if (!confirm('确定重置该用户今日配额？')) return
  try {
    const d = await adminResetQuota(uid.value, feature)
    showToast('✅ ' + (d.msg || '已重置'), 'success')
    const q = d.quota || {}
    quotaList.value = Object.entries(q).map(([k, v]) => ({
      feature: k, label: FEATURE[k] || k, ...v,
    }))
  } catch (e) { showToast('❌ ' + (e.message || '重置失败'), 'error') }
}

async function extend(days) {
  try {
    const d = await adminExtendPlus([uid.value], days)
    showToast('✅ ' + (d.msg || '已续期'), 'success')
    load()
  } catch (e) { showToast('❌ ' + (e.message || '续期失败'), 'error') }
}

function show(targetUid) {
  uid.value = targetUid
  open.value = true
  quotaList.value = []
  load()
}
function close() { open.value = false }

defineExpose({ show, close })

watch(open, (v) => {
  document.body.style.overflow = v ? 'hidden' : ''
})
</script>

<style scoped>
.ud-mask {
  position: fixed; inset: 0; z-index: 3000;
  background: rgba(0, 0, 0, .5);
  display: flex; justify-content: flex-end;
}
.ud-panel {
  width: min(560px, 100vw); height: 100%;
  background: var(--bg-panel); border-left: 1px solid var(--border-soft);
  display: flex; flex-direction: column;
  animation: udIn .2s ease;
}
@keyframes udIn { from { transform: translateX(24px); opacity: .6 } to { transform: none; opacity: 1 } }
.ud-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 16px; border-bottom: 1px solid var(--border-soft); flex-shrink: 0;
}
.ud-title { font-size: 0.9375rem; font-weight: 700; color: var(--text-main); }
.ud-title i { color: var(--accent); margin-right: 6px; }
.ud-close {
  background: transparent; border: none; color: var(--text-muted);
  font-size: 1rem; cursor: pointer; padding: 4px 8px;
}
.ud-close:hover { color: var(--text-main); }
.ud-loading { padding: 40px; display: flex; justify-content: center; }
.ud-err { padding: 30px; text-align: center; color: #ff6a6a; font-size: 0.875rem; }
.ud-body { flex: 1; overflow-y: auto; padding: 12px 16px 30px; display: flex; flex-direction: column; gap: 12px; }

.ud-card {
  border: 1px solid var(--border-soft); border-radius: 10px; padding: 12px 14px;
}
.ud-card-title { font-size: 0.8125rem; font-weight: 700; color: var(--text-main); margin-bottom: 8px; }
.ud-name-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.ud-name { font-size: 1rem; font-weight: 700; color: var(--text-main); }
.ud-badge {
  font-size: 0.6875rem; padding: 1px 8px; border-radius: 9px;
  background: var(--bg-input); color: var(--text-muted);
}
.ud-badge.lv-1 { background: rgba(var(--accent-rgb), .14); color: var(--accent-deep); }
.ud-badge.lv-2 { background: rgba(255, 176, 32, .16); color: #d4a017; }
.ud-badge.admin { background: rgba(43, 182, 115, .14); color: #2bb673; }
.ud-kv { display: flex; gap: 14px; flex-wrap: wrap; font-size: 0.75rem; color: var(--text-secondary); margin-top: 5px; }
.ud-kv i { color: var(--text-muted); margin-right: 4px; }

.ud-quota { display: flex; gap: 8px; flex-wrap: wrap; }
.ud-q-item {
  flex: 1; min-width: 92px; border: 1px solid var(--border-soft); border-radius: 8px;
  padding: 8px 10px; font-size: 0.75rem;
}
.ud-q-name { display: block; color: var(--text-muted); margin-bottom: 3px; }
.ud-q-val { color: var(--text-main); font-weight: 700; }

.ud-acts { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
.ud-btn {
  padding: 5px 11px; font-size: 0.75rem; border-radius: 6px; cursor: pointer;
  border: 1px solid var(--accent); background: transparent; color: var(--accent);
}
.ud-btn:hover { background: rgba(var(--accent-rgb), .1); }

.ud-line { display: flex; gap: 10px; font-size: 0.75rem; margin-top: 6px; align-items: flex-start; }
.ud-line .ud-k { flex-shrink: 0; width: 92px; color: var(--text-muted); }
.ud-line .ud-v { flex: 1; color: var(--text-secondary); word-break: break-all; }
.ud-line .mono { font-family: ui-monospace, Menlo, Consolas, monospace; }
.ud-chip {
  display: inline-block; padding: 1px 7px; margin: 0 4px 4px 0; border-radius: 8px;
  background: var(--bg-input); color: var(--text-secondary); font-size: 0.6875rem;
}
.ud-chip b { color: var(--accent-deep); }
.ud-chip-warn { color: #ffb020; font-style: normal; margin-left: 3px; }
.ud-chips { display: flex; flex-wrap: wrap; gap: 4px; }
.ud-empty { font-size: 0.75rem; color: var(--text-muted); padding: 4px 0; }

body[data-bg="light"] .ud-panel { background: #fff; }
</style>
