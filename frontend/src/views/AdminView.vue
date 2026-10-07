<template>
  <div class="admin-wrap">
    <h1 class="visually-hidden">管理后台</h1>
    <div class="admin-head">
      <h2 class="admin-title"><i class="fa fa-shield"></i> 管理后台</h2>
      <span style="width:120px;"></span>
    </div>

    <!-- 403 提示 -->
    <div v-if="denied" class="admin-card" style="text-align:center;padding:40px;color:#ff9a9a;">
      <i class="fa fa-lock" style="font-size:2.25rem;"></i>
      <p style="margin-top:12px;">无管理员权限，请联系管理员开通</p>
      <router-link to="/" class="tdx-export-btn nav-btn nav-history">返回主页</router-link>
    </div>

    <template v-else>
      <!-- 站内公告(2026-10-04 建立 / 2026-10-06 扩展: 定时/草稿/编辑/触达统计)
           主人需求：「系统消息比如系统更新提醒…」——这里是**站方广播**的发布端。
           🔴 会员到期提醒不在这里发：它由消息中心按 expire_at 自动生成（T-7/T-3/T-1/T+0），
              人工发会在用户续费后变成永久脏数据。要催续费去「到期预警」点「一键提醒」。 -->
      <div class="admin-card">
        <div class="card-title"><i class="fa fa-bullhorn"></i> 站内公告（系统消息）</div>
        <div class="notice-form">
          <input v-model="nf.title" class="admin-input" placeholder="标题，如：系统更新：新增超智研判" maxlength="60" />
          <textarea v-model="nf.body" class="admin-input" rows="3" placeholder="正文（换行会原样展示）"></textarea>
          <div class="notice-form-row">
            <label>分类
              <select v-model="nf.category" class="admin-input">
                <option value="system">系统/运营</option>
                <option value="trade">交易时点</option>
                <option value="account">账户会员</option>
              </select>
            </label>
            <label>级别
              <select v-model="nf.level" class="admin-input">
                <option value="info">普通</option>
                <option value="warn">提醒（亮红点）</option>
                <option value="urgent">紧急（亮红点 + 强调）</option>
              </select>
            </label>
            <label>定向
              <select v-model="nf.target" class="admin-input" @change="loadNoticeCount">
                <option value="all">全部用户</option>
                <option value="free">仅免费试用</option>
                <option value="member">仅付费/VIP</option>
                <option value="vip">仅 VIP</option>
                <!-- A4: 标签定向 —— 能对"免费但很活跃"这群最该转化的人单独说话 -->
                <option value="tag">按分层标签</option>
              </select>
            </label>
            <label v-if="nf.target === 'tag'">标签
              <select v-model="nf.tag" class="admin-input" @change="loadNoticeCount">
                <option value="">请选择标签</option>
                <option v-for="t in tagSummary" :key="t.tag" :value="t.tag">
                  {{ t.label }}（{{ t.count }} 人）
                </option>
              </select>
            </label>
            <label>有效期
              <select v-model.number="nf.days" class="admin-input">
                <option :value="0">长期</option>
                <option :value="3">3 天</option>
                <option :value="7">7 天</option>
                <option :value="30">30 天</option>
              </select>
            </label>
          </div>
          <div class="notice-form-row">
            <label>发送方式
              <select v-model="nf.status" class="admin-input">
                <option value="sent">立即发送</option>
                <option value="scheduled">定时发送</option>
                <option value="draft">存为草稿</option>
              </select>
            </label>
            <label v-if="nf.status === 'scheduled'">定时时刻（北京时间）
              <input v-model="nf.publish_at" class="admin-input" type="datetime-local" />
            </label>
            <label>行动按钮
              <select v-model="nf.action_type" class="admin-input">
                <option value="">无</option>
                <option value="route">跳转页面</option>
                <option value="copy">复制文本</option>
              </select>
            </label>
            <input
              v-if="nf.action_type"
              v-model="nf.action_value"
              class="admin-input"
              :placeholder="nf.action_type === 'route' ? '如 /member' : '如客服微信号'"
            />
          </div>
          <div class="notice-form-row">
            <button class="admin-btn" :disabled="nfPosting || !canPublish" @click="publishNotice">
              {{ nfPosting ? '提交中…' : (nf.status === 'draft' ? '存草稿' : (nf.status === 'scheduled' ? '排期' : '发布')) }}
            </button>
            <!-- A5: 发之前先用「仅自己可见」看一眼排版, 确认无误再真发 -->
            <button class="admin-btn admin-btn-sm" :disabled="nfTesting || !nf.title.trim()" @click="testSendNotice">
              {{ nfTesting ? '发送中…' : '测试发送（仅我可见）' }}
            </button>
            <!-- 发布前人数预估：防误发全量（金融工具站误发紧急公告是信任事故） -->
            <span class="admin-tip">目标人群约 {{ noticeCount.total }} 人，其中已开推送 {{ noticeCount.pushable }} 人</span>
          </div>
          <span class="admin-tip">
            发布后用户铃铛亮红点（info 级不亮）；推送会按用户偏好与每日上限（3 条/运营 1 条）过滤，不保证人人收到。
          </span>
        </div>

        <div class="table-scroll" style="margin-top:10px;">
          <table class="admin-table">
            <thead>
              <tr>
                <th style="width:28%">标题</th><th>分类</th><th>级别</th><th>定向</th>
                <th>状态</th><th>人群</th><th>推送</th><th>已读</th><th>点击</th><th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!notices.length"><td colspan="10" class="weight-desc">还没有公告</td></tr>
              <tr v-for="n in notices" :key="n.nkey || n.id">
                <td :title="n.body">
                  {{ n.title }}
                  <div v-if="n.publish_date" class="weight-desc">计划 {{ n.publish_date }} 发送</div>
                </td>
                <td>{{ noticeCatLabel(n.category) }}</td>
                <td>{{ noticeLevelLabel(n.level) }}</td>
                <td>{{ noticeTargetLabel(n.target) }}</td>
                <td>
                  <span v-if="n.off_at">已撤回</span>
                  <span v-else-if="n.status === 'draft'">草稿</span>
                  <span v-else-if="n.status === 'scheduled'">待发送</span>
                  <span v-else>展示中</span>
                </td>
                <td>{{ n.target_count || 0 }}</td>
                <td>{{ n.push_sent || 0 }}<span v-if="n.push_failed" class="weight-desc"> / 失败{{ n.push_failed }}</span></td>
                <td>{{ n.read_count || 0 }}<span v-if="n.target_count" class="weight-desc"> ({{ n.read_rate }}%)</span></td>
                <!-- 点击 = 消息内行动按钮被点了多少人(触达漏斗最后一环) -->
                <td>{{ n.click_count || 0 }}<span v-if="n.target_count" class="weight-desc"> ({{ clickRate(n) }}%)</span></td>
                <td>
                  <button v-if="!n.off_at && n.status !== 'sent'" class="admin-btn admin-btn-sm" @click="editNotice(n)">编辑</button>
                  <button v-if="n.status === 'sent' && n.nkey" class="admin-btn admin-btn-sm" @click="exportReach(n)">明细</button>
                  <button v-if="!n.off_at" class="admin-btn admin-btn-sm" @click="offNotice(n)">撤回</button>
                  <span v-else class="weight-desc">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- 第二批(2026-10-06): A4 用户分层标签 —— 定向运营的前提。
           没有标签, 公告只能对"全部/免费/付费/VIP"四档说话。 -->
      <div class="admin-card">
        <div class="card-title">
          <i class="fa fa-th-large"></i> 用户分层标签
          <button class="admin-btn admin-btn-sm" :disabled="tagRefreshing" @click="refreshTags">
            {{ tagRefreshing ? '重算中…' : '重算自动标签' }}
          </button>
        </div>
        <div v-if="tagSummary.length" class="tag-cloud">
          <button
            v-for="t in tagSummary" :key="t.tag" class="tag-chip"
            :class="{ on: tagFilter === t.tag }" :title="t.desc" @click="pickTag(t.tag)"
          >
            {{ t.label }}<span class="tag-n">{{ t.count }}</span>
          </button>
        </div>
        <div v-else class="weight-desc">还没有标签，点右上角「重算自动标签」生成</div>
        <span class="admin-tip">
          点标签 = 下方用户列表按该标签筛选（再点一次取消）；悬停看口径。自动标签每天凌晨重算，手动标签在用户详情里打。
        </span>
      </div>

      <!-- 第二批(2026-10-06): A10 转化漏斗 -->
      <div class="admin-card">
        <div class="card-title">
          <i class="fa fa-filter"></i> 转化漏斗（近 {{ funnelDays }} 天）
          <button class="admin-btn admin-btn-sm" @click="loadFunnel">刷新</button>
        </div>
        <div v-if="funnel && funnel.stages && funnel.stages.length">
          <div v-for="s in funnel.stages" :key="s.key" class="funnel-row">
            <span class="funnel-label">{{ s.label }}</span>
            <span class="funnel-bar-wrap">
              <span class="funnel-bar" :style="{ width: funnelWidth(s.count) + '%' }"></span>
            </span>
            <span class="funnel-num">{{ s.count }} 人 · {{ s.rate }}%</span>
          </div>
          <span class="admin-tip">
            🔴 「咨询客服」这一环<strong>没有埋点</strong>（客服走微信人工），所以显示「无数据」而不是编一个数字填进去 ——
            这个看板是给运营决定投入方向用的。
          </span>
        </div>
        <div v-else class="weight-desc">暂无数据</div>
      </div>

      <!-- 用户统计卡片 -->
      <StatCards :stats="stats" />

      <!-- 会员运营中心(2026-09-21 会员体系): 运营看板/到期预警/风控/邀请榜/审计/权益配置/导入导出 -->
      <MemberAdminPanel @open-detail="openUserDetail" />

      <!-- 用户列表 -->
      <div class="admin-card">
        <div class="card-title">
<i class="fa fa-users"></i> 用户列表
          <div class="user-toolbar">
            <!-- 会员筛选 tab -->
            <div class="member-tabs">
              <button
v-for="t in memberTabs" :key="t.key" class="member-tab"
                      :class="{ active: memberTab === t.key }" @click="setMemberTab(t.key)"
