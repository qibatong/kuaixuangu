<template>
  <div class="ma-wrap">
    <!-- 子 tab -->
    <div class="ma-tabs">
      <button v-for="t in TABS" :key="t.key" :class="['ma-tab', { on: tab === t.key }]" @click="switchTab(t.key)">
        <i class="fa" :class="t.icon"></i> {{ t.label }}
      </button>
    </div>

    <!-- ============ 运营看板 ============ -->
    <template v-if="tab === 'board'">
      <div v-if="loading.board" class="ma-loading"><div class="spinner"></div></div>
      <template v-else>
        <div class="ma-grid">
          <div v-for="c in boardCards" :key="c.label" class="ma-stat" :class="c.tone">
            <div class="ma-stat-num">{{ c.value }}</div>
            <div class="ma-stat-label">{{ c.label }}</div>
          </div>
        </div>
        <div class="ma-card">
          <div class="ma-card-title"><i class="fa fa-line-chart"></i> 近 14 天注册趋势</div>
          <div class="ma-trend">
            <div v-for="d in trend" :key="d.date" class="ma-trend-col" :title="d.date + '：' + d.count + ' 人'">
              <div class="ma-trend-bar" :style="{ height: barH(d.count) + 'px' }" :class="{ zero: !d.count }"></div>
              <div class="ma-trend-num">{{ d.count }}</div>
              <div class="ma-trend-day">{{ d.date.slice(5) }}</div>
            </div>
          </div>
        </div>
        <div v-if="quotaStat" class="ma-card">
          <div class="ma-card-title"><i class="fa fa-tachometer"></i> 今日配额使用</div>
          <div class="ma-kv">
            <span class="ma-chip">今日用量 <b>{{ quotaStat.usage_total ?? 0 }}</b> 次</span>
            <span class="ma-chip">用过的人 <b>{{ quotaStat.usage_users ?? 0 }}</b> 人</span>
            <span class="ma-chip">今日签到 <b>{{ quotaStat.checkin_today ?? 0 }}</b> 人</span>
            <span class="ma-chip">签到送出 <b>{{ quotaStat.bonus_granted_today ?? 0 }}</b> 次</span>
          </div>
          <div class="ma-hint">
            免费用户每日基础额度：选股 {{ quotaLimits.picker ?? 0 }} 次 · AI 预测 {{ quotaLimits.aipick ?? 0 }} 次 ·
            竞价异动 {{ quotaLimits.auction ?? 0 }} 次；签到每天额外送 {{ quotaStat.checkin_bonus_per_day ?? 0 }} 次选股额度；
            <b>会员与管理员不计数</b>，因此本页只反映免费/试用用户。
          </div>
          <table v-if="quotaTop.length" class="ma-table compact">
            <thead><tr><th>#</th><th>用户名</th><th>功能</th><th>用量</th></tr></thead>
            <tbody>
              <tr v-for="(r, i) in quotaTop" :key="i">
                <td>{{ i + 1 }}</td>
                <td>{{ r.username || ('uid=' + r.uid) }}</td>
                <td>{{ featureLabel(r.feature) }}</td>
                <td><b>{{ r.used }}</b> 次</td>
              </tr>
            </tbody>
          </table>
          <div v-else class="ma-empty">今日还没有免费用户消耗配额</div>
        </div>
        <!-- 2026-09-22 v4.11.35: 今日登录(此前后台看不到任何登录记录) -->
        <div v-if="loginStat" class="ma-card">
          <div class="ma-card-title"><i class="fa fa-sign-in"></i> 今日登录（{{ loginStat.date }}）</div>
          <div class="ma-kv">
            <span class="ma-chip">登录成功 <b>{{ loginStat.success_users ?? 0 }}</b> 人 / <b>{{ loginStat.success ?? 0 }}</b> 次</span>
            <span class="ma-chip">登录失败 <b>{{ loginStat.fail ?? 0 }}</b> 次</span>
            <span class="ma-chip">被顶出 <b>{{ resultCount('kicked') }}</b> 次</span>
            <span class="ma-chip">主动退出 <b>{{ resultCount('logout') }}</b> 次</span>
            <span class="ma-chip">重置密码 <b>{{ resultCount('reset') }}</b> 次</span>
          </div>
          <table v-if="loginRecent.length" class="ma-table compact">
            <thead><tr><th>时间</th><th>账号</th><th>结果</th><th>IP</th></tr></thead>
            <tbody>
              <tr v-for="(l, i) in loginRecent" :key="i">
                <td>{{ l.time }}</td>
                <td>{{ l.username }}</td>
                <td>{{ l.result_label }}</td>
                <td>{{ l.ip || '-' }}</td>
              </tr>
            </tbody>
          </table>
          <div v-else class="ma-empty">今日还没有登录记录</div>
          <div class="ma-hint">
            登录记录已从系统日志回溯至 2026-08-22（回溯条目无 UA 一项）；
            再往前取决于服务器日志保留期。
            「登录失败」在用户不存在时账号栏为空（只留尝试的账号名）。
          </div>
        </div>
        <!-- 2026-09-22 v4.11.35: 功能使用(会员/管理员也一样计数; 可按日期+功能查历史) -->
        <div v-if="usageData" class="ma-card">
          <div class="ma-card-title"><i class="fa fa-hand-pointer-o"></i> 功能使用（按人 Top）</div>
          <div class="ma-filter">
            <label>日期 <input v-model="usageDate" type="date" @change="loadUsage" /></label>
            <label>功能
              <select v-model="usageFeature" @change="loadUsage">
                <option value="">全部</option>
                <option v-for="(n, f) in ACT_LABEL" :key="f" :value="f">{{ n }}</option>
              </select>
            </label>
            <span class="ma-filter-note">统计日：{{ usageData.date }}</span>
          </div>
          <div class="ma-kv">
            <span class="ma-chip">操作总次数 <b>{{ usageData.total ?? 0 }}</b></span>
            <span class="ma-chip">使用人数 <b>{{ usageData.users ?? 0 }}</b> 人</span>
            <span v-for="(n, f) in usageData.by_feature" :key="f" class="ma-chip">{{ actLabel(f) }} <b>{{ n }}</b></span>
          </div>
          <table v-if="usageRows.length" class="ma-table compact">
            <thead><tr><th>#</th><th>用户名</th><th>身份</th><th>次数</th><th>拦截</th></tr></thead>
            <tbody>
              <tr v-for="(r, i) in usageRows" :key="i">
                <td>{{ i + 1 }}</td>
                <td>{{ r.username }}</td>
                <td>{{ whoLabel(r) }}</td>
                <td><b>{{ r.count }}</b></td>
                <td>{{ r.blocked || 0 }}</td>
              </tr>
            </tbody>
          </table>
          <div v-else class="ma-empty">该日还没有功能使用记录</div>
          <div class="ma-hint">
            口径：<b>用户主动操作一次记 1 次</b>（如选股点一次「应用」；30 秒自动轮询不计入）。
            <b>会员与管理员同样计数</b> —— 这与上方「今日配额使用」卡不同，那张卡只含免费/试用用户。
            可切日期查历史：使用记录自 <b>2026-09-22 功能上线当天</b>开始——更早的历史未回溯
            （系统日志只能数接口请求次数，与这里「点一次记一次」口径不同，混进来会被误读）。
          </div>
        </div>
        <!-- 2026-09-22 v4.11.35: 近 7 天活跃趋势(柱=操作次数, 数字=当日去重人数) -->
        <div v-if="activeTrend.length" class="ma-card">
          <div class="ma-card-title"><i class="fa fa-users"></i> 近 7 天活跃（操作次数 / 去重人数）</div>
          <div class="ma-trend">
            <div
