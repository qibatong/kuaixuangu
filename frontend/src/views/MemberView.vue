<template>
  <div class="page-shell member-page">
    <h1 class="visually-hidden">我的会员</h1>
    <!-- 🔴 2026-10-05 晚（主人指令）：账户卡**置顶**。
         上午（v4.11.88 / M6b）曾按"会员与开配置顶、账户与显示设置收尾"把它移到页尾；
         主人实际用了一天反馈「这个在页面的最下面，不方便」⇒ 现在回到本页最上方。
         ★ 仍然放在 loading 闸门之外（理由见下方 2026-09-27 的注释）。 -->
    <!-- 🔴 2026-09-27 v4.11.65 账户区块由顶部导航栏整块迁入这里
         （主人：「3、首页的用户收进 我的 里面。」+ 澄清「是，从顶部移除、整块收进『我的』」）。
         原 NavBar「用户名 ▾ 下拉菜单」里的能力逐一对应保留：
           我的会员 = 本页自身 / 个人信息 / 修改密码 / 退出登录 / 字号 / 字体族。
         顶部导航栏此后只保留：品牌 · 一级分组 · 全局搜索 · 主题圆点（+ 未登录时的登录/注册）。
         ★ 刻意放在 `loading` 判断**之外**：本卡片的每一项（个人信息/改密/退出/字号/字体）
           都不依赖会员接口。接口慢或挂了也必须能改密、能退出登录 ——
           否则「会员接口异常」会连带把「退出登录」也锁死，那就成了一个自己把自己关在门里的缺陷。
           附带好处：SSR 冒烟测试（_verify/nav.spec.js G12）无需接口即可覆盖本卡模板。 -->
    <div class="mb-card mb-acc-card">
      <div class="mb-card-head">
        <span class="mb-card-title"><i class="fa fa-user-circle-o"></i> 账户</span>
        <span class="mb-card-note">{{ user.username }}</span>
      </div>
      <div class="mb-acc-badges">
        <span v-if="user.memberLevel === 2" class="member-badge vip-badge" title="VIP · 永久权限">VIP</span>
        <span v-else-if="user.memberLevel === 1" class="member-badge paid-badge" title="付费会员">付费会员</span>
        <span v-else-if="user.isAdmin" class="member-badge admin-badge" title="管理员">管理员</span>
        <span v-else-if="user.memberDaysLeft >= 0" class="member-badge trial-badge" :title="'免费试用剩余 ' + user.memberDaysLeft + ' 天, 到期请联系管理员开通'">试用{{ user.memberDaysLeft }}天</span>
        <span
          v-if="!user.isAdmin && user.memberLevel !== 2 && user.memberDaysLeft >= 0 && user.memberDaysLeft <= 2"
          class="member-badge renew-badge" title="请尽快续费, 联系管理员(微信号 poet-1986)"
        >
          <i class="fa fa-exclamation-circle"></i> 还剩{{ user.memberDaysLeft }}天续费
        </span>
      </div>
      <div class="mb-acc-actions">
        <button class="mb-mini-btn" @click="profileModal.open()"><i class="fa fa-id-card"></i> 个人信息</button>
        <button class="mb-mini-btn" @click="changePwdModal.open()"><i class="fa fa-key"></i> 修改密码</button>
        <button class="mb-mini-btn danger" @click="logout"><i class="fa fa-sign-out"></i> 退出登录</button>
      </div>
      <!-- 显示设置：背景明暗 / 字号 / 字体族。原在**顶部导航栏**（背景是右侧圆点，
           字号字体在账户下拉菜单），2026-09-27 下拉整块迁来；
           **2026-10-04 主人指令「深色和浅色背景 转移到用户中心里面」** ⇒ 顶部圆点一并收进来，
           三者现在同属一张「显示」卡片，位置集中、不再需要顶栏那个只有 2 个点的控件。 -->
      <!-- 2026-10-05 (M6a): 显示设置（背景/字号/字体）原先是三整行，直接占掉「我的会员」首屏。
           折叠成原生 <details>（默认收起）⇒ 首屏回到"账户 + 会员卡"本身；点开才占版面。 -->
      <details class="mb-settings">
        <summary class="mb-settings-sum"><i class="fa fa-sliders"></i> 显示设置（背景 / 字号 / 字体）</summary>
      <div class="mb-setting-row">
          <span class="mb-setting-label"><i class="fa fa-desktop"></i> 背景</span>
          <button
            v-for="b in BGS" :key="b.key"
            class="mb-bg-btn" :class="{ active: bg === b.key }"
            :title="b.label" :aria-label="'背景：' + b.label"
            :aria-pressed="bg === b.key ? 'true' : 'false'"
            @click="setBg(b.key)"
          >
            <span class="mb-bg-chip" :style="{ background: b.color }"></span>
            <span>{{ b.key === 'dark' ? '深色' : '浅色' }}</span>
          </button>
        </div>
        <div class="mb-setting-row">
          <span class="mb-setting-label"><i class="fa fa-font"></i> 字号</span>
          <button
            v-for="f in FONTS" :key="f.key"
            class="mb-set-btn" :class="{ active: font === f.key }"
            :style="{ fontSize: f.key === 'sm' ? '12px' : f.key === 'lg' ? '16px' : '13px' }"
            :title="f.label" :aria-label="'字号：' + f.label" @click="setFont(f.key)"
          >
            A
          </button>
        </div>
        <div class="mb-setting-row">
          <span class="mb-setting-label"><i class="fa fa-text-height"></i> 字体</span>
          <button
            v-for="ff in FONT_FAMILIES" :key="ff.key"
            class="mb-fontfam" :class="{ active: fontFam === ff.key }"
            :title="ff.desc" :aria-label="'字体：' + ff.label" @click="setFontFam(ff.key)"
          >
            <span class="ff-label" :style="{ fontFamily: ff.family }">{{ ff.label }}</span>
            <span class="ff-desc">{{ ff.desc }}</span>
          </button>
        </div>
        <div class="mb-acc-tip">显示设置即刻保存：背景明暗跟随账号同步，换设备登录也保持一致。</div>
      </details>
    </div>

    <div v-if="loading" class="loading-placeholder"><div class="spinner"></div><div>加载会员信息...</div></div>

    <template v-else>
      <!-- 概览卡: 等级 + 到期倒计时 -->
      <div class="mb-hero" :class="'lv-' + level">
        <div class="mb-hero-left">
          <div class="mb-crown"><i class="fa" :class="level === 2 ? 'fa-certificate' : level === 1 ? 'fa-star' : 'fa-user'"></i></div>
          <div>
            <div class="mb-name">{{ username }}</div>
            <div class="mb-level">{{ memberLabel }}</div>
          </div>
        </div>
        <div class="mb-hero-right">
          <template v-if="member.permanent">
            <div class="mb-days-txt">永久有效</div>
          </template>
          <template v-else-if="member.expired">
            <div class="mb-days-txt danger">已过期</div>
            <div class="mb-days-sub">{{ member.expire_date }}</div>
          </template>
          <template v-else>
            <div class="mb-days-txt">剩余 <b>{{ member.days_left }}</b> 天</div>
            <div class="mb-days-sub">到期 {{ member.expire_date }}</div>
          </template>
        </div>
      </div>

      <!-- 到期提醒条 -->
      <div v-if="!member.permanent && !member.expired && member.days_left <= 3 && !member.privileged" class="mb-warn">
        <i class="fa fa-exclamation-triangle"></i>
        会员将在 {{ member.days_left }} 天后到期，到期后每日免费次数降为 {{ freePicker }} 次。
        可<b>邀请好友</b>（每位 +{{ invite.reward_days }} 天）或联系管理员开通。
      </div>
      <div v-if="member.expired" class="mb-warn strong">
        <i class="fa fa-times-circle"></i>
        会员已过期，当前按免费用户计次。邀请好友或联系管理员开通即可恢复不限次。
      </div>

      <!-- 今日配额 -->
      <div class="mb-card">
        <div class="mb-card-head">
          <span class="mb-card-title"><i class="fa fa-tachometer"></i> 今日可用次数</span>
          <span class="mb-card-note">每日 0 点重置（北京时间）</span>
        </div>
        <div class="mb-quota-grid">
          <div v-for="q in quota" :key="q.feature" class="mb-quota-item" :class="{ exhausted: !q.privileged && q.remain <= 0 }">
            <div class="mb-q-name">{{ q.label }}</div>
            <div class="mb-q-num">
              <template v-if="q.privileged"><b>不限次</b></template>
              <template v-else><b :class="{ zero: q.remain <= 0 }">{{ q.remain }}</b><span>/ {{ q.limit }}</span></template>
            </div>
            <div v-if="!q.privileged" class="mb-q-bar"><i :style="{ width: pct(q) + '%' }"></i></div>
            <div v-if="q.bonus > 0" class="mb-q-bonus">含签到奖励 +{{ q.bonus }}</div>
          </div>
        </div>
      </div>

      <!-- 签到 -->
      <!-- 2026-10-05 (M7): 不限次会员不显示签到 —— 原先那三格写着「不限次」，下面却给一个大红
           按钮「签到领 3 次选股额度」，自相矛盾（用户会怀疑额度体系到底有没有生效）。
           签到只对免费/试用账号有实际意义。 -->
      <div v-if="!user.isVipOrPaid" class="mb-card">
        <div class="mb-card-head">
          <span class="mb-card-title"><i class="fa fa-calendar-check-o"></i> 每日签到</span>
          <span class="mb-card-note">连续签到 {{ checkin.streak }} 天</span>
        </div>
        <button class="mb-checkin-btn" :disabled="checkin.done_today || checkinBusy" @click="doCheckin">
          <template v-if="checkin.done_today"><i class="fa fa-check"></i> 今日已签到</template>
          <template v-else-if="checkinBusy">签到中...</template>
          <template v-else><i class="fa fa-gift"></i> 签到领 {{ checkin.reward }} 次选股额度</template>
        </button>
        <div v-if="history.length" class="mb-checkin-hist">
          <span v-for="h in history" :key="h.date" class="mb-hist-dot" :class="{ on: h.reward > 0 }" :title="h.date + ' +' + h.reward">{{ h.date.slice(8) }}</span>
        </div>
      </div>

      <!-- 邀请 -->
      <div class="mb-card">
        <div class="mb-card-head">
          <span class="mb-card-title"><i class="fa fa-user-plus"></i> 邀请好友</span>
          <span class="mb-card-note">每成功邀请 1 位，双方各得 {{ invite.reward_days }} 天会员</span>
        </div>
        <div class="mb-invite-row">
          <div class="mb-code-box">
            <span class="mb-code-label">我的邀请码</span>
            <span class="mb-code">{{ invite.code || '——' }}</span>
          </div>
          <button class="mb-mini-btn" @click="copyCode"><i class="fa fa-copy"></i> 复制邀请码</button>
          <button class="mb-mini-btn" @click="copyLink"><i class="fa fa-link"></i> 复制邀请链接</button>
          <button class="mb-mini-btn ghost" @click="doRefreshCode"><i class="fa fa-refresh"></i> 更换邀请码</button>
        </div>
        <div class="mb-invite-stats">
          <span>已邀请 <b>{{ invite.invited_count }}</b> 人</span>
          <span class="sep">·</span>
          <span>累计获得 <b>{{ invite.earned_days }}</b> 天会员</span>
        </div>
        <div v-if="invite.invitees && invite.invitees.length" class="mb-invitee-list">
          <div v-for="(it, i) in invite.invitees" :key="i" class="mb-invitee">
            <i class="fa fa-user-circle-o"></i>
            <span class="mb-invitee-name">{{ it.username || '已注销' }}</span>
            <span class="mb-invitee-time">{{ fmtDay(it.created_at) }}</span>
          </div>
        </div>
        <div v-else class="mb-empty-small">还没有邀请记录，把邀请码发给好友即可开始</div>
      </div>

      <!-- 权益对照 -->
      <div class="mb-card">
        <div class="mb-card-head">
          <span class="mb-card-title"><i class="fa fa-table"></i> 权益对照</span>
        </div>
        <div class="mb-table-wrap">
          <table class="mb-table">
            <thead>
              <tr>
                <th>功能</th>
                <th :class="{ mine: level === 0 }">{{ plans.free.label }}</th>
                <th :class="{ mine: level === 1 }">{{ plans.member.label }}</th>
                <th :class="{ mine: level === 2 }">{{ plans.vip.label }}</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>选股快照</td>
                <td :class="{ mine: level === 0 }">{{ lim(plans.free.picker) }}</td>
                <td :class="{ mine: level === 1 }">{{ lim(plans.member.picker) }}</td>
                <td :class="{ mine: level === 2 }">{{ lim(plans.vip.picker) }}</td>
              </tr>
              <tr>
                <td>AI 预测</td>
                <td :class="{ mine: level === 0 }">{{ lim(plans.free.aipick) }}</td>
                <td :class="{ mine: level === 1 }">{{ lim(plans.member.aipick) }}</td>
                <td :class="{ mine: level === 2 }">{{ lim(plans.vip.aipick) }}</td>
              </tr>
              <tr>
                <td>竞价异动</td>
                <td :class="{ mine: level === 0 }">{{ lim(plans.free.auction) }}</td>
                <td :class="{ mine: level === 1 }">{{ lim(plans.member.auction) }}</td>
                <td :class="{ mine: level === 2 }">{{ lim(plans.vip.auction) }}</td>
              </tr>
              <tr>
                <td>签到奖励</td>
                <td :class="{ mine: level === 0 }" colspan="3">每日 +{{ plans.checkin_bonus }} 次选股额度</td>
              </tr>
              <tr>
                <td>邀请奖励</td>
                <td :class="{ mine: level === 0 }" colspan="3">每邀请 1 人，双方各 +{{ plans.invite_reward_days }} 天会员</td>
              </tr>
              <tr>
                <td>新用户赠送</td>
                <td :class="{ mine: level === 0 }" colspan="3">{{ plans.new_user_days }} 天会员完整体验</td>
              </tr>
            </tbody>
          </table>
        </div>
        <!-- 2026-10-05 (S4): 开通/续费自助化。
             原先这里只有一行纯文本「开通 / 续费会员请联系管理员 微信: poet-1986」——
             不可点、不可复制, 用户要长按选中手抄一串微信号; 且页面上**没有任何**"立即开通"按钮,
             付费只能靠人工承接。现在: 可点击复制 + 价格区(主人填了才展示) + 到期顺延说明。 -->
        <div class="mb-open">
          <div class="mb-open-head">
            <!-- 图标用 fa-money: fa-credit-card 在 FA4.7 本地子集里没有字形(_verify/fa_guard.js 会拦) -->
            <span class="mb-open-title"><i class="fa fa-money" aria-hidden="true"></i> 开通 / 续费会员</span>
            <span class="mb-open-sub">通过客服微信办理，付款后即时开通；续费后到期时间自动顺延，可在本页查看。</span>
          </div>
          <!-- 价格表：数据来自本文件 script 里的 PLANS_PRICE 常量（2026-10-05 主人给定
               月卡 ¥218 / 季卡 ¥588）。改价只动那一处常量，模板不用碰。 -->
          <ul v-if="PLANS_PRICE.length" class="mb-price">
            <li v-for="p in PLANS_PRICE" :key="p.label">
              <b>{{ p.label }}</b>
              <em>¥{{ p.price }}</em>
              <span>{{ p.days }} 天<template v-if="p.perDay"> · 折合 ¥{{ p.perDay }}/天</template></span>
            </li>
          </ul>
          <div class="mb-open-acts">
            <button type="button" class="mb-btn-cta" @click="copySupportWx">
              <i class="fa fa-weixin" aria-hidden="true"></i> 复制客服微信 {{ SUPPORT_WECHAT }}
            </button>
            <span class="mb-open-hint">加微信办理 · 付款后即时开通</span>
          </div>
          <!-- 2026-10-05 主人提供客服二维码：付款前最需要"扫一下就能加上"，故与本区并排展示 -->
          <div class="mb-open-qr">
            <ContactQr :width="186" />
          </div>
        </div>
      </div>
    </template>

    <!-- 账户弹层（2026-09-27 v4.11.65 由 NavBar 迁来：顶部导航栏已无触发按钮，
         这两个弹层现在只由本页的「个人信息 / 修改密码」按钮打开）。 -->
    <ChangePwdModal ref="changePwdModal" />
    <ProfileModal ref="profileModal" />
  </div>
