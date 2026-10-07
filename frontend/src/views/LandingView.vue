<script setup>
// 2026-10-05 (S1): 匿名落地页 —— 新用户第一眼看到的东西。
//
// 设计约束（照仓库既有约定）：
//   ① 只用现有设计 token（--accent/--bg-panel/--text-*/--fs-*/--s*），不引入新配色，
//      免得踩 _verify/color_guard.js 的语义色棘轮；
//   ② 断点只用仓库已有的 768 / 1100，不新增（_verify/breakpoint_guard.js 棘轮）；
//   ③ 只用**匿名可访问**的接口，且失败一律静默降级 —— 落地页是转化入口，绝不能被接口拖垮：
//        · GET /api/register/config  → 赠送天数 / 邀请奖励天数（backend/app/api/auth.py:244 无 Depends）
//        · GET /api/member/plans     → 三档权益真实配额（backend/app/api/member.py:139 无 Depends）
//   ④ 不放任何"假数据预览"。商品截图带用户水印、真实行情需鉴权 ⇒ 一律不伪造，
//      改为如实描述能力与口径（这也是合规页要讲清的东西）。
import { ref, onMounted } from 'vue'
import { memberPlans } from '../api/member'
import { usePublicConfig } from '../composables/usePublicConfig'
import ContactQr from '../components/ContactQr.vue'
import { copyText } from '../utils/tdx'
import { SUPPORT_WECHAT, OPEN_TIP } from '../utils/contact'

// 赠送天数/注册开关走匿名公共配置（模块级单例 + 请求去重，顶栏也用同一份 ⇒ 全站只请求一次）
const { giftDays, inviteRewardDays, regOpen, load: loadPublicConfig } = usePublicConfig()
const plans = ref(null)     // null = 加载中；对象 = 后端真实配额

// 能力墙：如实描述产品实际具备的能力（每条都能在登录后找到对应页面）
const FEATURES = [
  { icon: 'fa-filter',        title: '竞价选股',   desc: '涨停基因 × 高开 ≥3% × 竞价放量 5~10%，9:25 定格后不再改动' },
  { icon: 'fa-bolt',          title: '竞价异动',   desc: '封单 / 爆量 / 抢筹 / 委买 / 净额五张榜，含连续多日封单对比' },
  { icon: 'fa-sitemap',       title: '连板天梯',   desc: '按连板高度分层排列，晋级率与炸板率一眼看清' },
  { icon: 'fa-exchange',      title: '龙虎榜',     desc: '席位 → 个股层级树，按通达信口径对齐，默认只展开到席位' },
  { icon: 'fa-lightbulb-o',   title: '双脑竞价',   desc: '情绪 / 资金 / 晋级率 / 承接 四维打分，盘前快速定调' },
  { icon: 'fa-newspaper-o',   title: '盘前资讯',   desc: '头条 / 快讯 / 明天炒什么 / 大V复盘，一键扫完' },
]

const FACTS = [
  { icon: 'fa-clock-o',   t: '9:25 定格',    d: '竞价口径按交易日 9:25 定格，零值不回退昨日' },
  { icon: 'fa-refresh',   t: '30s 自动刷新', d: '盘中自动跟随，不需要手动点' },
  { icon: 'fa-download',  t: '一键导出',     d: '名单直接导出通达信自选股' },
  // 2026-10-05 晚（主人：「撤」）：不再在页面上展示上游来源名
  { icon: 'fa-shield',    t: '可追溯',       d: '每处数据都标注更新时刻，随时可核对' },
]

/** -1 表示不限次 */
function lim(v) {
  if (v === undefined || v === null) return '—'
  return Number(v) < 0 ? '不限次' : `${v} 次/日`
}

onMounted(async () => {
  // 两个都是匿名接口；任何一个失败都不影响落地页渲染（有默认值兜底）
  loadPublicConfig()
  try {
    const p = await memberPlans()
    if (p) plans.value = p
  } catch (e) { /* 静默：权益表退化为"登录后查看" */ }
  // 注：此处**刻意不做**曝光埋点 —— 全站埋点接口 /api/activity/track 需鉴权，
  //   匿名调用必然 401（白费一次请求且污染日志）。要做落地页转化统计需后端先开匿名口径。
})

function copyWx() {
  copyText(SUPPORT_WECHAT, '客服微信已复制')
}
</script>