v-for="d in activeTrend" :key="d.date" class="ma-trend-col"
                 :title="d.date + '：' + d.actions + ' 次操作 / ' + d.act_users + ' 人'"
>
              <div class="ma-trend-bar" :style="{ height: actBarH(d.actions) + 'px' }" :class="{ zero: !d.actions }"></div>
              <div class="ma-trend-num">{{ d.act_users }}</div>
              <div class="ma-trend-day">{{ d.date.slice(5) }}</div>
            </div>
          </div>
          <div class="ma-hint">
            柱高 = 当日操作总次数；柱下数字 = 当日活跃人数（同一人多次操作只算 1 人）。
            近 7 天合计 <b>{{ sum7.actions ?? 0 }}</b> 次操作、日均活跃 <b>{{ sum7.act_users_avg ?? 0 }}</b> 人；
            登录成功合计 <b>{{ sum7.logins ?? 0 }}</b> 次。
          </div>
        </div>
        <button class="ma-btn ghost" @click="loadBoard"><i class="fa fa-refresh"></i> 刷新看板</button>
      </template>
    </template>

    <!-- ============ 到期预警 ============ -->
    <template v-else-if="tab === 'expiring'">
      <div class="ma-bar">
        <div class="ma-seg">
          <button v-for="t in EXP_TABS" :key="t.key" :class="['ma-seg-btn', { on: expTab === t.key }]" @click="expTab = t.key; loadExpiring()">{{ t.label }}</button>
        </div>
        <label class="ma-inline">窗口
          <select v-model.number="expDays" class="ma-select" @change="loadExpiring">
            <option :value="3">3 天</option>
            <option :value="7">7 天</option>
            <option :value="15">15 天</option>
            <option :value="30">30 天</option>
          </select>
        </label>
        <span class="ma-count">共 {{ expRows.length }} 条</span>
        <span class="ma-spacer"></span>
        <button class="ma-btn" :disabled="!selectedExp.length" @click="batchExtend(30, '续期 30 天')">
          <i class="fa fa-plus-circle"></i> 选中 +30 天（{{ selectedExp.length }}）
        </button>
      </div>
      <div v-if="loading.exp" class="ma-loading"><div class="spinner"></div></div>
      <div v-else-if="!expRows.length" class="ma-empty">该范围内没有到期账号</div>
      <table v-else class="ma-table">
        <thead>
          <tr>
            <th class="ck"><input type="checkbox" :checked="allExpChecked" @change="toggleAllExp($event.target.checked)"></th>
            <th>用户名</th><th>手机号</th><th>微信名</th><th>等级</th>
            <th>到期日</th><th>剩余</th><th>邀请人</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in expRows" :key="r.id" :class="{ danger: r.expired }">
            <td class="ck"><input v-model="selectedExp" type="checkbox" :value="r.id"></td>
            <td>{{ r.username }}</td>
            <td class="mono">{{ r.phone || '-' }}</td>
            <td>{{ r.wx_name || '-' }}</td>
            <td>{{ levelLabel(r.member_level) }}</td>
            <td class="mono">{{ r.expire_date }}</td>
            <td :class="r.expired ? 'neg' : ''">{{ r.expired ? '已过期' : r.days_left + ' 天' }}</td>
            <td>{{ r.inviter_name || '-' }}</td>
            <td class="ops">
              <button class="ma-mini" @click="extendOne(r.id, 30)">+30天</button>
              <button class="ma-mini" @click="extendOne(r.id, 365)">+1年</button>
              <button class="ma-mini" @click="$emit('open-detail', r.id)">详情</button>
            </td>
          </tr>
        </tbody>
      </table>
    </template>

    <!-- ============ 风控 ============ -->
    <template v-else-if="tab === 'risk'">
      <div v-if="loading.risk" class="ma-loading"><div class="spinner"></div></div>
      <template v-else>
        <div class="ma-card">
          <div class="ma-card-title"><i class="fa fa-map-marker"></i> 同 IP 多账号注册（≥2）</div>
          <table v-if="risk.ip_register.length" class="ma-table compact">
            <thead><tr><th>IP</th><th>账号数</th><th>首次</th><th>账号</th></tr></thead>
            <tbody>
              <tr v-for="(r, i) in risk.ip_register" :key="i">
                <td class="mono">{{ r.ip }}</td>
                <td class="neg"><b>{{ r.n }}</b></td>
                <td class="mono">{{ r.first_date }}</td>
                <td class="names">{{ r.names }}</td>
              </tr>
            </tbody>
          </table>
          <div v-else class="ma-empty">正常：无同 IP 多账号</div>
        </div>

        <div class="ma-card">
          <div class="ma-card-title"><i class="fa fa-exchange"></i> 邀请人与被邀请人同 IP（刷邀请嫌疑）</div>
          <table v-if="risk.same_ip_invite.length" class="ma-table compact">
            <thead><tr><th>邀请人</th><th>同 IP 被邀请数</th><th>IP</th></tr></thead>
            <tbody>
              <tr v-for="(r, i) in risk.same_ip_invite" :key="i">
                <td>{{ r.inviter }}</td><td class="neg"><b>{{ r.n }}</b></td><td class="mono">{{ r.ip }}</td>
              </tr>
            </tbody>
          </table>
          <div v-else class="ma-empty">正常：无同 IP 邀请</div>
        </div>

        <div class="ma-card">
          <div class="ma-card-title"><i class="fa fa-phone-square"></i> 手机号重复领取（删号重注册嫌疑）</div>
          <table v-if="risk.dup_claims.length" class="ma-table compact">
            <thead><tr><th>手机号</th><th>领取次数</th><th>首次 UID</th><th>最近 IP</th><th>首次时间</th></tr></thead>
            <tbody>
              <tr v-for="(r, i) in risk.dup_claims" :key="i">
                <td class="mono">{{ r.phone }}</td><td class="neg"><b>{{ r.claim_count }}</b></td>
                <td>{{ r.first_uid }}</td><td class="mono">{{ r.last_ip || '-' }}</td>
                <td class="mono">{{ fmtTs(r.first_claim) }}</td>
              </tr>
            </tbody>
          </table>
          <div v-else class="ma-empty">正常：无重复领取</div>
        </div>

        <div class="ma-card">
          <div class="ma-card-title"><i class="fa fa-trophy"></i> 邀请榜</div>
          <table v-if="risk.top_inviters.length" class="ma-table compact">
            <thead><tr><th>用户名</th><th>邀请数</th><th>等级</th></tr></thead>
            <tbody>
              <tr v-for="(r, i) in risk.top_inviters" :key="i">
                <td>{{ r.username }}</td><td><b>{{ r.n }}</b></td><td>{{ levelLabel(r.member_level) }}</td>
              </tr>
            </tbody>
          </table>
          <div v-else class="ma-empty">暂无邀请记录</div>
        </div>

        <div class="ma-card">
          <div class="ma-card-title"><i class="fa fa-clock-o"></i> 最近手机号领取（50 条）</div>
          <table v-if="risk.recent_claims.length" class="ma-table compact">
            <thead><tr><th>手机号</th><th>账号</th><th>UID</th><th>时间</th><th>IP</th></tr></thead>
            <tbody>
              <tr v-for="(r, i) in risk.recent_claims" :key="i">
                <td class="mono">{{ r.phone }}</td><td>{{ r.username || '已注销' }}</td>
                <td>{{ r.first_uid }}</td><td class="mono">{{ r.first_date }}</td>
                <td class="mono">{{ r.last_ip || '-' }}</td>
              </tr>
            </tbody>
          </table>
          <div v-else class="ma-empty">暂无领取记录（存量用户早于本功能上线）</div>
        </div>
        <button class="ma-btn ghost" @click="loadRisk"><i class="fa fa-refresh"></i> 刷新风控</button>
      </template>
    </template>

    <!-- ============ 邀请榜 ============ -->
    <template v-else-if="tab === 'rank'">
      <div class="ma-bar">
        <span class="ma-count">每邀请 1 人，邀请人获 {{ rankRewardDays }} 天会员</span>
        <span class="ma-spacer"></span>
        <button class="ma-btn ghost" @click="loadRank"><i class="fa fa-refresh"></i> 刷新</button>
      </div>
      <div v-if="loading.rank" class="ma-loading"><div class="spinner"></div></div>
      <div v-else-if="!rankRows.length" class="ma-empty">暂无邀请记录</div>
      <table v-else class="ma-table">
        <thead><tr><th>#</th><th>用户名</th><th>手机号</th><th>等级</th><th>邀请人数</th><th>在期被邀请人</th><th>累计获得</th></tr></thead>
        <tbody>
          <tr v-for="(r, i) in rankRows" :key="r.id">
            <td>{{ i + 1 }}</td>
            <td>{{ r.username }}</td>
            <td class="mono">{{ r.phone || '-' }}</td>
            <td>{{ levelLabel(r.member_level) }}</td>
            <td><b>{{ r.n }}</b></td>
            <td>{{ r.active_n }}</td>
            <td class="pos">+{{ r.earned_days }} 天</td>
          </tr>
        </tbody>
      </table>
    </template>

    <!-- ============ 审计日志 ============ -->
    <template v-else-if="tab === 'audit'">
      <div class="ma-bar">
        <label class="ma-inline">动作
          <select v-model="auditAction" class="ma-select" @change="loadAudit(1)">
            <option value="">全部</option>
            <option v-for="a in auditActions" :key="a.action" :value="a.action">{{ a.action }}（{{ a.n }}）</option>
          </select>
        </label>
        <span class="ma-count">共 {{ auditTotal }} 条</span>
        <span class="ma-spacer"></span>
        <button class="ma-btn ghost" :disabled="auditPage <= 1" @click="loadAudit(auditPage - 1)">上一页</button>
        <span class="ma-count">{{ auditPage }} / {{ auditPages }}</span>
        <button class="ma-btn ghost" :disabled="auditPage >= auditPages" @click="loadAudit(auditPage + 1)">下一页</button>
      </div>
      <div v-if="loading.audit" class="ma-loading"><div class="spinner"></div></div>
      <div v-else-if="!auditRows.length" class="ma-empty">暂无审计记录</div>
      <table v-else class="ma-table">
        <thead><tr><th>时间</th><th>管理员</th><th>动作</th><th>对象</th><th>详情</th><th>IP</th></tr></thead>
        <tbody>
          <tr v-for="(r, i) in auditRows" :key="i">
            <td class="mono">{{ fmtTs(r.created_at) }}</td>
            <td>{{ r.admin_name || r.admin_uid }}</td>
            <td><span class="ma-tag">{{ r.action }}</span></td>
            <td>{{ r.target_name || (r.target_uid || '-') }}</td>
            <td class="detail">{{ fmtDetail(r.detail) }}</td>
            <td class="mono">{{ r.ip || '-' }}</td>
          </tr>
        </tbody>
      </table>
    </template>

    <!-- ============ 权益配置 ============ -->
    <template v-else-if="tab === 'conf'">
      <div v-if="loading.conf" class="ma-loading"><div class="spinner"></div></div>
      <template v-else>
        <div class="ma-card">
          <div class="ma-card-title"><i class="fa fa-sliders"></i> 会员权益配置（保存后立即生效，无需重启）</div>
          <div class="ma-conf-grid">
            <label v-for="(meta, k) in confMeta" :key="k" class="ma-conf-item">
              <span class="ma-conf-label">{{ meta.label }}</span>
              <template v-if="meta.type === 'bool'">
                <select v-model="conf[k]" class="ma-select">
                  <option :value="true">开启</option>
                  <option :value="false">关闭</option>
                </select>
              </template>
              <input v-else v-model.number="conf[k]" type="number" class="ma-input" min="0" max="3650">
              <span class="ma-conf-key">{{ k }}</span>
            </label>
          </div>
          <div class="ma-actions">
            <button class="ma-btn" :disabled="saving.conf" @click="saveConf">
              <i class="fa fa-save"></i> {{ saving.conf ? '保存中...' : '保存并生效' }}
            </button>
            <button class="ma-btn ghost" @click="loadConf"><i class="fa fa-undo"></i> 放弃修改</button>
          </div>
          <div class="ma-hint">
            <i class="fa fa-info-circle"></i>
            关闭「开放注册」后，注册页与导航栏注册入口会立即隐藏（已登录用户不受影响）。
          </div>
        </div>
      </template>
    </template>

    <!-- ============ 导入导出 ============ -->
    <template v-else-if="tab === 'io'">
      <div class="ma-card">
        <div class="ma-card-title"><i class="fa fa-download"></i> 导出用户 CSV</div>
        <div class="ma-hint">导出当前筛选条件下的全部用户（14 列，含到期日 / 注册 IP / 邀请码 / 已邀请人数）。文件带 BOM，Excel 直接打开不乱码。</div>
        <div class="ma-actions">
          <button class="ma-btn" @click="doExport"><i class="fa fa-file-excel-o"></i> 导出 CSV</button>
        </div>
      </div>
      <div class="ma-card">
        <div class="ma-card-title"><i class="fa fa-upload"></i> 批量导入会员</div>
        <div class="ma-hint">
          每行一条，格式：<code>用户名,手机号,天数[,邀请码]</code>；天数 <b>0 = 永久</b>；空行与 <code>#</code> 开头忽略；用户名或手机号已存在则跳过。
        </div>
        <textarea