</template>

<script setup>
// 「我的」页（一级分组「我的」的入口，meta.group='me'）
//   ★ 2026-09-27 v4.11.65：顶部导航栏的账户区块（用户名下拉：我的会员/个人信息/修改密码/
//     退出登录/字号/字体族）整块迁入本页，见模板里的「账户」卡片。
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { memberOverview, memberPlans, memberCheckin, doCheckin as apiCheckin, refreshInvite } from '../api/member'
import { trackUsageOnce } from '../api/activity'
import { logoutApi } from '../api/auth'
import { useUserStore } from '../stores/user'
import { useTheme, BGS, FONTS, FONT_FAMILIES } from '../composables/useTheme'
import { showToast } from '../utils/toast'
import ChangePwdModal from '../components/ChangePwdModal.vue'
import ProfileModal from '../components/ProfileModal.vue'
// 2026-10-05 (S4): 开通/续费自助化 —— 客服微信号单一来源。
//   ⚠️ 本文件下方**已自带** async copyText(text, okMsg)（含 secureContext 判断 + execCommand 兜底），
//   故这里只引 contact 常量，不再从 utils/tdx 引同名函数（同名会解析冲突）。
import { SUPPORT_WECHAT } from '../utils/contact'
// 2026-10-05: 客服二维码（缺图时组件自身不渲染）
import ContactQr from '../components/ContactQr.vue'