<template>
  <div class="page-shell ld-page">
    <h1 class="visually-hidden">快选 · 竞价选股</h1>

    <!-- ================= 首屏：价值主张 + 双 CTA ================= -->
    <section class="ld-hero">
      <div class="ld-hero-main">
        <span class="ld-badge"><i class="fa fa-clock-o" aria-hidden="true"></i> 交易日 09:25 定格</span>
        <p class="ld-h1">早 9:25，把当天竞价名单定下来</p>
        <p class="ld-sub">
          竞价选股 · 竞价异动 · 连板天梯 · 龙虎榜 · 双脑竞价 · 盘前资讯 ——
          从盘前到盘后的复盘流程，一个页面走完。
        </p>
        <div class="ld-cta">
          <router-link v-if="regOpen" class="ld-btn ld-btn-primary" to="/login?mode=register">
            免费注册 · 领 {{ giftDays }} 天会员
          </router-link>
          <router-link class="ld-btn ld-btn-ghost" :to="regOpen ? '/login' : '/'">已有账号，登录</router-link>
        </div>
        <p class="ld-cta-note">
          手机号注册，不需要下载 App；已有 {{ inviteRewardDays }} 天邀请奖励 —— 邀请好友双方各得 {{ inviteRewardDays }} 天。
        </p>
      </div>

      <ul class="ld-facts">
        <li v-for="f in FACTS" :key="f.t">
          <i class="fa" :class="f.icon" aria-hidden="true"></i>
          <b>{{ f.t }}</b>
          <span>{{ f.d }}</span>
        </li>
      </ul>
    </section>

    <!-- ================= 能力墙 ================= -->
    <section class="ld-section">
      <h2 class="ld-h2">登录后你会用到什么</h2>
      <div class="ld-grid">
        <article v-for="f in FEATURES" :key="f.title" class="ld-card">
          <i class="fa" :class="f.icon" aria-hidden="true"></i>
          <h3>{{ f.title }}</h3>
          <p>{{ f.desc }}</p>
        </article>
      </div>
    </section>

    <!-- ================= 权益对照（读后端真实配额） ================= -->
    <section id="plans" class="ld-section">
      <h2 class="ld-h2">会员权益</h2>
      <p class="ld-h2-sub">新注册账号自动获得 {{ giftDays }} 天完整体验，到期后仍可用免费额度。</p>
      <div class="ld-plans" :class="{ 'is-loading': !plans }">
        <table class="ld-table">
          <thead>
            <tr>
              <th scope="col">能力</th>
              <th scope="col">免费试用</th>
              <th scope="col" class="is-hi">付费会员</th>
              <th scope="col">VIP 老师</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">选股快照</th>
              <td>{{ lim(plans && plans.free && plans.free.picker) }}</td>
              <td class="is-hi">{{ lim(plans && plans.member && plans.member.picker) }}</td>
              <td>{{ lim(plans && plans.vip && plans.vip.picker) }}</td>
            </tr>
            <tr>
              <th scope="row">AI 预测</th>
              <td>{{ lim(plans && plans.free && plans.free.aipick) }}</td>
              <td class="is-hi">{{ lim(plans && plans.member && plans.member.aipick) }}</td>
              <td>{{ lim(plans && plans.vip && plans.vip.aipick) }}</td>
            </tr>
            <tr>
              <th scope="row">竞价异动</th>
              <td>{{ lim(plans && plans.free && plans.free.auction) }}</td>
              <td class="is-hi">{{ lim(plans && plans.member && plans.member.auction) }}</td>
              <td>{{ lim(plans && plans.vip && plans.vip.auction) }}</td>
            </tr>
            <tr>
              <th scope="row">每日签到</th>
              <td colspan="3">每日签到再加 {{ (plans && plans.checkin_bonus) || 3 }} 次选股额度</td>
            </tr>
            <tr>
              <th scope="row">邀请好友</th>
              <td colspan="3">每成功邀请 1 人，双方各得 {{ (plans && plans.invite_reward_days) || inviteRewardDays }} 天会员</td>
            </tr>
            <tr>
              <th scope="row">新用户</th>
              <td colspan="3">{{ (plans && plans.new_user_days) || giftDays }} 天会员完整体验</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 开通入口：可点击复制（原先是 title 悬停里的一串纯文本，用户要手抄微信号） -->
      <div class="ld-open">
        <div class="ld-open-txt">
          <b>{{ OPEN_TIP }}</b>
          <span>在架套餐与价格请加微信咨询；付款后即时开通，额度与到期时间可在「我的会员」查看。</span>
        </div>
        <div class="ld-open-acts">
          <button type="button" class="ld-btn ld-btn-primary" @click="copyWx">
            <i class="fa fa-weixin" aria-hidden="true"></i> 复制客服微信 {{ SUPPORT_WECHAT }}
          </button>
          <router-link v-if="regOpen" class="ld-btn ld-btn-ghost" to="/login?mode=register">
            先免费体验 {{ giftDays }} 天
          </router-link>
        </div>
        <!-- 2026-10-05 主人提供客服二维码：未登录访客想咨询/开通时不必先注册 -->
        <ContactQr :width="180" />
      </div>
    </section>

    <!-- ================= 数据与免责（信任元素） ================= -->
    <section class="ld-section">
      <h2 class="ld-h2">关于数据</h2>
      <ul class="ld-trust">
        <li><b>竞价口径</b>按交易日 9:25 定格快照计算，历史交易日与当日同源；零值不回退上一交易日。</li>
        <li><b>提供内容</b>仅提供软件工具使用权与数据整理能力，<b>不构成任何投资建议</b>，股市有风险，投资需谨慎。</li>
        <li><b>你的数据</b>注册仅需手机号；我们不会向第三方提供你的账号与使用记录。详见<a href="/privacy">《隐私政策》</a>。</li>
      </ul>
    </section>

    <!-- ================= 结尾再给一次 CTA ================= -->
    <section class="ld-final">
      <p class="ld-final-h">下一个交易日，早点看到名单</p>
      <div class="ld-cta">
        <router-link v-if="regOpen" class="ld-btn ld-btn-primary" to="/login?mode=register">
          免费注册 · 领 {{ giftDays }} 天会员
        </router-link>
        <router-link class="ld-btn ld-btn-ghost" to="/login">已有账号，登录</router-link>
      </div>
    </section>
  </div>