v-model="importText" class="ma-textarea" rows="10"
                  placeholder="张三,13800138000,365&#10;李四,13900139000,0&#10;王五,,90,ABC12345"
></textarea>
        <label class="ma-inline">默认密码
          <input v-model="importPwd" class="ma-input" style="width:150px" placeholder="Kx123456">
        </label>
        <div class="ma-actions">
          <button class="ma-btn" :disabled="saving.import" @click="doImport">
            <i class="fa fa-cloud-upload"></i> {{ saving.import ? '导入中...' : '开始导入' }}
          </button>
          <button class="ma-btn ghost" @click="importText = ''">清空</button>
        </div>
        <div v-if="importResult" class="ma-import-result">
          <div class="ma-ir-line ok">✅ {{ importResult.msg }}</div>
          <div v-if="importResult.failed && importResult.failed.length" class="ma-ir-line bad">
            失败明细：
            <div v-for="f in importResult.failed.slice(0, 20)" :key="f.line" class="ma-ir-item">
              第 {{ f.line }} 行 · {{ f.name || f.raw }} · {{ f.msg }}
            </div>
          </div>
          <div v-if="importResult.skipped && importResult.skipped.length" class="ma-ir-line warn">
            跳过明细：
            <div v-for="f in importResult.skipped.slice(0, 20)" :key="f.line" class="ma-ir-item">
              第 {{ f.line }} 行 · {{ f.name }} · {{ f.msg }}
            </div>
          </div>
        </div>
      </div>
      <div class="ma-card">
        <div class="ma-card-title"><i class="fa fa-mobile"></i> 短信用量</div>
        <div v-if="loading.sms" class="ma-loading"><div class="spinner"></div></div>
        <template v-else>
          <div class="ma-kv">
            <span v-for="(n, sc) in (sms.scenes || {})" :key="sc" class="ma-chip">{{ sc }} <b>{{ n }}</b></span>
            <span class="ma-chip">手机号限流键 <b>{{ sms.phone_limited }}</b></span>
            <span class="ma-chip">IP 限流键 <b>{{ sms.ip_limited }}</b></span>
          </div>
          <div class="ma-hint">{{ sms.note }}</div>
        </template>
      </div>
      <div class="ma-card">
        <div class="ma-card-title"><i class="fa fa-bolt"></i> 一键续期（批量 UID）</div>
        <div class="ma-hint">输入 UID（逗号分隔），批量延长到期时间，可选一并设置等级。</div>
        <input v-model="plusUids" class="ma-input wide" placeholder="12,34,56">
        <div class="ma-inline-row">
          <label class="ma-inline">加天数 <input v-model.number="plusDays" type="number" class="ma-input" style="width:100px"></label>
          <label class="ma-inline">设等级
            <select v-model="plusLevel" class="ma-select">
              <option value="">不修改</option>
              <option :value="0">0 免费试用</option>
              <option :value="1">1 付费会员</option>
              <option :value="2">2 VIP 老师</option>
            </select>
          </label>
          <button class="ma-btn" :disabled="saving.plus" @click="doExtendPlus">
            <i class="fa fa-flash"></i> 执行
          </button>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import {
  adminDashboard, adminExpiring, adminRisk, adminInviteRank, adminAudit, adminAuditActions,
  adminMemberConf, saveAdminMemberConf, adminImportUsers, adminExtendPlus, adminSmsUsage,
  adminLoginLog, adminUsageRank, adminActiveUsers,
} from '../api/admin'
import { showToast } from '../utils/toast'
import { logFront } from '../utils/logger'