const router = useRouter()
const user = useUserStore()

// 🔴 2026-10-05 (S4) **在售套餐与价格 —— 待主人填写**。
//   格式: [{ label:'月卡', price:'¥99', days:30 }, ...]；**留空数组则整块价格区自动隐藏**
//   （绝不展示编造的价钱）。填好后前端立刻展示，无需改后端。
//   后端 /api/member/plans 目前只有配额字段、没有价格字段，故价格由前端常量承载。
// 2026-10-05 (S4) 主人给定在售套餐与价格：月卡 ¥218 / 季卡 ¥588。
//   perDay 是"折合每天"（月卡 218/30 ≈ 7.3、季卡 588/90 ≈ 6.5）—— 让季卡更划算这件事
//   一眼可见，是纯前端展示值，不是后端口径。改价只动这个数组。
const PLANS_PRICE = [
  { label: '月卡', price: 218, days: 30, perDay: '7.3' },
  { label: '季卡', price: 588, days: 90, perDay: '6.5' },
]

function copySupportWx() {
  copyText(SUPPORT_WECHAT, '客服微信已复制')
}
// 显示设置三项：背景明暗 / 字号 / 字体族（2026-10-04 起背景也在这里 —— 顶部圆点已移除）
const { bg, setBg, font, fontFam, setFont, setFontFam } = useTheme()
// 账户弹层（2026-09-27 v4.11.65 由 NavBar 迁来）
const changePwdModal = ref(null)
const profileModal = ref(null)