>
{{ t.label }}
</button>
            </div>
            <input v-model="keyword" class="admin-input" placeholder="搜用户名/手机/邮箱/微信名/备注" @keyup.enter="loadUsers(1)" />
            <button class="admin-search-btn" @click="loadUsers(1)"><i class="fa fa-search"></i> 搜索</button>
            <button class="admin-search-btn btn-warn" :disabled="!selectedIds.length" @click="openBatchExpire">
              <i class="fa fa-clock-o"></i> 批量设到期{{ selectedIds.length ? ' (' + selectedIds.length + ')' : '' }}
            </button>
            <button class="admin-search-btn btn-create" @click="openCreate()"><i class="fa fa-plus"></i> 新建会员</button>
            <!-- A7 导出扩充: 客服拿这份 CSV 当工作清单用(字段越全沟通越准) -->
            <button class="admin-search-btn" title="导出当前筛选条件下的用户（含活跃/签到/标签等字段）" @click="exportUsers('users')">
              <i class="fa fa-download"></i> 导出用户
            </button>
            <button class="admin-search-btn" title="导出已到期且到期后无使用的用户" @click="exportUsers('churn')">
              <i class="fa fa-download"></i> 导出流失
            </button>
          </div>
        </div>
        <div v-if="tagFilter" class="admin-tip" style="padding:0 0 6px;">
          已按标签「{{ (tagSummary.find(t => t.tag === tagFilter) || {}).label || tagFilter }}」筛选
          <button class="admin-btn admin-btn-sm" @click="pickTag(tagFilter)">取消筛选</button>
        </div>
        <div class="table-scroll">
          <table class="admin-table">
            <thead>
              <tr>
                <th style="width:30px;"><input type="checkbox" :checked="pageAllChecked" title="全选本页" @change="togglePageAll" /></th>
                <th class="sortable" :class="{ active: userSort.keyOf('id') }" @click="userSort.onSort('id')">ID<span class="sort-ind">{{ userSort.ind('id') }}</span></th>
                <th class="sortable" :class="{ active: userSort.keyOf('username') }" @click="userSort.onSort('username', 'string')">用户名<span class="sort-ind">{{ userSort.ind('username') }}</span></th>
                <th class="sortable" :class="{ active: userSort.keyOf('wx_name') }" @click="userSort.onSort('wx_name', 'string')">微信名<span class="sort-ind">{{ userSort.ind('wx_name') }}</span></th>
                <th class="sortable" :class="{ active: userSort.keyOf('phone') }" @click="userSort.onSort('phone', 'string')">手机/邮箱<span class="sort-ind">{{ userSort.ind('phone') }}</span></th>
                <th class="sortable" :class="{ active: userSort.keyOf('remark') }" @click="userSort.onSort('remark', 'string')">备注<span class="sort-ind">{{ userSort.ind('remark') }}</span></th>
                <th class="sortable" :class="{ active: userSort.keyOf('created_at') }" @click="userSort.onSort('created_at')">注册时间<span class="sort-ind">{{ userSort.ind('created_at') }}</span></th>
                <th class="sortable" :class="{ active: userSort.keyOf('expire_at') }" @click="userSort.onSort('expire_at')">到期时间<span class="sort-ind">{{ userSort.ind('expire_at') }}</span></th>
                <th class="sortable" :class="{ active: userSort.keyOf('invited_by_username') }" @click="userSort.onSort('invited_by_username', 'string')">邀请人<span class="sort-ind">{{ userSort.ind('invited_by_username') }}</span></th>
                <th class="sortable" :class="{ active: userSort.keyOf('invited_count') }" @click="userSort.onSort('invited_count')">邀请<span class="sort-ind">{{ userSort.ind('invited_count') }}</span></th>
                <th class="sortable" :class="{ active: userSort.keyOf('batch_count') }" @click="userSort.onSort('batch_count')">选股<span class="sort-ind">{{ userSort.ind('batch_count') }}</span></th>
                <th>会员等级</th>
                <th>标签</th>
                <th style="min-width:70px;">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="u in userSort.sorted(rows, userVal)" :key="u.id">
                <td><input type="checkbox" :checked="selectedIds.includes(u.id)" :disabled="u.is_admin" :title="u.is_admin ? '管理员不可批量操作' : ''" @change="e => toggleSelect(u.id, e.target.checked)" /></td>
                <td>{{ u.id }}</td>
                <td>{{ u.username }}</td>
                <td>{{ u.wx_name || '-' }}</td>
                <td>{{ u.phone || u.email || '-' }}</td>
                <td>
                  <span v-if="u.remark" class="cell-note" :title="u.remark">{{ u.remark }}</span>
                  <span v-else>-</span>
                  <span v-if="u.pay_remark" class="cell-pay" :title="u.pay_remark">💰 {{ u.pay_remark }}</span>
                </td>
                <td>{{ fmtTsTime(u.created_at) }}</td>
                <td>
                  <!-- VIP(member_level=2) 默认按 expire_at 显示; 仅管理员/expire_at=0 显示永久 -->
                  <span v-if="u.is_admin || expireState(u) === 'forever'" class="user-tag">永久</span>
                  <span v-else-if="expireState(u) === 'expired'" class="expired-tag">已过期 {{ fmtBjDay(u.expire_at) }}</span>
                  <span v-else class="ok-tag">{{ fmtBjDay(u.expire_at) }}</span>
                </td>
                <td>
                  <span v-if="u.invited_by_username" class="inviter-tag" :title="'被 ' + u.invited_by_username + ' 邀请'">{{ u.invited_by_username }}</span>
                  <span v-else class="dim">-</span>
                </td>
                <td>{{ u.invited_count }}</td>
                <td>{{ u.batch_count }}</td>
                <td>
                  <!-- 管理员固定, 普通用户/付费/VIP 可下拉修改 -->
                  <span v-if="u.is_admin" class="role-badge role-admin">管理员</span>
                  <select v-else v-model.number="u._level" class="level-select" title="修改会员等级" @change="setLevel(u)">
                    <option :value="0">免费试用</option>
                    <option :value="1">付费会员</option>
                    <option :value="2">VIP</option>
                  </select>
                </td>
                <!-- A4 分层标签: 自动标签系统算, 手动标签运营打(点 ✎ 编辑, 两者互不影响) -->
                <td class="tag-cell">
                  <span
                    v-for="t in (u.tags || [])" :key="t.tag" class="tag-chip-mini"
                    :class="{ manual: t.source === 'manual' }"
                    :title="t.source === 'manual' ? '手动标签' : '自动标签: ' + (tagSummary.find(x => x.tag === t.tag) || {}).desc"
                  >{{ t.label }}</span>
                  <span v-if="!(u.tags || []).length" class="dim">-</span>
                  <button class="mini-btn tag-edit" title="编辑手动标签" @click="editTags(u)">✎</button>
                </td>
                <td>
                  <div class="row-actions">
                    <!-- ⋮ 操作下拉(teleport 到 body 避免短表格时溢出覆盖搜索栏) -->
                    <button class="mini-btn" @click.stop="toggleMenu(u, $event)">⋮</button>
                  </div>
                </td>
              </tr>
              <tr v-if="!rows.length"><td colspan="13" style="text-align:center;color:#888;padding:20px;">暂无用户</td></tr>
            </tbody>
          </table>
        </div>
        <div class="pager">
          <div class="pager-left">
            <span style="color:#888;font-size:0.75rem;">每页</span>
            <select v-model.number="pageSize" class="admin-input" style="width:70px;padding:5px 8px;" @change="changePageSize">
              <option :value="10">10</option>
              <option :value="20">20</option>
              <option :value="50">50</option>
              <option :value="100">100</option>
            </select>
            <span style="color:#888;font-size:0.75rem;">条</span>
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
          <div class="pwd-title">🔑 重置密码：{{ pwdTarget.username }}<span style="color:#999;font-size:0.75rem;margin-left:8px;">({{ pwdTarget.phone || pwdTarget.email || '-' }})</span></div>
          <input v-model="pwdNew" type="text" class="admin-input" style="width:100%;box-sizing:border-box;" placeholder="输入新密码(至少6位)" @keyup.enter="doResetPwd" />
          <div style="display:flex;gap:10px;margin-top:14px;justify-content:flex-end;">
            <button class="mini-btn" @click="closePwdReset">取消</button>
            <button class="mini-btn danger" :disabled="pwdSaving" @click="doResetPwd">{{ pwdSaving ? '重置中...' : '确认重置' }}</button>
          </div>
        </div>
      </div>

      <!-- 创建会员弹层(管理员代创建账号) -->
      <div v-if="createOpen" class="pwd-mask" @click.self="closeCreate">
        <div class="pwd-pop" style="width:480px;">
          <div class="pwd-title">➕ 新建会员账号</div>
          <div class="profile-grid">
            <label class="profile-label">用户名 <span style="color:#ff6a6a;">*</span>
              <input v-model="createForm.username" type="text" maxlength="20" class="admin-input" placeholder="2-20 位, 中英文/数字/下划线" /></label>
            <label class="profile-label">初始密码 <span style="color:#ff6a6a;">*</span>
              <input v-model="createForm.password" type="text" maxlength="32" class="admin-input" placeholder="至少 6 位 (建议告知用户)" /></label>
            <label class="profile-label">手机号 <span style="color:#ff6a6a;">*</span>
              <input v-model="createForm.phone" type="text" maxlength="11" class="admin-input" placeholder="11 位手机号" /></label>
            <label class="profile-label">邮箱 <span style="color:#ff6a6a;">*</span>
              <input v-model="createForm.email" type="text" maxlength="60" class="admin-input" placeholder="用于找回密码" /></label>
            <label class="profile-label">会员等级
              <select v-model.number="createForm.member_level" class="admin-input">
                <option :value="0">免费试用</option>
                <option :value="1">付费会员</option>
                <option :value="2">VIP</option>
              </select></label>
            <label class="profile-label">到期日
              <input v-model="createForm.expire_at" type="date" class="admin-input" :min="todayStr" /></label>
            <label class="profile-label">微信名
              <input v-model="createForm.wx_name" type="text" maxlength="40" class="admin-input" placeholder="联系用微信昵称" /></label>
            <label class="profile-label">管理员备注
              <input v-model="createForm.remark" type="text" maxlength="200" class="admin-input" placeholder="仅管理端可见" /></label>
            <label class="profile-label profile-pay" style="grid-column:1 / -1;">付款备注 (月费用户必填)
              <textarea
v-model="createForm.pay_remark" maxlength="500" rows="2" class="admin-input"
                        placeholder="例: 8-16 微信月付 300元; 下次续费 9-16"
></textarea></label>
          </div>
          <div class="profile-tip">带 * 的字段必填; 创建成功后会显示初始密码, 请告知用户</div>
          <div style="display:flex;gap:10px;margin-top:14px;justify-content:flex-end;">
            <button class="mini-btn" @click="closeCreate">取消</button>
            <button class="mini-btn danger" :disabled="createSaving" @click="doCreate">{{ createSaving ? '创建中...' : '创建账号' }}</button>
          </div>
          <!-- 创建成功结果展示 -->
          <div v-if="createResult" class="create-result">
            <div><b>{{ createResult.username }}</b> 创建成功!</div>
            <div style="margin-top:6px;font-size:0.75rem;color:#999;">
              ID {{ createResult.uid }} · 到期 {{ fmtBjDay(createResult.expire_at) }} · 等级 {{ levelLabel(createResult.member_level) }}
            </div>
            <div style="margin-top:8px;padding:8px;background:rgba(255,200,80,0.15);border:1px solid #ffc850;border-radius:6px;color:#ffe0a0;">
              初始密码: <b style="user-select:all;font-family: inherit;">{{ createResult.password }}</b>
              <button class="mini-btn" style="margin-left:8px;" @click="copyText(createResult.password)">复制</button>
            </div>
          </div>
        </div>
      </div>

      <!-- 邀请关系弹层(被谁邀请 + 邀请了谁, 含注册 IP 便于识别同 IP 刷号) -->
      <div v-if="invitesTarget" class="pwd-mask" @click.self="closeInvites">
        <div class="pwd-pop" style="width:520px;">
          <div class="pwd-title">
👥 邀请关系：{{ invitesTarget.username }}
            <span style="color:#999;font-size:0.75rem;margin-left:8px;">(ID {{ invitesTarget.id }})</span>