defineEmits(['open-detail'])   // 仅声明事件; 模板内用 $emit 触发, 无需 script 引用

const TABS = [
  { key: 'board', label: '运营看板', icon: 'fa-dashboard' },
  { key: 'expiring', label: '到期预警', icon: 'fa-bell' },
  { key: 'risk', label: '风控', icon: 'fa-shield' },
  { key: 'rank', label: '邀请榜', icon: 'fa-trophy' },
  { key: 'audit', label: '审计日志', icon: 'fa-list-alt' },
  { key: 'conf', label: '权益配置', icon: 'fa-sliders' },
  { key: 'io', label: '导入导出', icon: 'fa-exchange' },
]
const EXP_TABS = [
  { key: 'expiring', label: '即将到期' },
  { key: 'expired', label: '已过期' },
  { key: 'all', label: '全部' },
]
const LEVELS = { 0: '免费试用', 1: '付费会员', 2: 'VIP老师' }
const FEATURE = { picker: '选股', aipick: 'AI 预测', auction: '竞价异动' }

const tab = ref('board')
const loading = reactive({ board: false, exp: false, risk: false, rank: false, audit: false, conf: false, sms: false })
const saving = reactive({ conf: false, import: false, plus: false })

/* 看板 */
const stats = ref({})
const trend = ref([])
const quotaStat = ref(null)
const boardCards = computed(() => {
  const s = stats.value || {}
  return [
    { label: '总用户', value: s.total ?? 0, tone: '' },
    { label: '付费会员', value: s.paid ?? 0, tone: 'pos' },
    { label: 'VIP 老师', value: s.vip ?? 0, tone: 'gold' },
    { label: '永久账号', value: s.permanent ?? 0, tone: '' },
    { label: '已过期', value: s.expired ?? 0, tone: 'neg' },
    { label: '今日新增', value: s.new_today ?? 0, tone: 'pos' },
    { label: '7 日新增', value: s.new_7d ?? 0, tone: '' },
    { label: '30 日新增', value: s.new_30d ?? 0, tone: '' },
    { label: '7 日活跃', value: s.active_7d ?? 0, tone: '' },
    { label: '被邀请注册', value: s.invited ?? 0, tone: '' },
    { label: '3 日内到期', value: s.expiring_3d ?? 0, tone: 'neg' },
    { label: '7 日内到期', value: s.expiring_7d ?? 0, tone: 'warn' },
    { label: '手机号台账', value: s.phone_claims ?? 0, tone: '' },
    { label: '7 日新领取', value: s.phone_claims_7d ?? 0, tone: '' },
    { label: '今日签到', value: s.checkin_today ?? 0, tone: 'pos' },
    { label: '管理员', value: s.admins ?? 0, tone: '' },
  ]
})
const trendMax = computed(() => Math.max(1, ...trend.value.map((d) => d.count || 0)))
function barH(n) { return Math.max(2, Math.round((n / trendMax.value) * 90)) }
function featureLabel(k) { return FEATURE[k] || k }
const quotaLimits = computed(() => (quotaStat.value && quotaStat.value.limits) || {})
const quotaTop = computed(() => (quotaStat.value && quotaStat.value.usage_top) || [])