// 权益对照表的展示默认值。★ 必须是一个可复用的常量，不能只在 ref 里写一次字面量 —— 见 load() 的合并注释。
const PLANS_DEFAULT = { free: {}, member: {}, vip: {}, checkin_bonus: 3, invite_reward_days: 5, new_user_days: 5 }

const loading = ref(true)
const checkinBusy = ref(false)
const member = ref({})
const quota = ref([])
const checkin = ref({ done_today: false, reward: 3, streak: 0 })
const invite = ref({ code: '', invited_count: 0, earned_days: 0, reward_days: 5, invitees: [] })
const plans = ref({ ...PLANS_DEFAULT })
const history = ref([])

const username = computed(() => member.value.username || '')
const level = computed(() => member.value.member_level || 0)
const memberLabel = computed(() => member.value.member_label || '免费试用')
const freePicker = computed(() => plans.value.free.picker ?? 3)

const QUOTA_ORDER = { picker: '选股', aipick: 'AI 预测', auction: '竞价异动' }

function pct(q) {
  if (!q || !q.limit) return 0
  return Math.max(0, Math.min(100, Math.round((q.remain / q.limit) * 100)))
}
function lim(v) {
  return v === -1 || v === undefined || v === null ? '不限次' : v + ' 次/天'
}
function fmtDay(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return `${d.getMonth() + 1}/${d.getDate()}`
}

