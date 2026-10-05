<script setup>
// 2026-10-05 (S6): 合规静态页 —— 用户协议 / 隐私政策 / 退款说明。
//
// 为什么合并成一个组件：三份文档结构完全一致（标题 + 章节 + 结尾），
//   拆三个文件会产生三份重复的样式与外壳；用 route.meta.doc 选内容即可。
//
// ⚠️ 内容来源与边界（重要）：
//   · 隐私政策：**按真实数据库字段逐条写**（users 表 username/phone/register_ip/register_ua/
//     filter_prefs/invite_code、tokens、activity_logs、push_subscriptions），不是抄模板，
//     所以可被代码验证；将来改动数据采集行为时必须同步改这里。
//   · 用户协议：按产品**实际**能力与限制写（虚拟服务、不构成投资建议、账号不得共享）。
//   · 退款说明：🔴 **业务口径需主人拍板**（当前取"24 小时未使用可全退 + 超期按剩余天数折算"
//     这一行业常见口径），主人确认后只改 REFUND 里的文案即可。
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { copyText } from '../utils/tdx'
import { SUPPORT_WECHAT } from '../utils/contact'
import ContactQr from '../components/ContactQr.vue'

const route = useRoute()
const UPDATED = '2026-10-05'

const TERMS = {
  title: '用户协议',
  intro: '欢迎使用「快选」（以下简称"本平台"）。本协议是你与本平台之间就使用本平台软件工具与数据整理服务达成的约定。注册、登录或继续使用本平台，即表示你已阅读并同意本协议。',
  sections: [
    { h: '一、服务内容', p: ['本平台提供股票行情数据的整理、统计与选股辅助工具，包括竞价选股、竞价异动、连板天梯、龙虎榜、股性、异动监管、盘前资讯等页面。', '本平台只提供**软件工具使用权**，不提供证券投资顾问服务，不代客理财，不承诺任何收益。'] },
    { h: '二、账号与使用规则', p: ['注册需使用手机号完成短信验证；请妥善保管账号与密码，因账号外借、共享、泄露造成的损失由使用者自行承担。', '同一账号请勿多人共用或转售 —— 系统会对异常登录做限制（例如新设备登录会使旧登录态失效）。', '禁止使用爬虫、批量抓取、反向工程等方式获取或转发本平台数据；禁止将本平台数据用于再分发或商业售卖。'] },
    { h: '三、会员与额度', p: ['免费试用与付费会员的可用次数、有效期以「我的会员」页面实时展示为准。', '会员属于**虚拟内容服务**，开通后即时生效，**不支持退款**（付款后未成功开通或重复支付的情形除外，详见《退款说明》）。', '会员到期后，超出免费额度的功能将不可用；已产生的历史记录仍可在额度范围内查看。'] },
    // 2026-10-05 晚（主人：「撤」）：去掉"数据来源于第三方公开行情接口与公开榜单"这句来源披露；
    //   ★ 但**保留**「可能存在延迟、缺失或错漏；请以交易所与券商行情为准」—— 那是免责声明里
    //     真正起保护作用的部分，与"展示不展示来源名"是两件事，不跟着一起删。
    { h: '四、免责声明', p: ['本平台展示的所有数据、榜单、评分与模型输出均为**信息整理结果**，不构成任何投资建议，亦不构成对任何证券的推荐。', '数据可能存在延迟、缺失或错漏；请以交易所与券商行情为准。', '股市有风险，投资需谨慎。你依据本平台信息作出的任何决策及后果，由你自行承担。'] },
    { h: '五、协议变更与终止', p: ['本平台可能根据业务与法规要求调整本协议，调整后会在本页面更新并标注更新日期；继续使用即视为接受。', '如你违反本协议，本平台可暂停或终止向你提供服务。'] },
  ],
}