/* 行为记录卡(2026-09-22 v4.11.35): 登录概况 + 功能使用 + 近 7 天活跃
   数据来源 = dashboard 的 activity.login(登录概况) + login-log(最近 8 条流水)
              + usage-rank(按日期/功能查, 可回溯历史) + active-users(近 7 天趋势) */
const loginStat = ref(null)
const loginRecent = ref([])
const ACT_LABEL = { picker: '选股', aipick: 'AI 选股', auction: '竞价异动', concept: '题材异动',
                    history: '历史回看', ladder: '连板天梯', market: '市场雷达', member: '会员中心' }
function actLabel(k) { return ACT_LABEL[k] || k }
function resultCount(k) {
  const by = (loginStat.value && loginStat.value.by_result) || {}
  return (by[k] && by[k].count) || 0
}
function whoLabel(r) { return r.is_admin ? '管理员' : (r.member_level ? '会员' : '免费') }

// 功能使用: 支持按「日期 + 功能」查(默认今天 / 全部)
function bjToday() { return new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10) }
const usageDate = ref(bjToday())
const usageFeature = ref('')
const usageData = ref(null)
const usageRows = computed(() => (usageData.value && usageData.value.rows) || [])
async function loadUsage() {
  try {
    usageData.value = await adminUsageRank({ date: usageDate.value, feature: usageFeature.value, limit: 20 })
  } catch (e) { showToast('❌ 使用记录加载失败: ' + (e.message || ''), 'error') }
}

// 近 7 天活跃(去重人数 / 操作次数)
const activeTrend = ref([])
const sum7 = ref({})
function actBarH(n) {
  const m = Math.max(1, ...activeTrend.value.map((x) => x.actions || 0))
  return Math.max(2, Math.round(((n || 0) / m) * 70))
}
async function loadActive() {
  try {
    const d = await adminActiveUsers(7)
    activeTrend.value = d.days || []
    sum7.value = d.sum7 || {}
  } catch (e) { activeTrend.value = []; sum7.value = {} }
}