async function load() {
  loading.value = true
  try {
    const d = await memberOverview()
    member.value = d.member || {}
    quota.value = (d.quota || []).map((q) => ({
      ...q,
      label: q.label || QUOTA_ORDER[q.feature] || q.feature,
    }))
    checkin.value = d.checkin || checkin.value
    invite.value = d.invite || invite.value
  } catch (e) {
    showToast('❌ 会员信息加载失败: ' + (e.message || ''), 'error')
  } finally {
    loading.value = false
  }
  // ★ 2026-09-27 v4.11.65：必须是「与默认值合并」而不是整体替换。
  //   权益对照表模板直接读 plans.free.label / plans.member.picker / plans.vip.picker
  //   （plans 初值里 free/member/vip 是 {}）。一旦接口返回的形状不完整（缺 free/vip），
  //   整体替换会让模板抛 `Cannot read properties of undefined (reading 'label')`
  //   ⇒ **整个「我的」页白屏**（连账户卡片都渲染不出来）。
  //   该缺陷是 scripts/scroll_smoke.js（Chromium 真渲染探针）用"不完整桩响应"当场抓到的
  //   —— SSR 冒烟测试（_verify/nav.spec.js）碰不到它，因为 SSR 不跑 onMounted 里的 load()。
  try { plans.value = { ...PLANS_DEFAULT, ...((await memberPlans()) || {}) } } catch { /* 用默认 */ }
  try {
    const c = await memberCheckin()
    history.value = c.history || []
    checkin.value = { ...checkin.value, ...c }
  } catch { /* 忽略 */ }
}

async function doCheckin() {
  if (checkinBusy.value || checkin.value.done_today) return
  checkinBusy.value = true
  try {
    const d = await apiCheckin()
    checkin.value.done_today = true
    checkin.value.streak = d.streak || checkin.value.streak
    if (d.quota) {
      quota.value = quota.value.map((q) => (q.feature === d.quota.feature ? { ...q, ...d.quota, label: q.label } : q))
    }
    showToast('✅ ' + (d.msg || '签到成功'), 'success')
    const c = await memberCheckin()
    history.value = c.history || []
  } catch (e) {
    if (e && e.done_today) {
      checkin.value.done_today = true
      showToast(e.message || '今日已签到', 'info')
    } else {
      showToast('❌ ' + (e.message || '签到失败'), 'error')
    }
  } finally {
    checkinBusy.value = false
  }
}

function inviteUrl(code) {
  return window.location.origin + '/login?invite=' + encodeURIComponent(code)
}

async function copyText(text, okMsg) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
    } else {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    showToast(okMsg, 'success')
  } catch (e) {
    showToast('复制失败，请手动选择：' + text, 'error')
  }
}

function copyCode() {
  if (!invite.value.code) return showToast('邀请码尚未生成', 'error')
  copyText(invite.value.code, '✅ 邀请码已复制')
}
function copyLink() {
  if (!invite.value.code) return showToast('邀请码尚未生成', 'error')
  copyText(inviteUrl(invite.value.code), '✅ 邀请链接已复制，发给好友即可')
}

async function doRefreshCode() {
  if (!confirm('更换后旧邀请码立即失效（已注册的好友不受影响），确定更换？')) return
  try {
    const d = await refreshInvite()
    invite.value.code = d.invite_code || invite.value.code
    showToast('✅ 邀请码已更换', 'success')
  } catch (e) {
    showToast('❌ ' + (e.message || '更换失败'), 'error')
  }
}

// 退出登录（2026-09-27 v4.11.65 由 NavBar 迁来，逻辑与原来一致）
function logout() {
  if (!confirm('确定退出当前账号？')) return
  // 2026-09-22 v4.11.35: 先通知后端作废 token 并落一条「主动退出」记录,
  // 再清本地会话。后端失败不阻塞退出(本地清理是用户能感知的那一步)。
  logoutApi().finally(() => {
    user.clearSession()
    showToast('已退出登录', 'success')
    router.replace('/login')
  })
}

onMounted(() => {
  // 2026-09-22 v4.11.35: 打开「我的会员」算一次使用(Once 版防止刷新重复上报)。
  // ★ 这里**不**再单独给「签到」加一次计数 —— 签到本身已有 user_checkin 台账,
  //   若页面打开+签到各记一次, 同一个动作会变成 2 次, 数字就不可比了。
  trackUsageOnce('member')
  load()
})
</script>

<style scoped>
.member-page { max-width: 860px; margin: 0 auto; padding: var(--s4) var(--s3) var(--s8); display: flex; flex-direction: column; gap: var(--s4); }