</template>

<style scoped>
.ld-page { padding-bottom: var(--s6); }
.ld-hero { display: grid; grid-template-columns: 1.15fr 0.85fr; gap: var(--s6); align-items: start; }
.ld-hero-main { padding: var(--s5) 0 var(--s4); }
.ld-badge {
  display: inline-flex; align-items: center; gap: var(--s1);
  background: var(--accent-bg); color: var(--accent); border: 1px solid var(--accent-border);
  border-radius: var(--r-pill); padding: 3px var(--s3); font-size: var(--fs-xs); font-weight: 600;
}
.ld-h1 {
  margin: var(--s3) 0 var(--s2); font-size: var(--fs-hero); line-height: 1.18;
  font-weight: 800; letter-spacing: -0.5px; color: var(--text-main);
}
.ld-sub { margin: 0; color: var(--text-secondary); font-size: var(--fs-md); line-height: 1.7; max-width: 46em; }
.ld-cta { display: flex; flex-wrap: wrap; gap: var(--s3); margin-top: var(--s5); }
.ld-btn {
  display: inline-flex; align-items: center; justify-content: center; gap: var(--s1);
  border-radius: var(--r-md); padding: var(--s3) var(--s5); font-size: var(--fs-base);
  font-weight: 700; cursor: pointer; text-decoration: none; border: 1px solid transparent;
  transition: transform 0.15s, background 0.2s, border-color 0.2s;
}
.ld-btn:active { transform: translateY(1px); }
/* 主 CTA 用实心深红 + 纯白字：与 S3 修好的「应用筛选」同一套对比度口径（≥4.5:1） */
.ld-btn-primary { background: var(--accent-deep2); color: #fff; }
.ld-btn-primary:hover { background: var(--accent-deep); }
.ld-btn-ghost { background: transparent; color: var(--text-main); border-color: var(--border-soft); }
.ld-btn-ghost:hover { background: var(--bg-subtle); }
.ld-cta-note { margin: var(--s3) 0 0; color: var(--text-dim); font-size: var(--fs-xs); }
.ld-facts { list-style: none; margin: 0; padding: var(--s4); display: grid; gap: var(--s3);
  background: var(--bg-panel); border: 1px solid var(--border-soft); border-radius: var(--r-lg); }
.ld-facts li { display: grid; grid-template-columns: 18px 1fr; gap: var(--s1) var(--s2); align-items: baseline; }
.ld-facts i { color: var(--accent); font-size: var(--fs-base); }
.ld-facts b { color: var(--text-main); font-size: var(--fs-base); }
.ld-facts span { color: var(--text-muted); font-size: var(--fs-xs); grid-column: 2; line-height: 1.6; }

.ld-section { margin-top: var(--s7); }
.ld-h2 { margin: 0 0 var(--s1); font-size: var(--fs-2xl); font-weight: 700; color: var(--text-main); }
.ld-h2-sub { margin: 0 0 var(--s4); color: var(--text-muted); font-size: var(--fs-sm); }
.ld-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--s3); }
.ld-card {
  background: var(--bg-panel); border: 1px solid var(--border-soft); border-radius: var(--r-lg);
  padding: var(--s4); transition: transform 0.15s, border-color 0.2s;
}
.ld-card:hover { transform: translateY(-2px); border-color: var(--accent-border); }
.ld-card i { font-size: var(--fs-xl); color: var(--accent); }
.ld-card h3 { margin: var(--s2) 0 var(--s1); font-size: var(--fs-lg); color: var(--text-main); }
.ld-card p { margin: 0; color: var(--text-muted); font-size: var(--fs-xs); line-height: 1.75; }