async function loadBoard() {
  loading.board = true
  try {
    const d = await adminDashboard()
    stats.value = d.stats || {}
    trend.value = d.trend || []
    quotaStat.value = d.quota || null
    const act = d.activity || null
    loginStat.value = (act && act.login) || null
    // ⚠️ 这里不要碰未声明的变量: 2026-09-22 曾误写 activityUsage(未声明),
    //    运行时 ReferenceError 被本函数外层 catch 静默吞掉, 导致下面的
    //    login-log 请求**根本没发出**(卡上永远显示"今日还没有登录记录")。
    //    功能使用卡的独立数据源是 usageData(由 loadUsage 填), 不在这里赋值。
    // 最近登录流水: 失败不影响主看板(那两张卡会显示"无记录")
    try {
      const lg = await adminLoginLog({ days: 1, limit: 8 })
      loginRecent.value = lg.rows || []
    } catch (e) { loginRecent.value = [] }
  } catch (e) {
    // 2026-09-22: 这一层曾静默吞掉 ReferenceError(未声明变量), 导致后续请求不发、
    // 界面表现为"没数据"而非"出错"。凡是走到这里都记一条前端日志, 便于定位。
    logFront('error', '看板加载失败: ' + ((e && e.stack) || (e && e.message) || e))
    showToast('❌ 看板加载失败: ' + (e.message || ''), 'error')
  } finally { loading.board = false }
}

/* 到期预警 */
const expTab = ref('expiring')
const expDays = ref(7)
const expRows = ref([])
const selectedExp = ref([])
const allExpChecked = computed(() => expRows.value.length > 0 && selectedExp.value.length === expRows.value.length)
function toggleAllExp(v) { selectedExp.value = v ? expRows.value.map((r) => r.id) : [] }
async function loadExpiring() {
  loading.exp = true
  selectedExp.value = []
  try {
    const d = await adminExpiring(expDays.value, expTab.value)
    expRows.value = d.rows || []
  } catch (e) { showToast('❌ 到期列表加载失败: ' + (e.message || ''), 'error') } finally { loading.exp = false }
}
async function extendOne(id, days) {
  try {
    const d = await adminExtendPlus([id], days)
    showToast('✅ ' + (d.msg || '已续期'), 'success')
    loadExpiring()
  } catch (e) { showToast('❌ ' + (e.message || '续期失败'), 'error') }
}
async function batchExtend(days, label) {
  if (!selectedExp.value.length) return
  if (!confirm(`确定对选中的 ${selectedExp.value.length} 个账号执行「${label}」？`)) return
  try {
    const d = await adminExtendPlus(selectedExp.value, days)
    showToast('✅ ' + (d.msg || '已完成'), 'success')
    loadExpiring()
  } catch (e) { showToast('❌ ' + (e.message || '批量续期失败'), 'error') }
}

/* 风控 */
const risk = ref({ ip_register: [], same_ip_invite: [], top_inviters: [], dup_claims: [], recent_claims: [] })
async function loadRisk() {
  loading.risk = true
  try {
    const d = await adminRisk()
    risk.value = {
      ip_register: d.ip_register || [], same_ip_invite: d.same_ip_invite || [],
      top_inviters: d.top_inviters || [], dup_claims: d.dup_claims || [],
      recent_claims: d.recent_claims || [],
    }
  } catch (e) { showToast('❌ 风控加载失败: ' + (e.message || ''), 'error') } finally { loading.risk = false }
}

/* 邀请榜 */
const rankRows = ref([])
const rankRewardDays = ref(5)
async function loadRank() {
  loading.rank = true
  try {
    const d = await adminInviteRank()
    rankRows.value = d.rows || []
    rankRewardDays.value = d.reward_days ?? 5
  } catch (e) { showToast('❌ 邀请榜加载失败: ' + (e.message || ''), 'error') } finally { loading.rank = false }
}

/* 审计 */
const auditRows = ref([])
const auditActions = ref([])
const auditAction = ref('')
const auditPage = ref(1)
const auditTotal = ref(0)
const auditPages = computed(() => Math.max(1, Math.ceil(auditTotal.value / 30)))
async function loadAudit(page = 1) {
  loading.audit = true
  auditPage.value = page
  try {
    const d = await adminAudit(page, 30, auditAction.value)
    auditRows.value = d.rows || []
    auditTotal.value = d.total || 0
  } catch (e) { showToast('❌ 审计加载失败: ' + (e.message || ''), 'error') } finally { loading.audit = false }
}
async function loadAuditActions() {
  try {
    const d = await adminAuditActions()
    auditActions.value = d.actions || []
  } catch { /* 忽略 */ }
}
function fmtDetail(d) {
  if (!d) return '-'
  if (typeof d === 'string') return d
  try {
    const s = JSON.stringify(d)
    return s.length > 90 ? s.slice(0, 90) + '…' : s
  } catch { return '-' }
}

/* 权益配置 */
const conf = ref({})
const confMeta = ref({})
async function loadConf() {
  loading.conf = true
  try {
    const d = await adminMemberConf()
    conf.value = { ...(d.conf || {}) }
    confMeta.value = d.meta || {}
  } catch (e) { showToast('❌ 配置加载失败: ' + (e.message || ''), 'error') } finally { loading.conf = false }
}
async function saveConf() {
  saving.conf = true
  try {
    const d = await saveAdminMemberConf(conf.value)
    conf.value = { ...(d.conf || conf.value) }
    showToast('✅ ' + (d.msg || '已保存'), 'success')
  } catch (e) { showToast('❌ ' + (e.message || '保存失败'), 'error') } finally { saving.conf = false }
}