/* 概览卡 */
.mb-hero {
  display: flex; justify-content: space-between; align-items: center;
  gap: var(--s3); flex-wrap: wrap;
  padding: var(--s4) var(--s5); border-radius: var(--r-lg);
  background: var(--bg-panel); border: 1px solid var(--border-soft);
  position: relative; overflow: hidden;
}
.mb-hero.lv-1 { border-color: rgba(var(--accent-rgb), .5); box-shadow: 0 6px 24px rgba(var(--accent-rgb), .12); }
.mb-hero.lv-2 { border-color: rgba(255, 176, 32, .55); box-shadow: var(--sh-2); }
.mb-hero-left { display: flex; align-items: center; gap: var(--s4); }
.mb-crown {
  width: 46px; height: 46px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: var(--fs-2xl); color: var(--accent);
  background: rgba(var(--accent-rgb), .12);
}
.mb-hero.lv-2 .mb-crown { color: var(--warn-amber); background: rgba(255, 176, 32, .14); }
.mb-name { font-size: var(--fs-lg); font-weight: 700; color: var(--text-main); }
.mb-level { font-size: var(--fs-sm); color: var(--text-muted); margin-top: 2px; }
.mb-hero-right { text-align: right; }
.mb-days-txt { font-size: var(--fs-md); color: var(--text-secondary); }
.mb-days-txt b { font-size: var(--fs-display); color: var(--accent-deep); }
.mb-days-txt.danger { color: var(--brand-soft); font-weight: 700; }
.mb-days-sub { font-size: var(--fs-xs); color: var(--text-muted); margin-top: 2px; }

/* 提醒条 */
.mb-warn {
  display: flex; align-items: center; gap: var(--s2); flex-wrap: wrap;
  font-size: var(--fs-sm); line-height: 1.6;
  padding: var(--s2) var(--s4); border-radius: var(--r-lg);
  background: rgba(255, 176, 32, .1); border: 1px solid rgba(255, 176, 32, .35);
  color: var(--text-secondary);
}
.mb-warn.strong { background: rgba(255, 106, 106, .1); border-color: rgba(255, 106, 106, .35); }
.mb-warn b { color: var(--accent-deep); margin: 0 2px; }