</div>
          <div class="invite-chain">
            <div class="chain-row">
              <span class="chain-label">被谁邀请</span>
              <span v-if="invitesData.invited_by_username" class="chain-val">{{ invitesData.invited_by_username }}</span>
              <span v-else class="dim">无(自主注册)</span>
            </div>
            <div class="chain-row">
              <span class="chain-label">我的邀请码</span>
              <span class="chain-val mono">{{ invitesData.invite_code || '-' }}</span>
            </div>
            <div class="chain-row">
              <span class="chain-label">已邀请</span>
              <span class="chain-val">{{ invitesData.invited_count }} 人</span>
            </div>
          </div>
          <div v-if="invitesLoading" class="dim" style="text-align:center;padding:12px;">加载中...</div>
          <div v-else-if="!invitesData.invitees || !invitesData.invitees.length" class="dim" style="text-align:center;padding:12px;">该用户还没有邀请任何人</div>
          <div v-else class="invite-list-scroll">
            <table class="invite-table">
              <thead><tr><th>用户名</th><th>注册时间</th><th>注册 IP</th></tr></thead>
              <tbody>
                <tr v-for="iv in invitesData.invitees" :key="iv.username">
                  <td>{{ iv.username }}</td>
                  <td>{{ fmtTsTime(iv.created_at) }}</td>
                  <td class="mono">{{ iv.register_ip || '-' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div style="display:flex;gap:10px;margin-top:14px;justify-content:flex-end;">
            <button class="mini-btn" @click="closeInvites">关闭</button>
          </div>
        </div>
      </div>

      <!-- 编辑资料弹层(管理员代设置手机/邮箱/微信名/备注/付款备注) -->
      <div v-if="profileTarget" class="pwd-mask" @click.self="closeProfile">
        <div class="pwd-pop" style="width:560px;">
          <div class="pwd-title">
📝 编辑资料：{{ profileTarget.username }}
            <span style="color:#999;font-size:0.75rem;margin-left:8px;">(ID {{ profileTarget.id }})</span>
</div>
          <div class="profile-grid">
            <label class="profile-label">手机号
              <input v-model="profileForm.phone" type="text" maxlength="11" class="admin-input admin-input-lg" placeholder="11 位手机号" /></label>
            <label class="profile-label">邮箱
              <input v-model="profileForm.email" type="text" maxlength="60" class="admin-input admin-input-lg" placeholder="用于找回密码" /></label>
            <label class="profile-label">微信名
              <input v-model="profileForm.wx_name" type="text" maxlength="40" class="admin-input admin-input-lg" placeholder="联系用微信昵称" /></label>
            <label class="profile-label">管理员备注
              <input v-model="profileForm.remark" type="text" maxlength="200" class="admin-input admin-input-lg" placeholder="仅管理端可见" /></label>
            <label class="profile-label profile-pay" style="grid-column:1 / -1;">付款备注 (会员专属, 仅管理员可改)
              <textarea
v-model="profileForm.pay_remark" maxlength="500" rows="3" class="admin-input admin-input-lg"
                        placeholder="例: 8-16微信月付300元; 到期 9-16 自动提醒续费"
></textarea></label>
          </div>
          <div class="profile-tip">留空表示不修改该字段；手机号/邮箱有格式+唯一性校验</div>
          <div style="display:flex;gap:10px;margin-top:14px;justify-content:flex-end;">
            <button class="mini-btn" @click="closeProfile">取消</button>
            <button class="mini-btn danger" :disabled="profileSaving" @click="saveProfile">{{ profileSaving ? '保存中...' : '保存资料' }}</button>
          </div>
        </div>
      </div>

      <!-- 批量设置到期弹层(2026-08-17 主人需求: 多选用户统一设置会员时间) -->
      <div v-if="batchExpireOpen" class="pwd-mask" @click.self="closeBatchExpire">
        <div class="pwd-pop" style="width:480px;">
          <div class="pwd-title">⏰ 批量设置到期：已选 {{ selectedIds.length }} 人</div>
          <div class="batch-expire-box">
            <div class="pop-label">延长时长(在现有到期上累加)</div>
            <div class="pop-row" style="flex-wrap:wrap;">
              <button class="mini-btn batch-exp-btn" @click="batchExtend('week')">+1周</button>
              <button class="mini-btn batch-exp-btn" @click="batchExtend('month')">+1月</button>
              <button class="mini-btn batch-exp-btn" @click="batchExtend('quarter')">+1季</button>
              <button class="mini-btn batch-exp-btn" @click="batchExtend('year')">+1年</button>
            </div>
            <div class="pop-label" style="margin-top:12px;">自定义到期日(统一设为该日)</div>
            <div class="pop-row">
              <input v-model="batchExpireDate" type="date" class="mini-date" style="flex:1;padding:8px 12px;font-size:0.875rem;" :max="'2099-12-31'" />
              <button class="mini-btn batch-exp-btn" @click="batchSetDate">设为该日</button>
            </div>
            <div class="pop-row" style="margin-top:12px;">
              <button class="mini-btn batch-exp-btn batch-forever" @click="batchForever">设为永久</button>
            </div>
            <div class="profile-tip">被选中的管理员账号已自动排除</div>
          </div>
          <div style="display:flex;gap:10px;margin-top:14px;justify-content:flex-end;">
            <button class="mini-btn" @click="closeBatchExpire">取消</button>
            <button class="mini-btn danger" :disabled="batchSaving" @click="batchExpire">{{ batchSaving ? '设置中...' : '确认批量设置' }}</button>
          </div>
        </div>
      </div>

      <!-- 评分权重配置: 竞价 / 盘中实时 两套因子表(2026-09-28 v4.11.76) -->
      <div class="admin-card">
        <div class="card-title">
          <i class="fa fa-sliders"></i> {{ scoringStrategy === 'spot' ? '盘中实时评分·权重配置' : '竞价评分·权重配置' }}
          <span class="admin-tip">保存后立即生效（影响后续选股评分）</span>
        </div>
        <!-- 策略切换: 两套配置**互相独立**存储(竞价 settings.scoring / 盘中 settings.scoring_spot),
             切页签只切视图, 不做任何写入; 未保存的改动会随切换丢失(有 dirty 提示)。
             🔴 切换前若未保存, 这里会提示 —— 因子表不同, 直接切会看到"另一套"的表,
                容易误以为改动丢了。 -->
        <div class="factor-tabs" style="margin-bottom:10px;">
          <button class="factor-tab" :class="{ active: scoringStrategy === 'auction' }"
                  @click="switchScoringStrategy('auction')">竞价（9:25 定格）</button>
          <button class="factor-tab" :class="{ active: scoringStrategy === 'spot' }"
                  @click="switchScoringStrategy('spot')">盘中实时（现涨/量比/换手）</button>
          <span v-if="scoringDirty" class="admin-tip" style="color:#e8a33d;margin-left:auto;">
            <i class="fa fa-exclamation-triangle"></i> 有未保存的改动
          </span>
        </div>
        <div class="table-scroll">
        <table class="admin-table weight-table">
          <thead><tr><th style="width:140px;">因子</th><th>权重(0~1)</th><th>说明</th></tr></thead>
          <tbody>
            <tr v-for="wk in wKeys" :key="wk[0]">
              <td>{{ wk[1] }}</td>
              <td><input v-model.number="scoring[wk[0]]" type="number" step="0.01" min="0" max="1" class="admin-input" style="width:90px;" @input="scoringDirty = true" /></td>
              <td class="weight-desc">{{ wk[2] }}</td>
            </tr>
            <tr>
              <td colspan="2" class="weight-total">权重合计：{{ weightSum.toFixed(2) }} <span v-if="Math.abs(weightSum - 1) > 0.03" class="weight-warn">（需约等于 1 才能保存）</span></td>
              <td></td>
            </tr>
            <tr v-for="ck in confKeys" :key="ck[0]">
              <td>置信度·{{ ck[1] }}</td>
              <td><input v-model.number="scoring[ck[0]]" type="number" step="1" min="0" max="30" class="admin-input" style="width:90px;" @input="scoringDirty = true" /></td>
              <td class="weight-desc">{{ ck[2] }}</td>
            </tr>
          </tbody>
        </table>
        </div>
        <div style="display:flex;gap:10px;margin-top:14px;align-items:center;">
          <button class="tdx-export-btn admin-save-btn" :disabled="saving" @click="saveScoring">
            <i class="fa fa-save"></i> {{ saving ? '保存中...' : '保存并生效' }}
          </button>
          <span v-if="saveMsg" :class="saveErr ? 'admin-msg-err' : 'admin-msg-ok'">{{ saveMsg }}</span>
        </div>
      </div>

      <!-- 打分明细配置(因子 Tab 切换) -->
      <div class="admin-card">
        <div class="card-title"><i class="fa fa-table"></i> {{ scoringStrategy === 'spot' ? '盘中实时评分' : '竞价评分' }}·打分明细 <span class="admin-tip">命中区间 [下限, 上限) 得对应分，未命中取默认分</span></div>
        <div class="factor-tabs">
          <button v-for="fk in factorOrder" :key="fk" class="factor-tab" :class="{ active: activeFactor === fk }" @click="activeFactor = fk">
            {{ factors[fk] ? factors[fk].label : fk }}
          </button>
        </div>
        <div v-if="factors[activeFactor] && factors[activeFactor].buckets" class="factor-box">
          <div class="factor-title">
{{ factors[activeFactor].label }} <span style="color:#888;font-size:0.75rem;">（{{ factors[activeFactor].unit }}）</span>
            <span style="margin-left:auto;display:flex;align-items:center;gap:6px;">
              默认分 <input v-model.number="factors[activeFactor].default" type="number" step="0.05" min="0" max="1" class="admin-input" style="width:70px;" />
            </span>
          </div>
          <div class="table-scroll">
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
        </div>
        <div v-else class="empty-state" style="padding:16px;">该因子暂未加载</div>
      </div>

      <!-- 竞价强度 · 子权重(权重组因子, 2026-09-30 新增编辑区) -->
      <div v-if="scoringStrategy === 'auction'" class="admin-card">
        <div class="card-title"><i class="fa fa-bolt"></i> 竞价强度 · 子权重
          <span class="admin-tip">上面 17% 的「竞价强度」由这几项合成；合计需约等于 1。要关闭整个强度项，请把上表的 w_warn 设为 0</span>
        </div>
        <div class="table-scroll">
        <table class="admin-table">
          <tbody>
            <tr v-for="sk in STRENGTH_KEYS" :key="sk[0]">
              <td>{{ sk[1] }}</td>
              <td><input v-model.number="strength[sk[0]]" type="number" step="0.05" min="0" max="1" class="admin-input" style="width:90px;" @input="scoringDirty = true" /></td>
              <td class="weight-desc">{{ sk[2] }}</td>
            </tr>
            <tr>
              <td colspan="2" class="weight-total">子权重合计：{{ strengthSum.toFixed(2) }}
                <span v-if="Math.abs(strengthSum - 1) > 0.05" class="weight-warn">（需约等于 1 才能保存）</span>
              </td>
              <td></td>
            </tr>
          </tbody>
        </table>
        </div>
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
            自由流通市值下限(亿) <input v-model.number="adminDefaults.floatMvFloor" type="number" min="0" class="admin-input" style="width:110px;" />
          </label>
          <label class="field-label" style="display:flex;flex-direction:column;gap:4px;">
            自由流通市值上限(亿) <input v-model.number="adminDefaults.floatMvGt" type="number" min="0" class="admin-input" style="width:110px;" />
          </label>
          <label class="field-label" style="display:flex;flex-direction:column;gap:4px;">
            股价上限(元) <input v-model.number="adminDefaults.priceGt" type="number" min="0" class="admin-input" style="width:110px;" />
          </label>
          <label class="field-label" style="display:flex;flex-direction:column;gap:4px;">
            评分下限(双低) <input v-model.number="adminDefaults.probLt" type="number" min="0" max="100" class="admin-input" style="width:110px;" />
          </label>
          <!-- 2026-09-20 主人拍板: 单阈值硬门槛, 低于该分直接不显示(最低 50) -->
          <label class="field-label" style="display:flex;flex-direction:column;gap:4px;" title="单票评分低于此分直接不入选; 与「评分下限(双低)」不同, 不看可信度">
            评分门槛 <input v-model.number="adminDefaults.scoreFloor" type="number" min="50" max="100" class="admin-input" style="width:110px;" />
          </label>
          <!-- 2026-08-25 正逻辑: 勾上=只看这类票(不勾=剔除); tooltip 保留说明; 主人要求去掉"只看"二字 -->
          <label class="field-label" style="display:flex;align-items:center;gap:6px;font-size:0.8125rem;" title="勾选后只显示昨日涨停/连板股; 不勾选则剔除">
            <input v-model="adminDefaults.limitUp" type="checkbox" /> 昨日涨停
          </label>
          <label class="field-label" style="display:flex;align-items:center;gap:6px;font-size:0.8125rem;" title="勾选后只显示 ST/停牌股; 不勾选则剔除">
            <input v-model="adminDefaults.stSuspend" type="checkbox" /> ST/停牌
          </label>
          <button class="tdx-export-btn admin-save-btn" :disabled="savingDefaults" @click="saveDefaults(false)">
            <i class="fa fa-save"></i> {{ savingDefaults ? '保存中...' : '保存默认值' }}
          </button>
          <button class="tdx-export-btn admin-save-btn save-force-btn" :disabled="savingDefaultsForce" @click="saveDefaults(true)">
            <i class="fa fa-bolt"></i> {{ savingDefaultsForce ? '生效中...' : '保存并强制生效' }}
          </button>
          <span v-if="defaultsMsg" :class="defaultsErr ? 'admin-msg-err' : 'admin-msg-ok'" style="font-size:0.75rem;">{{ defaultsMsg }}</span>
        </div>
      </div>

      <!-- 历史竞价回放 -->
      <PlaybackCard />

      <!-- ⋮ 操作下拉/期限面板 (teleport 到 body, fixed 定位跟随触发按钮; 短表格不溢出覆盖搜索栏) -->
      <Teleport to="body">
        <div
v-if="menuUid !== null && menuRect" class="row-menu"
             :style="{ position:'fixed', top: menuRect.top+'px', left: menuRect.left+'px' }"
             @click.stop
>
          <button class="row-menu-item" @click="menuAction(currentMenuUser, 'detail')">🔎 用户详情</button>
          <button class="row-menu-item" @click="menuAction(currentMenuUser, 'profile')">📝 编辑资料</button>
          <button class="row-menu-item" @click="menuAction(currentMenuUser, 'invites')">👥 邀请关系</button>
          <template v-if="currentMenuUser && !currentMenuUser.is_admin">
            <button class="row-menu-item" @click="menuAction(currentMenuUser, 'expire')">⚙️ 设置期限</button>
            <button class="row-menu-item" @click="menuAction(currentMenuUser, 'pwd')">🔑 重置密码</button>
            <button class="row-menu-item row-menu-danger" @click="menuAction(currentMenuUser, 'delete')">🗑️ 删除</button>
          </template>
          <div v-if="menuSection === 'expire'" class="row-menu-expire" @click.stop>
            <div class="pop-label">延长时长</div>
            <div class="pop-row">
              <button class="mini-btn" @click="extendUser(currentMenuUser, 'week')">+1周</button>
              <button class="mini-btn" @click="extendUser(currentMenuUser, 'month')">+1月</button>
              <button class="mini-btn" @click="extendUser(currentMenuUser, 'quarter')">+1季</button>
              <button class="mini-btn" @click="extendUser(currentMenuUser, 'year')">+1年</button>
            </div>
            <div class="pop-label">自定义到期日</div>
            <div class="pop-row">
              <input v-model="currentMenuUser._expireDate" type="date" class="mini-date" :max="'2099-12-31'" />
              <button class="mini-btn" @click="extendUser(currentMenuUser, 'date')">设为该日</button>
            </div>
            <div class="pop-row">
              <button class="mini-btn danger" title="永久有效" @click="extendUser(currentMenuUser, 'forever')">设为永久</button>
            </div>
          </div>
        </div>
      </Teleport>

      <!-- 用户详情抽屉(2026-09-21) -->
      <UserDetailDrawer ref="detailDrawer" @goto-expire="onDrawerGotoExpire" />
    </template>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { adminCreateUser, adminDeleteUser, adminScoring, adminSetMemberLevel, adminSetUserProfile, adminUserInvites, adminUsers, getAdminDefaults, resetUserPassword, saveAdminDefaults, saveScoring as apiSaveScoring, setUserExpire, setUsersExpire,
  // 第二批(2026-10-06): A4 分层标签 / A5 测试发送 / A7 导出 / A10 转化漏斗
  adminUserTags, adminRefreshTags, adminSetUserTags, adminFunnel, adminTestNotice,
  adminExportUrl } from '../api/admin'
// 2026-10-04 站内公告(系统消息的站方广播部分)
import {
  adminNotices, adminOffNotice, adminPublishNotice, adminEditNotice, adminNoticeCount,
} from '../api/notices'
import { showToast as toast } from '../utils/toast'
import { useSortable } from '../composables/useSortable'
import { expireState } from '../utils/admin'
import { fmtBjDay, fmtTsTime } from '../utils/time'
import StatCards from '../components/admin/StatCards.vue'
import PlaybackCard from '../components/admin/PlaybackCard.vue'
import MemberAdminPanel from '../components/MemberAdminPanel.vue'
import UserDetailDrawer from '../components/UserDetailDrawer.vue'

// 表格排序实例(用户列表)
const userSort = useSortable()

// 用户表取值函数: "手机/邮箱"列实际存 phone 或 email, 需合并取
function userVal(u) {
  const k = userSort.sortState.value ? userSort.sortState.value.key : null
  if (k === 'phone') return u.phone || u.email || ''
  return u[k]
}

// 全局默认筛选参数
// 2026-08-25 语义改为正逻辑: limitUp/stSuspend 默认 false = 默认"剔除这类票",
//   等价于旧默认(勾上剔除ST/剔除昨涨停) → 保持默认行为一致但 UI 直觉正确
const adminDefaults = reactive({ bidAmtFloor: 1000, bidGt: 7, floatMvFloor: 30, floatMvGt: 1000, priceGt: 300, probLt: 50, scoreFloor: 50, limitUp: false, stSuspend: false })
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

const menuUid = ref(null)        // ⋮ 下拉菜单当前打开的用户 id
const menuSection = ref('')      // 下拉内当前面板: '' 主菜单 | 'expire' 期限面板
const menuRect = ref(null)       // 菜单 fixed 定位 {top, left}; null=不显示
const currentMenuUser = computed(() => rows.value.find(x => x.id === menuUid.value) || null)

function closeMenu() {
  watchMenuPos(false)
  menuUid.value = null
  menuSection.value = ''
  menuRect.value = null
  menuBtnEl.value = null
}

const menuBtnEl = ref(null)      // 触发 ⋮ 的按钮元素(滚动跟随定位用)
const MENU_W = 200
const MENU_H = 56
const MENU_MARGIN = 6

function setMenuRect() {
  const btn = menuBtnEl.value
  if (!btn || !btn.isConnected) { closeMenu(); return }
  const r = btn.getBoundingClientRect()
  const menu = document.querySelector('.row-menu')
  const h = (menu && menu.offsetHeight) || MENU_H   // expire 面板更高, 用真实高度
  const up = (window.innerHeight - r.bottom) < (h + MENU_MARGIN + 12)
  menuRect.value = {
    top: up ? Math.max(8, r.top - h - MENU_MARGIN) : (r.bottom + MENU_MARGIN),
    left: Math.max(8, Math.min(window.innerWidth - MENU_W - 8, r.right - MENU_W))
  }
}

// 滚动/窗口缩放时跟随 ⋮ 按钮(捕获阶段能听到表格内层滚动), 避免菜单停在原地点不到
function onMenuScroll() {
  if (menuUid.value !== null) setMenuRect()
}
function watchMenuPos(on) {
  if (on) {
    window.addEventListener('scroll', onMenuScroll, true)
    window.addEventListener('resize', onMenuScroll)
  } else {
    window.removeEventListener('scroll', onMenuScroll, true)
    window.removeEventListener('resize', onMenuScroll)
  }
}

function toggleMenu(u, ev) {
  if (menuUid.value === u.id) { closeMenu(); return }
  menuUid.value = u.id
  menuSection.value = ''
  menuBtnEl.value = ev && ev.currentTarget
  setMenuRect()
  watchMenuPos(true)
}

// 点空白处关闭菜单(⋮ 按钮和菜单本身已 .stop, 不会被误关闭)
function onMenuOutsideClick(ev) {
  if (menuUid.value === null) return
  const el = document.querySelector('.row-menu')
  if (el && !el.contains(ev.target)) closeMenu()
}
onMounted(() => document.addEventListener('click', onMenuOutsideClick))
onBeforeUnmount(() => document.removeEventListener('click', onMenuOutsideClick))
function menuAction(u, act) {
  if (act === 'expire') {
    menuSection.value = 'expire'   // 下拉内切换到期限面板
    setTimeout(setMenuRect, 0)     // 面板渲染后高度变化, 重算定位(可能改向上展开)
  } else {
    closeMenu()
    if (act === 'profile') openProfile(u)
    else if (act === 'pwd') openPwdReset(u)
    else if (act === 'delete') deleteUser(u)
    else if (act === 'invites') openInvites(u)
    else if (act === 'detail') openUserDetail(u.id)
  }
}

/* ---- 用户详情抽屉(2026-09-21) ---- */
const detailDrawer = ref(null)
function openUserDetail(uid) {
  if (detailDrawer.value) detailDrawer.value.show(uid)
}
// 抽屉里点「设置到期」: 关掉抽屉, 打开该行的期限面板
function onDrawerGotoExpire(uid) {
  const u = rows.value.find((x) => x.id === uid)
  if (!u) { toast('该用户不在当前页，请先搜索定位', 'warning'); return }
  menuUid.value = uid
  menuSection.value = 'expire'
  setTimeout(setMenuRect, 0)
}
function onDocClick(e) {
  if (!e.target.closest('.expire-cell') && !e.target.closest('.row-actions')) {
    closeMenu()
  }
}
onMounted(() => document.addEventListener('click', onDocClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))

// ---------- 管理员重置密码 ----------
const pwdTarget = ref(null)
const pwdNew = ref('')
const pwdSaving = ref(false)
function openPwdReset(u) {
  pwdTarget.value = u
  pwdNew.value = ''
  closeMenu()
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

// ---------- 管理员代编辑用户资料(手机/邮箱/微信名/备注/付款备注) ----------
const profileTarget = ref(null)
const profileForm = reactive({ phone: '', email: '', wx_name: '', remark: '', pay_remark: '' })
const profileSaving = ref(false)
function openProfile(u) {
  profileTarget.value = u
  profileForm.phone = u.phone || ''
  profileForm.email = u.email || ''
  profileForm.wx_name = u.wx_name || ''
  profileForm.remark = u.remark || ''
  profileForm.pay_remark = u.pay_remark || ''
  closeMenu()
}
function closeProfile() {
  profileTarget.value = null
  profileForm.phone = profileForm.email = profileForm.wx_name = profileForm.remark = profileForm.pay_remark = ''
}
async function saveProfile() {
  if (!profileTarget.value) return
  const fields = {}
  if (profileForm.phone.trim() !== (profileTarget.value.phone || '')) fields.phone = profileForm.phone.trim()
  if (profileForm.email.trim() !== (profileTarget.value.email || '')) fields.email = profileForm.email.trim()
  if (profileForm.wx_name.trim() !== (profileTarget.value.wx_name || '')) fields.wx_name = profileForm.wx_name.trim()
  if (profileForm.remark.trim() !== (profileTarget.value.remark || '')) fields.remark = profileForm.remark.trim()
  if (profileForm.pay_remark.trim() !== (profileTarget.value.pay_remark || '')) fields.pay_remark = profileForm.pay_remark.trim()
  if (!Object.keys(fields).length) { toast('没有改动', 'error'); return }
  profileSaving.value = true
  try {
    await adminSetUserProfile(profileTarget.value.id, fields)
    toast(`${profileTarget.value.username} 资料已更新`, 'success')
    closeProfile()
    loadUsers(page.value)
  } catch (e) {
    toast(e.message || '保存失败', 'error')
  } finally {
    profileSaving.value = false
  }
}

// ---------- 管理员创建会员 ----------
const createOpen = ref(false)
const createSaving = ref(false)
const createResult = ref(null)
const createForm = reactive({
  username: '', password: '', phone: '', email: '',
  member_level: 1, expire_at: '', wx_name: '', remark: '', pay_remark: ''
})
const todayStr = new Date().toISOString().slice(0, 10)
function openCreate() {
  createOpen.value = true
  createResult.value = null
  // 默认 30 天后到期
  const d = new Date(); d.setDate(d.getDate() + 30)
  createForm.expire_at = d.toISOString().slice(0, 10)
  Object.assign(createForm, { username: '', password: '', phone: '', email: '',
    member_level: 1, wx_name: '', remark: '', pay_remark: '' })
}
function closeCreate() { createOpen.value = false; createResult.value = null }
// ---------- 邀请关系链(2026-08-17) ----------
const invitesTarget = ref(null)
const invitesLoading = ref(false)
const invitesData = ref({})
async function openInvites(u) {
  invitesTarget.value = u
  invitesData.value = {}
  invitesLoading.value = true
  try {
    const d = await adminUserInvites(u.id)
    invitesData.value = d
  } catch (e) {
    toast(e.message || '加载邀请关系失败', 'error')
  } finally {
    invitesLoading.value = false
  }
}
function closeInvites() { invitesTarget.value = null }
async function doCreate() {
  if (!createForm.username.trim() || createForm.username.trim().length < 2) { toast('用户名 2-20 位', 'error'); return }
  if (createForm.password.length < 6) { toast('密码至少 6 位', 'error'); return }
  if (!/^1[3-9]\d{9}$/.test(createForm.phone.trim())) { toast('手机号格式不正确', 'error'); return }
  if (!/^[\w.+-]+@[\w-]+(\.[\w-]+)+$/.test(createForm.email.trim())) { toast('邮箱格式不正确', 'error'); return }
  createSaving.value = true
  try {
    const body = {
      username: createForm.username.trim(),
      password: createForm.password,
      phone: createForm.phone.trim(),
      email: createForm.email.trim(),
      member_level: createForm.member_level,
      expire_at: createForm.expire_at || undefined,
      wx_name: createForm.wx_name.trim() || undefined,
      remark: createForm.remark.trim() || undefined,
      pay_remark: createForm.pay_remark.trim() || undefined,
    }
    const d = await adminCreateUser(body)
    createResult.value = d
    toast(`${d.username} 创建成功`, 'success')
    loadUsers(page.value)
  } catch (e) {
    toast(e.message || '创建失败', 'error')
  } finally {
    createSaving.value = false
  }
}
async function copyText(t) {
  try {
    await navigator.clipboard.writeText(t)
    toast('已复制', 'success')
  } catch {
    toast('复制失败, 请手动选中', 'error')
  }
}

// ---------- 管理员删除账号 ----------
async function deleteUser(u) {
  if (!window.confirm(`确认删除「${u.username}」？此操作会清除该用户的所有会话/选股记录/重置链接, 不可恢复。`)) return
  if (!window.confirm(`再次确认: 真的要删除 ${u.username} (ID ${u.id}) 吗?`)) return
  try {
    await adminDeleteUser({ uid: u.id })
    toast(`${u.username} 已删除`, 'success')
    loadUsers(page.value)
  } catch (e) {
    toast(e.message || '删除失败', 'error')
  }
}

// ---------- 会员筛选 tab ----------
// 注: 不设"会员"总览 tab, 避免与 VIP/付费 歧义 (会员 = 付费 + VIP 的包含关系)
const memberTabs = [
  { key: 'all', label: '全部' },
  { key: 'paid', label: '付费会员' },
  { key: 'vip', label: 'VIP' },
  { key: 'normal', label: '普通用户' },
  { key: 'admin', label: '管理员' },
]
const memberTab = ref('all')
function setMemberTab(k) {
  memberTab.value = k
  page.value = 1
  loadUsers(1)
}
// 后端按 member_tab 参数过滤; 分页 total 由服务端给出, 直接展示
async function loadUsers(p) {
  try {
    const d = await adminUsers({ page: p, pageSize: pageSize.value,
                                 keyword: keyword.value,
                                 memberTab: memberTab.value,
                                 tag: tagFilter.value })    // A4 按分层标签筛选
    rows.value = (d.rows || []).map(r => ({ ...r, _level: r.member_level || 0 }))
    total.value = d.total || 0
    page.value = d.page || 1
    stats.value = d.stats || {}
  } catch (e) {
    if (e.status === 403) denied.value = true
    else toast(e.message || '加载失败', 'error')
  }
}

const stats = ref({})
const rows = ref([])
const page = ref(1)
const total = ref(0)
const pageSize = ref(10)
const keyword = ref('')
const denied = ref(false)
// 批量设置到期(2026-08-17): 多选 checkbox
const selectedIds = ref([])
const batchExpireOpen = ref(false)
const batchExpireDate = ref('')
const batchSaving = ref(false)
const pageAllChecked = computed(() => {
  const list = userSort.sorted(rows.value, userVal).filter(u => !u.is_admin)
  return list.length > 0 && list.every(u => selectedIds.value.includes(u.id))
})
// 行 checkbox 显式 :checked + @change(2026-08-17): 之前 v-model + :value="u.id" 在 Vue3 展开后
// :value 会覆盖 v-model 的 modelValue prop, 导致 DOM checked 变了但 selectedIds 数组不更新,
// 批量按钮始终 disabled. 改用显式切换避免 prop 冲突
function toggleSelect(uid, checked) {
  if (checked) {
    if (!selectedIds.value.includes(uid)) selectedIds.value.push(uid)
  } else {
    selectedIds.value = selectedIds.value.filter(x => x !== uid)
  }
}
function togglePageAll(e) {
  const list = userSort.sorted(rows.value, userVal).filter(u => !u.is_admin)
  if (e.target.checked) {
    list.forEach(u => { if (!selectedIds.value.includes(u.id)) selectedIds.value.push(u.id) })
  } else {
    const ids = list.map(u => u.id)
    selectedIds.value = selectedIds.value.filter(id => !ids.includes(id))
  }
}
function openBatchExpire() {
  if (!selectedIds.value.length) { toast('请先勾选用户', 'error'); return }
  batchExpireDate.value = ''
  batchExpireOpen.value = true
}
function closeBatchExpire() { batchExpireOpen.value = false }
async function batchExtend(duration) {
  const label = { week: '+1周', month: '+1月', quarter: '+1季', year: '+1年' }[duration]
  await doBatchExpire({ duration }, label)
}
async function batchSetDate() {
  if (!batchExpireDate.value) { toast('请先选择日期', 'error'); return }
  await doBatchExpire({ expire_at: batchExpireDate.value }, '设日期')
}
async function batchForever() { await doBatchExpire({ days: 0 }, '永久') }
async function doBatchExpire(payload, label) {
  const uids = selectedIds.value
  if (!uids.length) { toast('未勾选用户', 'error'); return }
  batchSaving.value = true
  try {
    const d = await setUsersExpire(uids, payload)
    toast(`${label}批量设置完成: 成功 ${d.success}/${d.total}`, d.failed && d.failed.length ? 'warning' : 'success')
    batchExpireOpen.value = false
    selectedIds.value = []
    loadUsers(page.value)
  } catch (e) {
    toast(e.message || '批量设置失败', 'error')
  } finally {
    batchSaving.value = false
  }
}

const scoring = reactive({})        // 当前策略的评分配置(竞价/盘中各一套)
const wKeys = ref([])
const confKeys = ref([])
// 2026-09-28 v4.11.76: 评分配置双策略 —— auction(竞价, settings.scoring)
// / spot(盘中实时, settings.scoring_spot)。两套因子表**互相独立**:
//   竞价 5 因子(bid/activity/warn/market/yesterday)
//   盘中 6 因子(chg/vol_ratio/turnover/seal/market/yesterday)
// 切页签 = 切视图 + 重拉数据; 不做任何写入, 也不"合并成一套"。
const scoringStrategy = ref('auction')
const scoringDirty = ref(false)     // 有未保存改动(切策略时提示, 防误以为丢了)
// 打分明细当前激活的因子Tab。⚠️ 两策略因子集不同, 切策略时必须重置为
// **该策略的首个因子**, 否则会停在竞价才有的 'bid' 上 → 面板显示"该因子暂未加载"。
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

const factors = reactive({})        // 当前策略的打分明细
// 因子 Tab 顺序按策略切换 —— 后端 w_keys 已按此顺序返回, 但打分明细 Tab 需要
// 一个"遍历哪些因子"的稳定列表, 故此处显式登记(与 admin.py W_KEYS/SPOT_W_KEYS 对齐)。
const FACTOR_ORDER = {
  auction: ['bid', 'activity', 'warn', 'market', 'yesterday'],
  spot: ['chg', 'vol_ratio', 'turnover', 'seal', 'market', 'yesterday']
}
const factorOrder = computed(() => FACTOR_ORDER[scoringStrategy.value] || FACTOR_ORDER.auction)
const scoringLoaded = ref(false)

// ---- 「权重组」因子(竞价强度 bid_strength)的子权重编辑态 ----
// 2026-09-30: 该组不在上面五因子的 buckets 表格里。旧版保存只从五因子表单重建
//   payload.factors ⇒ bid_strength 被整块丢掉, 后端整表替换后 w_zb(竞价昨比)静默变 0
//   (线上「昨比 40%」就是这么失效的), 且界面无入口配回。现在: 拉取时存进 strength、
//   保存时原样回传 ⇒ 后台保存不再丢, 也能在界面上直接调。
const STRENGTH_FACTOR = 'bid_strength'
const STRENGTH_KEYS = [
  ['w_vol_ratio', '量比档', '今日9:25竞价额 ÷ 昨日9:25竞价额 的分档权重'],
  ['w_zb', '竞价昨比档', '竞价额 ÷ 昨日全天成交额 的分档权重（0 = 不参与评分）'],
  ['w_ai', 'AI 档', 'aipick XGBoost 全市场 Top30 概率分档权重'],
  ['w_ff', '净额档', '竞价主力净额分档权重（已废弃，建议保持 0）']
]
const strength = reactive({})      // 该重组的完整对象(含各档位表/默认分, 原样回传)
const strengthSum = computed(() => {
  let s = 0
  STRENGTH_KEYS.forEach(([k]) => { const v = Number(strength[k]); if (!isNaN(v)) s += v })
  return s
})

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
    toast(`${u.username} ${label}设置成功，到期 ${fmtBjDay(d.expire_at)}`, 'success')
    u._expireDate = ''
    closeMenu()
    loadUsers(page.value)
  } catch (e) {
    toast(e.message || '设置失败', 'error')
  }
}

// 每页条数变更: 回第 1 页重载
function changePageSize() {
  loadUsers(1)
}

// 会员等级: 0=免费试用 1=付费会员 2=VIP (永久)
function levelLabel(lv) {
  return lv === 2 ? 'VIP' : lv === 1 ? '付费会员' : '免费试用'
}
async function setLevel(u) {
  try {
    const d = await adminSetMemberLevel(u.id, u._level)
    toast(`${u.username} 已设为「${d.label}」`, 'success')
    u.member_level = u._level
  } catch (e) {
    toast(e.message || '设置失败', 'error')
    u._level = u.member_level || 0
  }
}

/**
 * 拉取指定策略的评分配置并铺进表单。
 * 2026-09-28 v4.11.76: 加 strategy 参数 —— 后端按策略返回**各自的** w_keys/
 * conf_keys/factors(竞价五因子 / 盘中六因子)。切换策略必须重拉, 不能复用上一次的。
 */
async function loadScoring(strategy = scoringStrategy.value) {
  try {
    const d = await adminScoring(strategy)
    // 🔴 必须清空再 Assign: 两策略的数值键**部分不同**(竞价有 w_bid 无 w_chg;
    //   盘中有 w_chg 无 w_bid), 若只 Object.assign 会残留上一套的键 ⇒ 保存时
    //   把竞价的 w_bid 一起发给盘中配置(后端 _validate 会报"权重 w_bid 必须是数字")。
    Object.keys(scoring).forEach((k) => { delete scoring[k] })
    Object.assign(scoring, d.scoring || {})
    wKeys.value = d.w_keys || []
    confKeys.value = d.conf_keys || []
    // 打分明细同理: 先清空, 否则旧的因子块会残留在 factors 里被一并保存。
    Object.keys(factors).forEach((k) => { delete factors[k] })
    const fac = (d.scoring && d.scoring.factors) || {}
    factorOrder.value.forEach((fk) => {
      factors[fk] = fac[fk] || { label: fk, unit: '', buckets: [], default: 0.1 }
      // buckets 行转数组, 便于 v-model.number 双向绑定
      factors[fk].buckets = (factors[fk].buckets || []).map((b) => [Number(b[0]), Number(b[1]), Number(b[2])])
    })
    // 权重组(竞价强度): 单独存一份, 保存时原样回传 —— 否则会被整块丢掉。
    // 后端 GET 返回的是**合并后**的配置, 所以这里拿到的就是当前生效值, 可直接改。
    Object.keys(strength).forEach((k) => { delete strength[k] })
    const bs = fac[STRENGTH_FACTOR]
    if (bs && typeof bs === 'object') Object.assign(strength, bs)
    // 激活 Tab 归位到**该策略的首个因子** —— 否则从竞价('bid')切到盘中会停在
    // 'bid'(盘中无此因子) → 面板显示"该因子暂未加载", 看起来像数据没拉到。
    activeFactor.value = factorOrder.value[0]
    scoringLoaded.value = true
    scoringDirty.value = false
  } catch (e) {
    if (e.status === 403) denied.value = true
    else toast(e.message || '加载失败', 'error')
  }
}

/** 切换评分策略(竞价 ↔ 盘中)。有未保存改动时先确认, 避免改动静默丢失。 */
async function switchScoringStrategy(s) {
  if (s === scoringStrategy.value) return
  if (scoringDirty.value) {
    const ok = window.confirm('当前策略有未保存的改动，切换后将丢失。确定切换？')
    if (!ok) return
  }
  scoringStrategy.value = s
  saveMsg.value = ''
  await loadScoring(s)
}

function addBucket(fk) {
  factors[fk].buckets.push([0, 1, 0.5])
  scoringDirty.value = true
}

function delBucket(fk, idx) {
  factors[fk].buckets.splice(idx, 1)
  scoringDirty.value = true
}

async function saveScoring() {
  saveMsg.value = ''
  const payload = {}
  Object.keys(scoring).forEach((k) => {
    if (!k.startsWith('factors')) payload[k] = Number(scoring[k])
  })
  payload.factors = {}
  factorOrder.value.forEach((fk) => {
    const f = factors[fk]
    if (!f || !f.buckets) return
    payload.factors[fk] = {
      label: f.label, unit: f.unit, default: Number(f.default),
      buckets: f.buckets.map((b) => [String(b[0]), String(b[1]), Number(b[2])])
    }
  })
  // 权重组(竞价强度子权重)原样回传 —— 不带上就会把 w_zb(昨比)等静默清成默认值。
  if (Object.keys(strength).length) {
    const bs = { ...strength }
    STRENGTH_KEYS.forEach(([k]) => { bs[k] = Number(bs[k]) || 0 })   // 空值当 0, 避免字符串
    payload.factors[STRENGTH_FACTOR] = bs
  }
  const strategy = scoringStrategy.value
  saving.value = true
  try {
    const d = await apiSaveScoring(payload, strategy)
    saveMsg.value = d.msg || '已保存'
    saveErr.value = false
    scoringDirty.value = false
    const name = strategy === 'spot' ? '盘中实时' : '竞价'
    toast(`${name}权重与打分明细已保存并生效`, 'success')
  } catch (e) {
    saveMsg.value = e.message || '保存失败'
    saveErr.value = true
  } finally {
    saving.value = false
  }
}

// ---------- 站内公告(2026-10-04 建立 / 2026-10-06 扩展) ----------
// 🔴 这里只管**站方广播**(系统更新提醒等)。会员到期提醒不在这里发 ——
//    它由消息中心按 expire_at 自动生成(T-7/T-3/T-1/T+0), 人工发会在用户续费后变成永久脏数据。
//    要催续费: 去「到期预警」点「一键提醒」(会给每个人补发一条, 且带 expire_at 快照自愈)。
const notices = ref([])
const nf = reactive({
  title: '', body: '', level: 'info', target: 'all', days: 0,
  category: 'system', status: 'sent', publish_at: '',
  action_type: '', action_value: '',
  tag: '',                       // A4 标签定向: 仅 target==='tag' 时生效
})
const nfPosting = ref(false)
const nfTesting = ref(false)
const noticeCount = ref({ total: 0, pushable: 0 })

function noticeLevelLabel(l) { return ({ info: '普通', warn: '提醒', urgent: '紧急' })[l] || l }
function noticeTargetLabel(t) {
  if (t === 'tag') return '标签'
  return ({ all: '全部', free: '免费试用', member: '付费/VIP', vip: 'VIP' })[t] || t
}
function noticeCatLabel(c) { return ({ system: '系统', account: '账户', trade: '交易' })[c] || c || '系统' }

const canPublish = computed(() => {
  if (!nf.title.trim() || !nf.body.trim()) return false
  if (nf.status === 'scheduled' && !nf.publish_at) return false
  return true
})

async function loadNoticeCount() {
  try {
    const d = await adminNoticeCount(nf.target, nf.tag)
    if (d && d.ok) noticeCount.value = d.data || { total: 0, pushable: 0 }
  } catch (e) { noticeCount.value = { total: 0, pushable: 0 } }
}

// A5「仅自己可见」的测试发送: 发布前先看清楚这条消息长什么样, 只有你自己能看到。
// 🔴 默认不推送 —— 测试的是排版, 不该顺手把人推一遍。想验证推送通道再勾 push。
async function testSendNotice() {
  if (!nf.title.trim()) { toast('先填个标题再测试发送', 'error'); return }
  nfTesting.value = true
  try {
    const d = await adminTestNotice({
      title: nf.title, body: nf.body, level: nf.level, category: nf.category,
      action_type: nf.action_type, action_value: nf.action_value, push: false,
    })
    if (d && d.ok) toast('已发送，去 /messages 查看（仅你可见，不会推送给别人）', 'success')
    else toast('测试发送失败：' + ((d && d.msg) || ''), 'error')
  } catch (e) {
    toast('测试发送失败：' + (e.message || ''), 'error')
  } finally { nfTesting.value = false }
}

// ---------- 第二批: A4 用户分层标签 ----------
// 标签是**定向运营的前提**: 没有它, 公告只能对"全部/免费/付费/VIP"四档说话,
// 没法单独对"免费但很活跃"这群最该转化的人说话。
const tagSummary = ref([])
const tagFilter = ref('')            // 用户列表当前按哪个标签筛
const tagRefreshing = ref(false)

async function loadTags() {
  try {
    const d = await adminUserTags()
    if (d && d.ok) tagSummary.value = d.tags || []
  } catch (e) { /* 静默: 标签是运营增强项, 挂了不影响主流程 */ }
}

async function refreshTags() {
  tagRefreshing.value = true
  try {
    const d = await adminRefreshTags()
    if (d && d.ok) {
      toast('已重算自动标签（' + (d.rows || 0) + ' 条）', 'success')
      await loadTags()
    } else toast('重算失败', 'error')
  } catch (e) {
    toast('重算失败：' + (e.message || ''), 'error')
  } finally { tagRefreshing.value = false }
}

// 点标签 = 用户列表按该标签筛选(再点一次取消)
function pickTag(t) {
  tagFilter.value = tagFilter.value === t ? '' : t
  loadUsers(1)
}

// ---------- 第二批: A10 转化漏斗 ----------
// 🔴 「咨询客服」这一环**没有埋点**(客服是微信人工) ⇒ 后端返回 null, 这里显示「无数据」。
//    宁可空着也不编个数字填进去 —— 运营是拿这个看板决定投入方向的。
const funnel = ref(null)
const funnelDays = ref(30)

async function loadFunnel() {
  try {
    const d = await adminFunnel(funnelDays.value)
    if (d && d.ok) funnel.value = d
  } catch (e) { funnel.value = null }
}

function funnelWidth(n) {
  const st = funnel.value && funnel.value.stages
  const base = st && st[0] ? st[0].count : 0
  return base ? Math.max(2, Math.round(n / base * 100)) : 0
}

// 点击率(分母用定向人群, 与已读率同口径, 两个率才可比)
function clickRate(n) {
  return n && n.target_count ? Math.round((n.click_count || 0) * 1000.0 / n.target_count) / 10 : 0
}

// A7 导出: 浏览器直接下载(不走 request 封装, 后端返回的是 CSV 流)
function downloadCsv(url) {
  const a = document.createElement('a')
  a.href = url
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  a.remove()
}

function exportReach(n) {
  if (!n || !n.nkey) { toast('这条公告没有 nkey，导不出明细', 'error'); return }
  downloadCsv(adminExportUrl({ kind: 'reach', nkey: n.nkey }))
}

function exportUsers(kind) {
  const p = { kind }
  if (kind === 'users') {
    if (keyword.value) p.keyword = keyword.value
    if (memberTab.value && memberTab.value !== 'all') p.memberTab = memberTab.value
    if (tagFilter.value) p.tag = tagFilter.value
  }
  downloadCsv(adminExportUrl(p))
}

// A4 手动标签: 逗号分隔; 留空 = 清空手动标签(自动标签不会被删, 重算时也不会冲掉手动标签)
async function editTags(u) {
  const cur = (u.tags || []).filter(t => t.source === 'manual').map(t => t.tag).join(',')
  const v = window.prompt('手动标签（英文逗号分隔；留空清空；自动标签不受影响）', cur)
  if (v === null) return
  try {
    const d = await adminSetUserTags(u.id, v.split(',').map(s => s.trim()).filter(Boolean))
    if (d && d.ok) {
      toast('已更新标签', 'success')
      await loadUsers(page.value)
      await loadTags()
    } else toast('更新失败', 'error')
  } catch (e) {
    toast('更新失败：' + (e.message || ''), 'error')
  }
}

async function loadNotices() {
  try {
    const d = await adminNotices()
    if (d && d.ok) notices.value = d.items || []
  } catch (e) { /* 管理端已有全局错误提示, 这里静默即可 */ }
}

async function publishNotice() {
  if (!canPublish.value) {
    if (nf.status === 'scheduled' && !nf.publish_at) toast('请选择定时时刻', 'error')
    else toast('标题与正文都不能为空', 'error')
    return
  }
  nfPosting.value = true
  try {
    const payload = { ...nf }
    // datetime-local 给的是 "YYYY-MM-DDTHH:MM"(本地时区), 后端按北京时间解析
    if (nf.status !== 'scheduled') delete payload.publish_at
    const d = await adminPublishNotice(payload)
    if (d && d.ok) {
      toast(nf.status === 'draft' ? '草稿已保存' : (nf.status === 'scheduled' ? '已排期，到点自动发送' : '公告已发布'), 'success')
      nf.title = ''; nf.body = ''; nf.publish_at = ''
      await loadNotices()
    } else toast('发布失败：' + ((d && d.msg) || ''), 'error')
  } catch (e) {
    toast('发布失败：' + (e.message || ''), 'error')
  } finally { nfPosting.value = false }
}

async function editNotice(n) {
  // 简单起见只改正文与定时时刻 —— 这两项是"发错了最想改的", 也是已投递后仍可改的字段
  const body = window.prompt('修改正文（已发送的消息改标题/定向会让已读统计对不上，故只允许改正文）', n.body || '')
  if (body === null) return
  const patch = { nkey: n.nkey, body: body.trim() }
  if (n.status === 'scheduled') {
    const at = window.prompt('定时时刻（北京时间 YYYY-MM-DD HH:MM）', n.publish_date || '')
    if (at === null) return
    patch.publish_at = at.trim()
  }
  try {
    const d = await adminEditNotice(patch)
    if (d && d.ok) { toast('已更新', 'success'); await loadNotices() }
    else toast('更新失败：' + ((d && d.msg) || ''), 'error')
  } catch (e) { toast('更新失败', 'error') }
}

async function offNotice(n) {
  try {
    const d = await adminOffNotice({ nkey: n.nkey, id: n.id })
    if (d && d.ok) { toast('已撤回', 'success'); await loadNotices() }
    else toast('撤回失败', 'error')
  } catch (e) { toast('撤回失败', 'error') }
}

onMounted(() => {
  loadUsers(1)
  loadScoring('auction')   // 显式: 首屏默认竞价页签(与 scoringStrategy 初值一致)
  loadDefaults()
  loadNotices()
  loadNoticeCount()
  // 第二批: 标签汇总(定向要用) + 转化漏斗
  loadTags()
  loadFunnel()
})
</script>

<style scoped>
.inviter-tag {
  display: inline-block; padding: 2px var(--s2); border-radius: var(--r-lg);
  background: rgba(120, 160, 255, 0.15); color: var(--accent-text);
  border: 1px solid rgba(120, 160, 255, 0.35); font-size: var(--fs-xs); white-space: nowrap;
}
body[data-bg="light"] .inviter-tag { color: var(--brand-deep); background: rgba(90, 130, 255, 0.1); border-color: rgba(90, 130, 255, 0.4); }
.mono { font-family: inherit; }
.invite-chain { display: flex; flex-direction: column; gap: var(--s2); padding: var(--s2) var(--s1); }
.chain-row { display: flex; align-items: center; gap: var(--s2); font-size: var(--fs-sm); }
.chain-label { width: 72px; color: var(--text-muted); flex-shrink: 0; }
.chain-val { color: var(--text-main); }
.invite-list-scroll { max-height: 300px; overflow-y: auto; margin-top: var(--s2); border: 1px solid var(--border-soft); border-radius: var(--r-md); }
.invite-table { width: 100%; border-collapse: collapse; font-size: var(--fs-xs); }
.invite-table th { text-align: left; padding: var(--s2) var(--s2); color: var(--text-muted); border-bottom: 1px solid var(--border-soft); background: var(--bg-subtle); position: sticky; top: 0; }
.invite-table td { padding: var(--s2) var(--s2); border-bottom: 1px solid var(--border-soft); }
.invite-table tr:last-child td { border-bottom: none; }
.admin-wrap { max-width: 1500px; margin: 0 auto; padding: var(--s4) var(--s4); }
.admin-head { display: flex; align-items: center; margin-bottom: var(--s4); }
/* 2026-10-07 视觉自查收口: ① 大标题回归全站标题色(原内联 --accent-text 粉色); ② .card-title 补 700 与其他卡片标题一致;
   ③ 公告表单标题/正文原本是两个 inline-block 挤一行、基线错位 ⇒ 收成纵向表单; ④ .admin-btn 此前整仓无定义(浏览器默认白底按钮) ⇒ 补全站一致样式。 */
.admin-title { margin: 0 auto; color: var(--text-main); font-size: var(--fs-2xl); font-weight: 700; }
.notice-form { display: flex; flex-direction: column; gap: var(--s2); }
.notice-form .admin-input { width: 100%; box-sizing: border-box; }
.notice-form textarea.admin-input { resize: vertical; min-height: 72px; }
.notice-form-row { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s2); }
.admin-btn { background: var(--bg-input); border: 1px solid var(--border-soft); color: var(--text-secondary); border-radius: var(--r-md); padding: var(--s1) var(--s2); font-size: var(--fs-xs); cursor: pointer; }
.admin-btn:hover { background: var(--bg-hover); color: var(--text-main); border-color: var(--accent); }
.admin-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.admin-card { background: var(--bg-subtle); border: 1px solid var(--border-soft); border-radius: var(--r-lg); padding: var(--s4); margin-bottom: var(--s4); }
.field-label { color: #bbb; font-size: var(--fs-xs); }
.admin-tip { color: var(--text-muted); font-size: var(--fs-xs); margin-left: var(--s2); }
.admin-save-btn {
  background: rgba(120,200,80,0.2);
  border: 1px solid var(--success);
  color: var(--success-text);
  padding: var(--s2) var(--s4);
  border-radius: var(--r-md);
  font-size: var(--fs-sm);
  cursor: pointer;
  transition: opacity 0.15s;
}
.admin-save-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.admin-save-btn:hover:not(:disabled) { background: rgba(120,200,80,0.32); }
/* 「保存并强制生效」按钮: 深色主题用深红警示色 */
.save-force-btn { background: #A32D2D; border: 1px solid #A32D2D; color: #fff; }
.save-force-btn:hover:not(:disabled) { background: #c93838; border-color: #c93838; }
.admin-msg-ok { color: var(--success-text); }
.admin-msg-err { color: var(--brand-soft); }
.weight-desc { color: var(--text-muted); font-size: var(--fs-xs); }
.weight-total { color: var(--accent-text); font-size: var(--fs-sm); }
.weight-warn { color: var(--brand-soft); }
.admin-warn { color: var(--brand-soft); }
.admin-ok-text { color: var(--success-text); }
.card-title { display: flex; align-items: center; font-size: var(--fs-md); color: var(--warn-text); margin-bottom: var(--s3); }
.admin-input { background: var(--bg-input); border: 1px solid var(--border-soft); border-radius: var(--r-md); color: var(--text-main); padding: var(--s2) var(--s2); font-size: var(--fs-sm); }
.admin-input:focus { outline: none; border-color: var(--star); }
/* 2026-08-17: 弹窗内输入框放大(主人反馈: 设置资料/时间的框太小) */
.admin-input-lg { padding: var(--s2) var(--s3); font-size: var(--fs-base); border-radius: var(--r-md); }
.batch-expire-box { margin-top: var(--s1); }
.batch-exp-btn { padding: var(--s2) var(--s4); font-size: var(--fs-sm); border-radius: var(--r-md); }
.batch-forever { background: rgba(255,90,90,0.15); color: var(--brand-soft); border-color: rgba(255,90,90,0.5); }
.admin-search-btn {
  background: rgba(var(--accent-rgb),0.15);
  border: 1px solid var(--accent);
  color: var(--accent-text);
  border-radius: var(--r-md);
  padding: var(--s2) var(--s4);
  font-size: var(--fs-sm);
  cursor: pointer;
}
/* 工具栏彩色按钮: 用 class 而非内联 style(内联会盖住 light 主题覆盖), 按主题加深 */
.btn-warn {
  background: rgba(255,160,40,0.15);
  border-color: #ffa028;
  color: #ffa028;
}
body[data-bg="light"] .btn-warn {
  background: rgba(224,138,0,0.15);
  border-color: #b5740e;
  color: #965a00;
}
.btn-warn:disabled { opacity: 0.45; cursor: not-allowed; }
.btn-create {
  background: rgba(0,200,120,0.15);
  border-color: #00c878;
  color: #80ffaa;
}
body[data-bg="light"] .btn-create {
  background: rgba(0,168,100,0.15);
  border-color: #00a864;
  color: #007a48;
}
.factor-tabs {
  display: flex;
  gap: var(--s2);
  margin: var(--s1) 0 var(--s4);
  flex-wrap: wrap;
}
.factor-tab {
  background: var(--bg-subtle);
  border: 1px solid var(--border-soft);
  color: var(--text-secondary);
  border-radius: var(--r-md);
  padding: var(--s2) var(--s4);
  font-size: var(--fs-sm);
  cursor: pointer;
  transition: border-color 0.2s, color 0.2s;
}
.factor-tab:hover { border-color: var(--star); color: var(--warn-text); }
.factor-tab.active {
  background: rgba(120,200,80,0.14);
  border-color: var(--success);
  color: #c8f0a8;
}
.table-scroll { overflow-x: auto; }
.admin-table { width: 100%; border-collapse: collapse; font-size: var(--fs-sm); }
.admin-table th, .admin-table td { border-bottom: 1px solid var(--border-soft); padding: var(--s2) var(--s2); text-align: center; color: var(--text-secondary); }
.admin-table th { color: var(--text-muted); font-weight: 500; }
.admin-tag { color: var(--gold); border: 1px solid var(--gold); border-radius: var(--r-sm); padding: 1px var(--s2); font-size: var(--fs-xs); }
.user-tag { color: var(--text-muted); border: 1px solid var(--text-faint); border-radius: var(--r-sm); padding: 1px var(--s2); font-size: var(--fs-xs); }
/* 角色徽标 (合并 is_admin + member_level 显示, 紧贴用户名) */
.user-name-row { display: inline-flex; align-items: center; gap: var(--s2); }
.role-badge { display: inline-block; border-radius: var(--r-sm); padding: 2px var(--s2); font-size: var(--fs-xs); font-weight: 600; }
.role-admin { color: var(--accent-text); border: 1px solid var(--accent-text); background: rgba(74,158,255,0.12); }
.role-vip   { color: var(--warn-amber); border: 1px solid var(--warn-amber); background: rgba(255,179,71,0.15); }
.role-paid  { color: var(--brand-soft); border: 1px solid var(--brand-soft); background: rgba(255,106,106,0.12); }
.role-trial { color: var(--text-muted); border: 1px solid var(--text-faint); }
/* 备注/付款备注单元格 */
.cell-note { display: block; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-secondary); font-size: var(--fs-xs); }
.cell-pay { display: block; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--accent-text); font-size: var(--fs-xs); margin-top: 2px; }
/* ⋮ 操作下拉 (Teleport 到 body, 定位由内联 style position:fixed 控制) */
.row-actions { display: inline-block; }
.row-menu { z-index: 100000; min-width: 168px; max-width: 220px;
  background: var(--bg-panel-solid); border: 1px solid var(--border-soft); border-radius: var(--r-md);
  box-shadow: var(--sh-2); padding: var(--s1); }
.row-menu-item { display: block; width: 100%; text-align: left; padding: var(--s2) var(--s2); font-size: var(--fs-sm);
  background: transparent; border: 0; color: var(--text-main); cursor: pointer; border-radius: var(--r-sm); }
.row-menu-item:hover { background: rgba(var(--accent-rgb),0.12); }
.row-menu-danger { color: var(--brand-soft); }
.row-menu-danger:hover { background: rgba(255,80,80,0.12); }
.row-menu-expire { margin-top: var(--s2); padding-top: var(--s2); border-top: 1px dashed var(--border-soft); }
.expired-tag { color: var(--brand-soft); border: 1px solid var(--accent); border-radius: var(--r-sm); padding: 1px var(--s2); font-size: var(--fs-xs); }
.ok-tag { color: var(--success-text); border: 1px solid #4caf70; border-radius: var(--r-sm); padding: 1px var(--s2); font-size: var(--fs-xs); }
.expire-cell { position: relative; display: inline-block; }

/* 会员等级 */
.level-cell { display: inline-flex; align-items: center; gap: var(--s2); }
.level-tag { border-radius: var(--r-sm); padding: 1px var(--s2); font-size: var(--fs-xs); white-space: nowrap; }
.level-0 { color: var(--text-muted); border: 1px solid var(--text-faint); }
.level-1 { color: var(--brand-soft); border: 1px solid rgba(255, 90, 90, 0.55); }
.level-2 { color: var(--gold); border: 1px solid var(--gold); }
.level-select {
  background: var(--bg-input); color: var(--text-secondary);
  border: 1px solid var(--border-soft); border-radius: var(--r-sm);
  font-size: var(--fs-xs); padding: var(--s1) var(--s2); cursor: pointer; min-width: 100px;
}
.expire-popover {
  position: absolute;
  top: 28px;
  right: 0;
  z-index: 10;
  background: var(--bg-panel-solid);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-md);
  padding: var(--s2) var(--s3);
  min-width: 220px;
  box-shadow: var(--sh-2);
}
.pop-label { font-size: var(--fs-xs); color: var(--text-muted); margin: var(--s2) 0 var(--s1); }
.pop-label:first-child { margin-top: 0; }
.pop-row { display: flex; gap: var(--s1); margin-bottom: var(--s2); align-items: center; flex-wrap: wrap; }
.mini-btn { background: rgba(var(--accent-rgb),0.12); border: 1px solid var(--accent); color: var(--accent-text); border-radius: var(--r-sm); padding: var(--s1) var(--s2); font-size: var(--fs-xs); cursor: pointer; }
.mini-btn:hover { background: rgba(var(--accent-rgb),0.25); }
.mini-btn.danger { background: rgba(255,80,80,0.12); border-color: var(--accent); color: var(--accent-text); }
.mini-btn.danger:hover { background: rgba(255,80,80,0.25); }
.pwd-btn { background: rgba(255,180,0,0.12); border: 1px solid var(--star); color: var(--warn-text); }
.pwd-btn:hover { background: rgba(255,180,0,0.25); }
.pwd-mask { position: fixed; inset: 0; background: rgba(0,0,0,0.55); display: flex; align-items: center; justify-content: center; z-index: 100; }
.pwd-pop { background: var(--bg-panel-solid); border: 1px solid var(--border-soft); border-radius: var(--r-lg); padding: var(--s4) var(--s5); min-width: 320px; box-shadow: var(--sh-2); }
.pwd-title { font-size: var(--fs-base); color: var(--warn-text); margin-bottom: var(--s3); }
/* 编辑资料弹窗: 2 列网格 */
.profile-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--s2) var(--s3); }
.profile-label { display: flex; flex-direction: column; gap: var(--s1); font-size: var(--fs-xs); color: var(--text-muted); text-align: left; }
.profile-label .admin-input { width: 100%; box-sizing: border-box; }
.profile-label textarea.admin-input { resize: vertical; min-height: 36px; font-family: inherit; }
.profile-label.profile-pay { color: var(--accent-text); }
.profile-tip { font-size: var(--fs-xs); color: var(--text-muted); margin-top: var(--s2); text-align: left; }
/* 用户列表: 微信名/备注小字 */
.user-sub { font-size: var(--fs-xs); color: var(--text-muted); margin-top: 2px; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.user-remark { color: #b8965a; }
.user-pay { color: var(--accent-text); font-weight: 500; }
/* 会员筛选 tab */
.member-tabs { display: flex; flex-wrap: wrap; gap: 2px; background: rgba(255,255,255,0.04); padding: 2px; border-radius: var(--r-md); }
.member-tab { background: transparent; border: 0; color: var(--text-muted); padding: var(--s1) var(--s2); font-size: var(--fs-xs); cursor: pointer; border-radius: var(--r-sm); white-space: nowrap; }
.member-tab:hover { color: var(--text-main); }
.member-tab.active { background: rgba(var(--accent-rgb),0.18); color: var(--accent-text); }
/* 浅色主题: 会员 tab 选中态用深色(白底浅蓝字看不清) */
body[data-bg="light"] .member-tabs { background: rgba(0,0,0,0.04); }
body[data-bg="light"] .member-tab { color: #6b7280; }
body[data-bg="light"] .member-tab:hover { color: #1a1d26; }
body[data-bg="light"] .member-tab.active { background: rgba(11,134,200,0.18); color: var(--brand-deep); font-weight: 600; }
/* 用户列表工具区: desktop 横排右对齐(标题左边, tab+搜索+按钮挤右) */
.user-toolbar {
  display: flex;
  gap: var(--s2);
  margin-left: auto;
  align-items: center;
  flex-wrap: wrap;
}
/* 手机端(2026-08-18 主人反馈 5tab+搜索+3按钮挤一起): 工具区换 3 行布局 */
@media (max-width: 768px) {
  .admin-card .card-title { flex-direction: column; align-items: stretch; }
  .user-toolbar {
    margin-left: 0;
    width: 100%;
    gap: var(--s2);
  }
  .user-toolbar > .member-tabs { flex: 1 1 100%; }
  .user-toolbar > .member-tabs .member-tab { flex: 1 0 auto; text-align: center; padding: var(--s1) var(--s2); }
  .user-toolbar > input.admin-input { flex: 1 1 auto; min-width: 120px; box-sizing: border-box; }
  .user-toolbar > .admin-search-btn { padding: var(--s2) var(--s2); font-size: var(--fs-xs); }
  .user-toolbar > .btn-warn,
  .user-toolbar > .btn-create { flex: 1 1 45%; }
}
/* 创建结果展示 */
.create-result { margin-top: var(--s4); padding: var(--s3); background: rgba(0,200,120,0.10); border: 1px solid rgba(0,200,120,0.35); border-radius: var(--r-md); font-size: var(--fs-sm); }
.mini-date { background: var(--bg-card); border: 1px solid var(--border-soft); border-radius: var(--r-sm); color: var(--text-main); padding: var(--s1) var(--s2); font-size: var(--fs-xs); color-scheme: light; }
/* 2026-08-17: 设置期限面板日期框放大(主人反馈) */
.row-menu-expire .mini-date { padding: var(--s2) var(--s2); font-size: var(--fs-base); border-radius: var(--r-md); width: 100%; box-sizing: border-box; }
.row-menu-expire .pop-row { gap: var(--s2); }
.row-menu-expire .mini-btn { padding: var(--s2) var(--s3); font-size: var(--fs-sm); }
.pager { display: flex; justify-content: space-between; align-items: center; gap: var(--s3); margin-top: var(--s3); flex-wrap: wrap; }
.pager-left { display: flex; align-items: center; gap: var(--s2); }
.pager-right { display: flex; align-items: center; gap: var(--s3); }
.page-btn { background: rgba(var(--accent-rgb),0.12); border: 1px solid var(--accent); color: var(--accent-text); border-radius: var(--r-md); padding: var(--s1) var(--s4); cursor: pointer; }
.page-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.weight-table input { color: var(--gold); }
.factor-box { border: 1px solid var(--border-soft); border-radius: var(--r-md); padding: var(--s3); margin-bottom: var(--s3); }
.factor-title { display: flex; align-items: center; color: var(--gold); font-size: var(--fs-base); margin-bottom: var(--s2); }
.bucket-table input { color: var(--accent-text); }
.add-btn { background: rgba(var(--accent-rgb),0.12); border: 1px dashed var(--accent); color: var(--accent-text); border-radius: var(--r-md); padding: var(--s1) var(--s4); cursor: pointer; font-size: var(--fs-xs); }
.del-btn { background: rgba(255,80,80,0.15); border: 1px solid var(--accent); color: var(--accent-text); border-radius: var(--r-md); padding: 2px var(--s2); cursor: pointer; }

/* 浅色主题覆盖 */
body[data-bg="light"] .card-title {  color: var(--watermark);  }
body[data-bg="light"] .mini-btn.danger {  color: var(--brand-deep); background: rgba(255,80,80,0.12); border-color: rgba(220,50,50,0.5);  }
body[data-bg="light"] .mini-btn.danger:hover {  background: var(--brand-deep); color: white;  }
body[data-bg="light"] .del-btn {  color: var(--brand-deep); background: rgba(255,80,80,0.1); border-color: rgba(220,50,50,0.5);  }
body[data-bg="light"] .del-btn:hover {  background: var(--brand-deep); color: white;  }
body[data-bg="light"] .pwd-pop {  background: rgba(255,255,255,0.98); border-color: var(--border-soft);  }
body[data-bg="light"] .pwd-mask {  background: rgba(0,0,0,0.45);  }
body[data-bg="light"] .pwd-btn {  color: #8a5500; background: rgba(255,180,0,0.15); border-color: var(--gold-deep);  }
body[data-bg="light"] .pwd-title {  color: var(--watermark);  }
body[data-bg="light"] .weight-table input {  color: #1a1d26; background: rgba(255,255,255,0.95);  }
body[data-bg="light"] .bucket-table input {  color: #1a1d26; background: rgba(255,255,255,0.95);  }
body[data-bg="light"] .factor-title {  color: #8a5500;  }
body[data-bg="light"] .factor-tab {  color: var(--text-faint); border-color: var(--border-soft);  }
body[data-bg="light"] .factor-tab:hover {  color: var(--watermark); border-color: var(--gold-deep);  }
body[data-bg="light"] .factor-tab.active {  color: var(--watermark); border-color: var(--gold-deep); background: rgba(255,180,0,0.15);  }
body[data-bg="light"] .page-btn {  color: var(--brand-deep); background: rgba(184,48,16,0.12); border-color: var(--brand-deep);  }
body[data-bg="light"] .mini-date {  color: #1a1d26; background: rgba(255,255,255,0.95); border-color: var(--border-soft);  }
body[data-bg="light"] .mini-btn {  color: #1a1d26; background: rgba(240,245,250,0.9); border-color: var(--border-soft);  }
body[data-bg="light"] .admin-tag {  color: #8a5500; border-color: var(--gold-deep);  }
body[data-bg="light"] .expired-tag {  color: var(--brand-deep); border-color: var(--brand-deep);  }
body[data-bg="light"] .level-2 { color: #8a5500; border-color: var(--gold-deep); }
body[data-bg="light"] .level-1 { color: #c03030; border-color: #e06060; }
body[data-bg="light"] .ok-tag {  color: #2d7020; border-color: #4caf70;  }
body[data-bg="light"] .weight-table input { color: #1a1d26; background: rgba(255,255,255,0.95); }
body[data-bg="light"] .bucket-table input { color: #1a1d26; background: rgba(255,255,255,0.95); }
body[data-bg="light"] .factor-title { color: #8a5500; }
body[data-bg="light"] .expire-popover { background: rgba(255,255,255,0.98); border-color: var(--border-soft); }
body[data-bg="light"] .pwd-pop { background: rgba(255,255,255,0.98); border-color: var(--border-soft); }
body[data-bg="light"] .field-label { color: var(--watermark); }
body[data-bg="light"] .admin-tip { color: var(--text-faint); }
body[data-bg="light"] .admin-save-btn { background: rgba(34,139,34,0.1); border: 1px solid #228722; color: #1a6b1a; }
body[data-bg="light"] .admin-save-btn:hover { background: #228722; color: #fff; }
/* 浅色主题: 「保存并强制生效」改用浅红底 + 深红字(对应「保存默认值」浅绿底 + 深绿字) */
body[data-bg="light"] .save-force-btn { background: rgba(163,45,45,0.12); border-color: #A32D2D; color: #A32D2D; }
body[data-bg="light"] .save-force-btn:hover:not(:disabled) { background: #A32D2D; color: #fff; }
body[data-bg="light"] .admin-msg-ok { color: #1a6b1a; }
body[data-bg="light"] .weight-desc { color: var(--text-faint); }
body[data-bg="light"] .weight-total { color: #8a5500; }
body[data-bg="light"] .weight-warn { color: var(--brand-deep); }

/* ===================== 移动端适配 (<=768px) ===================== */
@media (max-width: 768px) {
  /* 页面留白压缩 */
  .admin-wrap { padding: var(--s2) var(--s1); }
  .admin-card { padding: var(--s2) var(--s2); margin-bottom: var(--s2); }
  /* 头部紧凑: 返回按钮/标题 */
  .admin-head { gap: var(--s2); }
  .admin-head h2 { font-size: var(--fs-lg); }
  /* 宽表格横向滚动(用户列表 11 列 / 权重表 / 打分明细表) */
  .table-scroll { -webkit-overflow-scrolling: touch; }
  .table-scroll .admin-table { min-width: 860px; }
  .weight-table { min-width: 640px; }
  .bucket-table { min-width: 560px; }
  /* 表格字号压缩 */
  .admin-table th, .admin-table td { padding: var(--s2) var(--s2); font-size: var(--fs-xs); }
  /* 输入框触控加大(手机点不中小输入框) */
  .admin-input { min-height: 32px; padding: var(--s2) var(--s2); font-size: var(--fs-sm); }
  /* 因子 Tab 紧凑 */
  .factor-tab { padding: var(--s1) var(--s2); font-size: var(--fs-xs); }
  /* 搜索栏/操作行换行 */
  .admin-card > div[style*="flex"] { flex-wrap: wrap; }
  /* 保存按钮触控加大 */
  .admin-save-btn { padding: var(--s2) var(--s4); }
}
</style>

<style scoped>
/* ===== 第二批(2026-10-06): A4 标签云 + A10 转化漏斗 ===== */
/* 说明单独放在第二个 style 块: 这批样式与上面的历史样式没有层叠关系,
   拆开后将来要撤走这一批, 直接删这个块即可, 不用在上面 1000 行里找。 */
.tag-cloud { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0; }
.tag-chip {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 4px 10px; border-radius: var(--r-lg);
  border: 1px solid var(--border-soft); background: var(--bg-panel-solid);
  color: var(--text-1); cursor: pointer; font-size: var(--fs-sm); line-height: 1.4;
}
.tag-chip:hover { border-color: var(--accent-solid); }
.tag-chip.on { background: var(--accent-solid); color: var(--on-accent); border-color: var(--accent-solid); }
.tag-n { font-size: var(--fs-xs); opacity: .75; }

.funnel-row { display: flex; align-items: center; gap: 8px; margin: 5px 0; }
.funnel-label { width: 104px; flex: none; font-size: var(--fs-sm); color: var(--text-2); }
.funnel-bar-wrap {
  flex: 1; height: 14px; min-width: 60px;
  background: rgba(128, 128, 128, .18); border-radius: var(--r-lg); overflow: hidden;
}
.funnel-bar { display: block; height: 100%; background: var(--accent-solid); }
.funnel-num { width: 132px; flex: none; font-size: var(--fs-sm); text-align: right; }
@media (max-width: 768px) {
  .funnel-label { width: 84px; }
  .funnel-num { width: 108px; }
}

/* 用户列表里的标签(比标签云的 chip 更小, 表格里要省地方) */
.tag-cell { white-space: normal; }
.tag-chip-mini {
  display: inline-block; margin: 1px 3px 1px 0; padding: 1px 6px;
  border-radius: var(--r-lg); font-size: var(--fs-xs);
  border: 1px solid var(--border-soft); background: var(--bg-panel-solid); color: var(--text-2);
}
.tag-chip-mini.manual { border-color: var(--accent-solid); color: var(--accent-text); }
.tag-edit { margin-left: 4px; }
</style>