/* 导入导出 */
const importText = ref('')
const importPwd = ref('')
const importResult = ref(null)
const sms = ref({})
async function doExport() {
  try {
    const SESSION_KEY = 'kuaixuan_session_v1'
    const SESSION_KEY_TMP = 'kuaixuan_session_tmp'
    const raw = localStorage.getItem(SESSION_KEY) || sessionStorage.getItem(SESSION_KEY_TMP) || 'null'
    const token = (JSON.parse(raw) || {}).token || ''
    if (!token) return showToast('❌ 登录态缺失，请重新登录', 'error')
    const resp = await fetch('/api/admin/users/export', { headers: { Authorization: 'Bearer ' + token } })
    if (!resp.ok) throw new Error('导出失败(' + resp.status + ')')
    const blob = await resp.blob()
    const cd = resp.headers.get('Content-Disposition') || ''
    const m = cd.match(/filename="?([^";]+)"?/)
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = m ? m[1] : 'kuaixuan_users.csv'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(a.href)
    showToast('✅ 已开始下载', 'success')
  } catch (e) { showToast('❌ ' + (e.message || '导出失败'), 'error') }
}
async function doImport() {
  if (!importText.value.trim()) return showToast('请先粘贴 CSV 内容', 'error')
  saving.import = true
  importResult.value = null
  try {
    const d = await adminImportUsers(importText.value, importPwd.value.trim())
    importResult.value = d
    showToast('✅ ' + (d.msg || '导入完成'), 'success')
    loadAuditActions()
  } catch (e) { showToast('❌ ' + (e.message || '导入失败'), 'error') } finally { saving.import = false }
}
async function loadSms() {
  loading.sms = true
  try { sms.value = await adminSmsUsage() } catch { sms.value = {} } finally { loading.sms = false }
}

/* 一键续期 */
const plusUids = ref('')
const plusDays = ref(30)
const plusLevel = ref('')
async function doExtendPlus() {
  const uids = plusUids.value.split(/[,，\s]+/).filter(Boolean).map((x) => parseInt(x, 10)).filter((x) => x > 0)
  if (!uids.length) return showToast('请输入有效的 UID', 'error')
  if (!confirm(`确定为 ${uids.length} 个账号 +${plusDays.value} 天？`)) return
  saving.plus = true
  try {
    const d = await adminExtendPlus(uids, plusDays.value, plusLevel.value === '' ? null : plusLevel.value)
    showToast('✅ ' + (d.msg || '已完成'), 'success')
    if (tab.value === 'expiring') loadExpiring()
  } catch (e) { showToast('❌ ' + (e.message || '执行失败'), 'error') } finally { saving.plus = false }
}