.ld-plans { background: var(--bg-panel); border: 1px solid var(--border-soft); border-radius: var(--r-lg); overflow: hidden; }
.ld-plans.is-loading { opacity: 0.72; }
.ld-table { width: 100%; border-collapse: collapse; font-size: var(--fs-base); }
.ld-table th, .ld-table td { padding: var(--s3) var(--s4); text-align: left; border-bottom: 1px solid var(--border-soft); }
.ld-table thead th { background: var(--bg-subtle); color: var(--text-secondary); font-size: var(--fs-sm); font-weight: 600; }
.ld-table tbody th { color: var(--text-secondary); font-weight: 500; }
.ld-table td { color: var(--text-main); font-weight: 600; }
.ld-table .is-hi { background: var(--accent-bg); color: var(--accent); }
.ld-table tr:last-child th, .ld-table tr:last-child td { border-bottom: none; }

.ld-open {
  display: flex; flex-wrap: wrap; gap: var(--s4); align-items: center; justify-content: space-between;
  margin-top: var(--s4); padding: var(--s4);
  background: var(--bg-panel); border: 1px dashed var(--accent-border); border-radius: var(--r-lg);
}
.ld-open-txt { display: grid; gap: var(--s1); max-width: 52em; }
.ld-open-txt b { color: var(--text-main); font-size: var(--fs-md); }
.ld-open-txt span { color: var(--text-muted); font-size: var(--fs-xs); line-height: 1.7; }
.ld-open-acts { display: flex; flex-wrap: wrap; gap: var(--s2); }

.ld-trust { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--s3); }
.ld-trust li {
  padding-left: var(--s4); position: relative; color: var(--text-muted);
  font-size: var(--fs-sm); line-height: 1.8;
}
.ld-trust li::before {
  content: ''; position: absolute; left: 0; top: 0.62em; width: 6px; height: 6px;
  border-radius: 50%; background: var(--accent-solid);
}
.ld-trust b { color: var(--text-secondary); margin-right: var(--s2); }
.ld-trust a { color: var(--accent); }

.ld-final {
  margin-top: var(--s7); padding: var(--s6) var(--s5); text-align: center;
  background: var(--bg-panel); border: 1px solid var(--border-soft); border-radius: var(--r-lg);
}
.ld-final-h { margin: 0; font-size: var(--fs-xl); font-weight: 700; color: var(--text-main); }
.ld-final .ld-cta { justify-content: center; }

/* ≤1100：首屏右栏下移（沿用仓库既有断点，不新增） */
@media (max-width: 1100px) {
  .ld-hero { grid-template-columns: 1fr; }
  .ld-grid { grid-template-columns: repeat(2, 1fr); }
  .ld-h1 { font-size: var(--fs-3xl); }
}
/* ≤768：手机端单列 + 收紧内边距 */
@media (max-width: 768px) {
  .ld-hero-main { padding-top: var(--s3); }
  .ld-grid { grid-template-columns: 1fr; }
  .ld-cta { gap: var(--s2); }
  .ld-btn { flex: 1 1 100%; padding: var(--s3) var(--s4); }
  .ld-open { flex-direction: column; align-items: stretch; }
  .ld-open-acts { flex-direction: column; }
  .ld-table th, .ld-table td { padding: var(--s2) var(--s2); font-size: var(--fs-xs); }
  .ld-final { padding: var(--s5) var(--s3); }
}
</style>