const PRIVACY = {
  title: '隐私政策',
  intro: '本政策说明「快选」收集哪些信息、用于什么目的、如何保存与保护。我们只收集提供服务所必需的最少信息。',
  sections: [
    { h: '一、我们收集哪些信息', p: ['**账号信息**：手机号（用于注册、登录、短信验证与密码找回）、用户名、密码哈希值（我们**不保存明文密码**）。', '**会员信息**：会员等级、到期时间、邀请码与邀请关系、签到与额度使用记录。', '**安全信息**：注册与登录时的 IP 地址、浏览器 User-Agent，用于识别异常登录与风控。', '**使用偏好**：你设置的背景、字号、字体、筛选条件（保存在账号偏好中，换设备登录后生效）。', '**消息与推送**：系统消息的已读状态；若你主动开启手机推送，会保存浏览器推送所需的订阅端点（可随时在浏览器设置中撤销）。'] },
    { h: '二、信息如何使用', p: ['用于账号登录与安全验证、会员权益与额度结算、系统通知（版本更新、会员到期）以及产品功能的正常运转。', '我们**不会**将你的手机号或账号信息出售、提供给第三方用于营销。'] },
    { h: '三、信息的存储与保护', p: ['数据存储于本平台服务器；密码以不可逆哈希方式保存，接口访问需通过令牌鉴权。', '为降低泄露风险，页面带有你账号标识的水印；请勿将截图外发。'] },
    { h: '四、你的权利', p: ['你可以随时在「我的会员 → 个人信息」查看与修改资料、修改密码或退出登录。', '如需注销账号或删除数据，请通过客服微信 ' + SUPPORT_WECHAT + ' 联系我们，核实身份后处理。'] },
    { h: '五、Cookie 与本地存储', p: ['本平台使用浏览器 localStorage / sessionStorage 保存登录态与显示偏好，用于保持登录与记住你的设置；不使用第三方广告跟踪脚本。'] },
  ],
}

// 2026-10-05 (S4) 主人口径：**不退**。原稿是行业常见的"24 小时内可全退 + 超期折算"，
//   已按主人指令改为"虚拟服务开通后不支持退款"，并保留两条**必要兜底**（未开通/重复支付、
//   平台原因持续不可用）——这两条不是"可退选项"，而是防止"付了钱没拿到货"的纠纷底线。
const REFUND = {
  title: '退款说明',
  intro: '会员属于**虚拟内容服务**，付款后即时开通、权益立即生效。因此**开通后不支持退款**。请在付款前确认套餐与有效期（新用户建议先用免费试用体验后再决定是否付费）。',
  sections: [
    { h: '一、不支持退款的情形', p: ['会员一经开通（含续费后自动顺延），虚拟服务已即时交付，**不支持退款**。', '已使用过任一付费额度（选股快照 / AI 预测 / 竞价异动）后，不支持退款。'] },
    { h: '二、可以退回的两种情形', p: ['付款后**未成功开通**，或**重复支付**：核实后全额原路退回。', '因本平台原因导致付费功能在有效期内**持续不可用**且无法修复：按未使用天数协商折算退回。'] },
    { h: '三、申请方式', p: ['请通过客服微信 ' + SUPPORT_WECHAT + ' 联系我们（请附付款时间与账号手机号），我们会在 3 个工作日内答复。'] },
  ],
}

const DOCS = { terms: TERMS, privacy: PRIVACY, refund: REFUND }
const doc = computed(() => DOCS[route.meta.doc] || TERMS)

// 极简富文本：把 **xxx** 渲染成 <b>xxx</b>（避免为几处强调引入 markdown 依赖）
function rich(text) {
  return String(text).split(/\*\*(.+?)\*\*/g).map((seg, i) => ({ t: seg, b: i % 2 === 1 }))
}
function copyWx() {
  copyText(SUPPORT_WECHAT, '客服微信已复制')
}
</script>