/* ===== 账户卡片（2026-09-27 v4.11.65 由顶部导航栏 NavBar 整块迁入） ===== */
.mb-acc-badges { display: flex; flex-wrap: wrap; gap: var(--s2); margin-bottom: var(--s3); }
.mb-acc-actions { display: flex; flex-wrap: wrap; gap: var(--s2); }
.mb-mini-btn.danger { border-color: rgba(255, 106, 106, .6); color: var(--brand-soft); }
/* 2026-10-05 (M6a): 折叠后的「显示设置」 */
.mb-settings { margin-top: var(--s3); border-top: 1px dashed var(--border-soft); padding-top: var(--s2); }
.mb-settings-sum {
  cursor: pointer; list-style: none; font-size: var(--fs-sm); color: var(--text-muted);
  display: inline-flex; align-items: center; gap: var(--s1); padding: var(--s1) 0;
}
.mb-settings-sum::-webkit-details-marker { display: none; }
.mb-settings-sum:hover { color: var(--accent); }
.mb-settings[open] .mb-setting-row:first-of-type { margin-top: var(--s2); }
.mb-setting-row { display: flex; align-items: center; gap: var(--s2); flex-wrap: wrap; margin-top: var(--s3); }
.mb-setting-label {
  display: inline-flex; align-items: center; gap: var(--s2);
  font-size: var(--fs-xs); color: var(--text-muted); min-width: 56px; white-space: nowrap;
}
.mb-setting-label i { width: 14px; text-align: center; }
.mb-set-btn {
  min-width: 24px; height: 24px; line-height: 1;
  border: 1px solid var(--border-soft); border-radius: var(--r-lg);
  background: transparent; color: var(--text-secondary);
  font-weight: 600; cursor: pointer; padding: 0 var(--s2);
  transition: border-color .15s, background .15s, color .15s;
}
.mb-set-btn:hover { border-color: var(--accent); }
.mb-set-btn.active { border-color: var(--accent); background: var(--accent-solid); color: #fff; }
/* 2026-10-04 背景明暗：由顶部导航栏圆点搬来。带真实色块预览 ⇒ 比两个纯圆点更好认，
   且「深色/浅色」有文字标签（原来的圆点只能靠 tooltip，触屏没 tooltip 就等于没标签）。 */
.mb-bg-btn {
  display: inline-flex; align-items: center; gap: var(--s2); padding: var(--s1) var(--s2) var(--s1) var(--s1);
  border: 1px solid var(--border-soft); border-radius: var(--r-md);
  background: transparent; color: var(--text-secondary);
  cursor: pointer; font-size: var(--fs-sm); line-height: 1;
  transition: border-color .15s, background .15s, color .15s;
}
.mb-bg-btn:hover { border-color: var(--accent); }
.mb-bg-btn.active { border-color: var(--accent); background: var(--accent-bg2); color: var(--accent-text); }
.mb-bg-chip {
  width: 16px; height: 16px; border-radius: var(--r-sm); flex: 0 0 auto;
  border: 1px solid var(--border-soft);
}
/* 浅色主题下深色块自带描边更清楚（避免白底上看不见块边界） */
body[data-bg="light"] .mb-bg-btn.active { background: rgba(11, 134, 200, .12); color: #0b5fa5; }
.mb-fontfam {
  display: inline-flex; align-items: center; gap: var(--s2); padding: var(--s1) var(--s2); border: 1px solid var(--border-soft); border-radius: var(--r-md);
  background: transparent; color: var(--text-secondary);
  cursor: pointer; font-size: var(--fs-sm);
  transition: border-color .15s, background .15s, color .15s;
}
.mb-fontfam:hover { border-color: var(--accent); }
.mb-fontfam.active {
  border-color: var(--accent);
  background: color-mix(in srgb, var(--accent) 16%, transparent);
  color: var(--text-main);
}
.mb-fontfam .ff-label { font-weight: 600; }
.mb-fontfam .ff-desc { font-size: var(--fs-xs); color: var(--text-muted); }
.mb-acc-tip { margin-top: var(--s3); font-size: var(--fs-xs); color: var(--text-muted); }
/* 2026-10-05 晚：显示设置各控件的**触屏命中区**补到 ≥34px。
   实测桌面尺寸：折叠摘要 193×28、背景 64×26、字号 27×24、字体族 178×28 ——
   鼠标够用，但手机（H5 + APK，本产品的真正主战场）点不中；
   而这块是**手机端唯一**能改背景/字号/字体的入口（顶栏那两个圆点 10-04 已按主人指令收回这里）。
   标准沿用主人 2026-10-05 定下的「移动端命中区 ≥34px」（v4.11.88 已对快讯行/搜索框/
   PWA 关闭钮做过同一件事）。仅 ≤768 生效 ⇒ 桌面版观感零变化。 */
@media (max-width: 768px) {
  .mb-settings-sum { min-height: 40px; padding: var(--s2) 0; }
  .mb-set-btn { min-width: 34px; height: 34px; }
  .mb-bg-btn { min-height: 34px; }
  .mb-fontfam { min-height: 34px; }
}

/* 会员徽标（类名与迁出前一致，便于对照历史截图） */
.member-badge {
  display: inline-flex; align-items: center; gap: var(--s1);
  font-size: var(--fs-xs); font-weight: 600;
  border-radius: var(--r-lg); padding: 1px var(--s2); white-space: nowrap;
}
.vip-badge { background: #ffd70022; color: var(--gold-deep); border: 1px solid #ffd70088; }
.paid-badge { background: rgba(255, 90, 90, 0.15); color: var(--brand-soft); border: 1px solid rgba(255, 90, 90, 0.5); }
.admin-badge { background: rgba(90, 160, 255, 0.15); color: var(--accent-text); border: 1px solid rgba(90, 160, 255, 0.5); }
.trial-badge { background: rgba(255, 180, 0, 0.12); color: var(--gold); border: 1px solid rgba(255, 180, 0, 0.4); }
.renew-badge {
  background: rgba(255, 160, 40, 0.15); color: #ffa028;
  border: 1px solid rgba(255, 160, 40, 0.55);
  animation: renew-pulse 1.8s infinite;
}
@keyframes renew-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.6; } }
body[data-bg="light"] .renew-badge { color: #b05e00; border-color: #c07a10; }
body[data-bg="light"] .mb-fontfam { background: #f7f8fb; }

/* 通用卡片 */
.mb-card {
  background: var(--bg-panel); border: 1px solid var(--border-soft);
  border-radius: var(--r-lg); padding: var(--s4) var(--s4);
}
.mb-card-head { display: flex; justify-content: space-between; align-items: baseline; gap: var(--s2); margin-bottom: var(--s3); flex-wrap: wrap; }
.mb-card-title { font-size: var(--fs-md); font-weight: 700; color: var(--text-main); }
.mb-card-title i { color: var(--accent); margin-right: var(--s2); }
.mb-card-note { font-size: var(--fs-xs); color: var(--text-muted); }

/* 配额 */
.mb-quota-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: var(--s3); }
.mb-quota-item {
  border: 1px solid var(--border-soft); border-radius: var(--r-lg); padding: var(--s3) var(--s4);
  background: rgba(255, 255, 255, .015);
}
.mb-quota-item.exhausted { border-color: rgba(255, 106, 106, .35); }
.mb-q-name { font-size: var(--fs-sm); color: var(--text-muted); margin-bottom: var(--s2); }
.mb-q-num { font-size: var(--fs-base); color: var(--text-secondary); }
.mb-q-num b { font-size: var(--fs-2xl); color: var(--accent-deep); margin-right: var(--s1); }
.mb-q-num b.zero { color: var(--brand-soft); }
.mb-q-bar { height: 4px; border-radius: var(--r-sm); background: var(--bg-input); margin-top: var(--s2); overflow: hidden; }
.mb-q-bar i { display: block; height: 100%; background: var(--accent-solid); border-radius: var(--r-sm); transition: width .3s; }
.mb-q-bonus { font-size: var(--fs-xs); color: var(--down); margin-top: var(--s2); }

/* 签到 */
.mb-checkin-btn {
  width: 100%; padding: var(--s3) 0; border: none; border-radius: var(--r-md);
  background: var(--accent-solid); color: #fff; font-size: var(--fs-md); cursor: pointer;
  transition: opacity .15s;
}
.mb-checkin-btn:hover:not(:disabled) { opacity: .88; }
.mb-checkin-btn:disabled { background: var(--bg-input); color: var(--text-muted); cursor: default; }
.mb-checkin-hist { display: flex; gap: var(--s2); margin-top: var(--s2); }
.mb-hist-dot {
  width: 26px; height: 26px; border-radius: 50%; font-size: var(--fs-xs);
  display: flex; align-items: center; justify-content: center;
  background: var(--bg-input); color: var(--text-muted);
}
.mb-hist-dot.on { background: rgba(43, 182, 115, .16); color: var(--down); font-weight: 700; }

/* 邀请 */
.mb-invite-row { display: flex; gap: var(--s2); align-items: center; flex-wrap: wrap; }
.mb-code-box {
  display: flex; align-items: center; gap: var(--s2);
  padding: var(--s2) var(--s3); border-radius: var(--r-md);
  background: rgba(var(--accent-rgb), .08); border: 1px dashed rgba(var(--accent-rgb), .4);
}
.mb-code-label { font-size: var(--fs-xs); color: var(--text-muted); }
.mb-code { font-size: var(--fs-lg); font-weight: 700; color: var(--accent-deep); letter-spacing: 2px; }
.mb-mini-btn {
  padding: var(--s2) var(--s3); font-size: var(--fs-sm); border-radius: var(--r-md); cursor: pointer;
  border: 1px solid var(--accent); background: transparent; color: var(--accent);
}
.mb-mini-btn.ghost { border-color: var(--border-soft); color: var(--text-muted); }
.mb-mini-btn:hover { opacity: .82; }
.mb-invite-stats { font-size: var(--fs-sm); color: var(--text-secondary); margin-top: var(--s3); }
.mb-invite-stats b { color: var(--accent-deep); font-size: var(--fs-lg); margin: 0 2px; }
.mb-invite-stats .sep { margin: 0 var(--s2); color: var(--text-muted); }
.mb-invitee-list { margin-top: var(--s2); display: flex; flex-direction: column; gap: var(--s2); max-height: 190px; overflow-y: auto; }
.mb-invitee { display: flex; align-items: center; gap: var(--s2); font-size: var(--fs-sm); color: var(--text-secondary); }
.mb-invitee i { color: var(--accent); }
.mb-invitee-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mb-invitee-time { font-size: var(--fs-xs); color: var(--text-muted); }
.mb-empty-small { margin-top: var(--s2); font-size: var(--fs-sm); color: var(--text-muted); }

/* 权益表 */
.mb-table-wrap { overflow-x: auto; }
.mb-table { width: 100%; border-collapse: collapse; font-size: var(--fs-sm); }
.mb-table th, .mb-table td { padding: var(--s2) var(--s2); text-align: center; border-bottom: 1px solid var(--border-soft); }
.mb-table th:first-child, .mb-table td:first-child { text-align: left; color: var(--text-secondary); }
.mb-table th { color: var(--text-muted); font-weight: 600; }
.mb-table td { color: var(--text-secondary); }
.mb-table .mine { background: rgba(var(--accent-rgb), .09); color: var(--text-main); font-weight: 600; }
/* ===== 2026-10-05 (S4) 开通 / 续费区 =====
   替换原 `.mb-contact`（一行纯文本微信号）。要点: 主色实心按钮(白字, 对比 ≥4.5:1)
   + 价格区(填了才显示, 走 dashed 主色描边表示"这里是转化入口")。 */
.mb-open {
  margin-top: var(--s4); padding: var(--s3) var(--s4);
  background: rgba(var(--accent-rgb), .06);
  border: 1px dashed var(--accent-border); border-radius: var(--r-md);
}
.mb-open-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--s1) var(--s3); }
.mb-open-title { font-size: var(--fs-md); font-weight: 700; color: var(--text-main); }
.mb-open-title i { color: var(--accent); margin-right: var(--s1); }
.mb-open-sub { font-size: var(--fs-xs); color: var(--text-muted); line-height: 1.7; }
.mb-price { list-style: none; display: flex; flex-wrap: wrap; gap: var(--s2); margin: var(--s3) 0 0; padding: 0; }
.mb-price li {
  display: inline-flex; align-items: baseline; gap: var(--s2);
  padding: var(--s2) var(--s3);
  background: var(--bg-panel); border: 1px solid var(--border-soft); border-radius: var(--r-md);
}
.mb-price b { color: var(--text-main); font-size: var(--fs-sm); }
.mb-price em { color: var(--accent); font-style: normal; font-weight: 700; }
.mb-price span { color: var(--text-dim); font-size: var(--fs-xs); }
.mb-open-acts { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s3); margin-top: var(--s3); }
.mb-open-qr { margin-top: var(--s3); }   /* 客服二维码（2026-10-05） */
.mb-btn-cta {
  display: inline-flex; align-items: center; gap: var(--s1);
  background: var(--accent-deep2); color: #fff; border: 1px solid transparent;
  border-radius: var(--r-md); padding: var(--s2) var(--s4);
  font-size: var(--fs-sm); font-weight: 700; cursor: pointer;
}
.mb-btn-cta:hover { background: var(--accent-deep); }
.mb-open-hint { font-size: var(--fs-xs); color: var(--text-dim); }
@media (max-width: 768px) {
  .mb-btn-cta { width: 100%; justify-content: center; }
  .mb-open-hint { width: 100%; text-align: center; }
}

/* 浅色主题微调 */
body[data-bg="light"] .mb-quota-item { background: rgba(0, 0, 0, .015); }
body[data-bg="light"] .mb-code-box { background: rgba(var(--accent-rgb), .08); }
</style>