/* 工具 */
function levelLabel(l) { return LEVELS[l] || ('等级 ' + l) }
function fmtTs(ts) {
  if (!ts) return '-'
  const d = new Date(Number(ts) * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function switchTab(k) {
  tab.value = k
  if (k === 'board' && !trend.value.length) loadBoard()
  else if (k === 'expiring' && !expRows.value.length) loadExpiring()
  else if (k === 'risk' && !risk.value.ip_register.length) loadRisk()
  else if (k === 'rank' && !rankRows.value.length) loadRank()
  else if (k === 'audit' && !auditRows.value.length) { loadAuditActions(); loadAudit(1) }
  else if (k === 'conf' && !Object.keys(conf.value).length) loadConf()
  else if (k === 'io' && !sms.value.raw_count) loadSms()
}

onMounted(() => { loadBoard(); loadUsage(); loadActive() })
</script>

<style scoped>
.ma-wrap { display: flex; flex-direction: column; gap: var(--s3); }
.ma-tabs { display: flex; gap: var(--s2); flex-wrap: wrap; }
.ma-tab {
  padding: var(--s2) var(--s3); font-size: var(--fs-sm); border-radius: var(--r-md); cursor: pointer;
  border: 1px solid var(--border-soft); background: transparent; color: var(--text-muted);
}
.ma-tab.on { background: var(--accent-solid); border-color: var(--accent); color: #fff; }
.ma-tab i { margin-right: var(--s1); }

.ma-loading { padding: var(--s6); display: flex; justify-content: center; }
.ma-empty { padding: var(--s4); text-align: center; font-size: var(--fs-sm); color: var(--text-muted); }

/* 看板 */
.ma-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(112px, 1fr)); gap: var(--s2); }
.ma-stat {
  border: 1px solid var(--border-soft); border-radius: var(--r-lg); padding: var(--s3) var(--s4);
  background: var(--bg-panel);
}
.ma-stat-num { font-size: var(--fs-2xl); font-weight: 700; color: var(--text-main); line-height: 1.2; }
.ma-stat-label { font-size: var(--fs-xs); color: var(--text-muted); margin-top: var(--s1); }
.ma-stat.pos .ma-stat-num { color: var(--accent); }
.ma-stat.neg .ma-stat-num { color: #ff7875; }
.ma-stat.warn .ma-stat-num { color: var(--warn-amber); }
.ma-stat.gold .ma-stat-num { color: var(--gold-deep); }

.ma-card {
  background: var(--bg-panel); border: 1px solid var(--border-soft);
  border-radius: var(--r-lg); padding: var(--s4) var(--s4);
}
.ma-card-title { font-size: var(--fs-base); font-weight: 700; color: var(--text-main); margin-bottom: var(--s2); }
.ma-card-title i { color: var(--accent); margin-right: var(--s2); }

.ma-trend { display: flex; gap: var(--s1); align-items: flex-end; overflow-x: auto; padding-top: var(--s2); }
.ma-trend-col { display: flex; flex-direction: column; align-items: center; min-width: 32px; }
.ma-trend-bar { width: 18px; border-radius: 3px 3px 0 0; background: var(--accent-solid); transition: height .25s; }
.ma-trend-bar.zero { background: var(--border-soft); }
.ma-trend-num { font-size: var(--fs-xs); color: var(--text-secondary); margin-top: var(--s1); }
.ma-trend-day { font-size: var(--fs-xs); color: var(--text-muted); }

.ma-kv { display: flex; gap: var(--s2); flex-wrap: wrap; }
/* 2026-09-22 v4.11.35: 功能使用卡的日期/功能筛选条(手机端可换行, 不做横滑) */
.ma-filter {
  display: flex; gap: var(--s2); flex-wrap: wrap; align-items: center;
  margin: 2px 0 var(--s2); font-size: var(--fs-xs); color: var(--text-muted);
}
.ma-filter label { display: inline-flex; align-items: center; gap: var(--s1); }
.ma-filter input[type="date"], .ma-filter select {
  font-size: var(--fs-xs); padding: var(--s1) var(--s2); border-radius: var(--r-md);
  border: 1px solid var(--border-soft); background: transparent; color: var(--text);
}
.ma-filter-note { margin-left: auto; }
.ma-chip {
  font-size: var(--fs-xs); padding: var(--s1) var(--s2); border-radius: var(--r-lg);
  background: var(--bg-input); color: var(--text-secondary);
}
.ma-chip b { color: var(--accent-deep); margin-left: var(--s1); }

/* 工具条 */
.ma-bar { display: flex; align-items: center; gap: var(--s2); flex-wrap: wrap; }
.ma-spacer { flex: 1; }
.ma-count { font-size: var(--fs-xs); color: var(--text-muted); }
.ma-inline { font-size: var(--fs-xs); color: var(--text-muted); display: inline-flex; align-items: center; gap: var(--s1); }
.ma-inline-row { display: flex; align-items: center; gap: var(--s3); flex-wrap: wrap; margin-top: var(--s2); }
.ma-seg { display: inline-flex; border: 1px solid var(--border-soft); border-radius: var(--r-md); overflow: hidden; }
.ma-seg-btn {
  padding: var(--s1) var(--s3); font-size: var(--fs-xs); border: none; cursor: pointer;
  background: transparent; color: var(--text-muted);
}
.ma-seg-btn.on { background: var(--accent-solid); color: #fff; }

.ma-btn {
  padding: var(--s2) var(--s4); font-size: var(--fs-sm); border-radius: var(--r-md); cursor: pointer;
  border: 1px solid var(--accent); background: var(--accent-solid); color: #fff;
}
.ma-btn.ghost { background: transparent; color: var(--accent); }
.ma-btn:disabled { opacity: .45; cursor: not-allowed; }
.ma-mini {
  padding: var(--s1) var(--s2); font-size: var(--fs-xs); border-radius: var(--r-sm); cursor: pointer;
  border: 1px solid var(--border-soft); background: transparent; color: var(--text-secondary);
  margin-right: var(--s1);
}
.ma-mini:hover { border-color: var(--accent); color: var(--accent); }

.ma-select, .ma-input {
  padding: var(--s1) var(--s2); font-size: var(--fs-sm); border-radius: var(--r-md);
  border: 1px solid var(--border-soft); background: var(--bg-input); color: var(--text-main);
}
.ma-input.wide { width: 100%; max-width: 460px; }
.ma-textarea {
  width: 100%; box-sizing: border-box; padding: var(--s2); font-size: var(--fs-sm);
  font-family: var(--font-mono); line-height: 1.6;
  border: 1px solid var(--border-soft); border-radius: var(--r-md);
  background: var(--bg-input); color: var(--text-main); resize: vertical;
}
.ma-actions { display: flex; gap: var(--s2); margin-top: var(--s3); }
.ma-hint { margin-top: var(--s2); font-size: var(--fs-xs); color: var(--text-muted); line-height: 1.6; }
.ma-hint code { background: var(--bg-input); padding: 1px var(--s1); border-radius: var(--r-sm); }

/* 表格 */
.ma-table { width: 100%; border-collapse: collapse; font-size: var(--fs-sm); }
.ma-table.compact { font-size: var(--fs-xs); }
.ma-table th, .ma-table td {
  padding: var(--s2) var(--s2); text-align: left; border-bottom: 1px solid var(--border-soft);
  color: var(--text-secondary); vertical-align: top;
}
.ma-table th { color: var(--text-muted); font-weight: 600; white-space: nowrap; }
.ma-table tr.danger td { background: rgba(255, 106, 106, .06); }
.ma-table .ck { width: 30px; }
.ma-table .mono { font-family: var(--font-mono); font-size: var(--fs-xs); }
.ma-table .ops { white-space: nowrap; }
.ma-table .detail { max-width: 260px; word-break: break-all; font-size: var(--fs-xs); color: var(--text-muted); }
.ma-table .names { max-width: 320px; word-break: break-all; }
.ma-table .neg { color: var(--brand-soft); }
.ma-table .pos { color: var(--down); }
.ma-tag {
  font-size: var(--fs-xs); padding: 2px var(--s2); border-radius: var(--r-md);
  background: rgba(var(--accent-rgb), .12); color: var(--accent-deep);
}

/* 权益配置 */
.ma-conf-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: var(--s3); }
.ma-conf-item { display: flex; flex-direction: column; gap: var(--s1); }
.ma-conf-label { font-size: var(--fs-sm); color: var(--text-secondary); }
.ma-conf-key { font-size: var(--fs-xs); color: var(--text-muted); font-family: var(--font-mono); }

/* 导入结果 */
.ma-import-result { margin-top: var(--s3); font-size: var(--fs-xs); line-height: 1.7; }
.ma-ir-line { padding: var(--s2) 0; }
.ma-ir-line.ok { color: var(--down); }
.ma-ir-line.bad { color: var(--brand-soft); }
.ma-ir-line.warn { color: var(--warn-amber); }
.ma-ir-item { padding-left: var(--s4); color: var(--text-muted); }

body[data-bg="light"] .ma-stat { background: #fff; }
body[data-bg="light"] .ma-card { background: #fff; }
body[data-bg="light"] .ma-trend-bar.zero { background: #e3e6ea; }
</style>