<template>
  <div class="page-shell legal-page">
    <header class="lg-head">
      <h1>{{ doc.title }}</h1>
      <p class="lg-meta">最后更新：{{ UPDATED }}</p>
    </header>
    <!-- 🔴 2026-10-05 修复：intro 原先直接 {{ }} 插值 ⇒ 里面的 **强调** 会**原样显示成星号**
         （生产实测 /refund 首屏就能看到 `**虚拟内容服务**`）。与下方段落统一走 rich() 富文本。 -->
    <p class="lg-intro">
      <template v-for="(seg, j) in rich(doc.intro)" :key="j">
        <b v-if="seg.b">{{ seg.t }}</b>
        <template v-else>{{ seg.t }}</template>
      </template>
    </p>

    <section v-for="s in doc.sections" :key="s.h" class="lg-sec">
      <h2>{{ s.h }}</h2>
      <p v-for="(para, i) in s.p" :key="i">
        <template v-for="(seg, j) in rich(para)" :key="j">
          <b v-if="seg.b">{{ seg.t }}</b>
          <template v-else>{{ seg.t }}</template>
        </template>
      </p>
    </section>

    <div class="lg-contact">
      <span>对本页内容有疑问？</span>
      <button type="button" class="lg-btn" @click="copyWx">
        <i class="fa fa-weixin" aria-hidden="true"></i> 复制客服微信 {{ SUPPORT_WECHAT }}
      </button>
      <router-link class="lg-btn lg-btn-ghost" to="/">返回首页</router-link>
    </div>
    <!-- 2026-10-05 主人提供客服二维码：退款/注销等诉求都以"加客服"为出口 -->
    <div class="lg-qr"><ContactQr :width="176" /></div>

    <p class="lg-foot">本平台仅提供软件工具使用权，不构成任何投资建议，股市有风险，投资需谨慎。</p>
  </div>
</template>

<style scoped>
.legal-page { max-width: 860px; margin: 0 auto; padding: var(--s5) var(--s4) var(--s8); }
.lg-head { border-bottom: 1px solid var(--border-soft); padding-bottom: var(--s3); }
.lg-head h1 { margin: 0; font-size: var(--fs-2xl); font-weight: 700; color: var(--text-main); }
.lg-meta { margin: var(--s1) 0 0; color: var(--text-dim); font-size: var(--fs-xs); }
.lg-intro {
  margin: var(--s4) 0 0; padding: var(--s3) var(--s4);
  background: var(--bg-subtle); border-left: 3px solid var(--accent);
  border-radius: var(--r-sm); color: var(--text-secondary); font-size: var(--fs-base); line-height: 1.85;
}
.lg-sec { margin-top: var(--s5); }
.lg-sec h2 { margin: 0 0 var(--s2); font-size: var(--fs-lg); font-weight: 700; color: var(--text-main); }
.lg-sec p { margin: var(--s2) 0; color: var(--text-secondary); font-size: var(--fs-base); line-height: 1.9; }
.lg-sec b { color: var(--text-main); }
.lg-contact {
  display: flex; flex-wrap: wrap; gap: var(--s3); align-items: center;
  margin-top: var(--s7); padding: var(--s4);
  background: var(--bg-panel); border: 1px solid var(--border-soft); border-radius: var(--r-lg);
  color: var(--text-muted); font-size: var(--fs-sm);
}
.lg-btn {
  display: inline-flex; align-items: center; gap: var(--s1);
  background: var(--accent-deep2); color: #fff; border: 1px solid transparent;
  border-radius: var(--r-md); padding: var(--s2) var(--s4);
  font-size: var(--fs-sm); font-weight: 600; cursor: pointer; text-decoration: none;
}
.lg-btn:hover { background: var(--accent-deep); }
.lg-btn-ghost { background: transparent; color: var(--text-main); border-color: var(--border-soft); }
.lg-btn-ghost:hover { background: var(--bg-subtle); }
.lg-foot { margin: var(--s6) 0 0; color: var(--text-dim); font-size: var(--fs-xs); line-height: 1.8; }

@media (max-width: 768px) {
  .legal-page { padding: var(--s4) var(--s3) var(--s7); }
  .lg-contact { flex-direction: column; align-items: stretch; }
}
</style>
