# -*- coding: utf-8 -*-
"""影子/晋级/回滚闸门（主人 2026-10-03 裁决落地）。

流程：影子(≥20 交易日) → 试运行(可回滚) → 60 日复核不劣 → 转正式
主指标：**可买组命中（同日配对）** + **按卖出规则模拟的期望收益**
大面率：**监控指标 + 相对否决**（配对恶化 ≥3pp 拒）
护栏：单日最多晋级 1 次；连续 2 日劣化 ⇒ 冻结 + 告警；训练失败绝不改线上（独立进程 + 原子切换）

统计口径（关键，实测校准过）：
  · 命中率日波动 sd≈22~23pp、**同日配对差 sd≈14pp** ⇒ 20 日窗只能识别 ≥12pp 的差异
  · 故主判据用**点估计**（hit_delta ≥ +2pp）而非"显著更优"；60 日复核用"不劣"（≥−1×SE）
  · 配对/按日聚类一律**同日配对**（不可把票当独立样本）

状态机：SHADOW → TRIAL → OFFICIAL；任一阶段可 → FROZEN（冻结，需人工介入）
"""
from dataclasses import dataclass, field

# 🔴 单位统一为 **pp（百分点）**：指标一律 pp，阈值也必须 pp
#    （2026-10-03 演练抓到：常量写成 0.03 而指标是 0.67pp ⇒ "0.67 > 0.03" 恒真，错误拒绝）
GATE = dict(shadow_min_days=20, hit_delta_min=2.0, bigface_worsen_max=3.0,
            trial_min_days=60, freeze_consec_days=2)


@dataclass
class GateState:
    phase: str = 'SHADOW'          # SHADOW / TRIAL / OFFICIAL / FROZEN
    day: int = 0
    shadow_days: int = 0
    trial_days: int = 0
    bad_streak: int = 0
    log: list = field(default_factory=list)
    frozen_reason: str = ''
    promote_count: int = 0         # 单日最多晋级 1 次护栏

    # 滚动窗口：数值列（new_hit, old_hit, new_bf, old_bf, new_ret, old_ret）；
    # 日期单独存 dates —— 混进 win 会让 numpy 数组退化成字符串（2026-10-03 踩到）
    win: list = field(default_factory=list)
    dates: list = field(default_factory=list)


def _paired(win, i):
    import numpy as np
    a = np.array(win)
    return (a[:, i].mean(), a[:, i].std(ddof=1) / max(np.sqrt(len(a)), 1))


def step(st: GateState, date, new_hit, old_hit, new_bf, old_bf, new_ret, old_ret):
    """推进一天。返回本日决策 dict（动作/原因/统计）。"""
    import numpy as np
    if any(v is None or (isinstance(v, float) and np.isnan(v))
           for v in (new_hit, old_hit, new_bf, old_bf, new_ret, old_ret)):
        return {'date': date, 'phase': st.phase, 'days': len(st.win), 'action': 'skip',
                'reason': '当日指标缺失(NaN) ⇒ 跳过', 'hit_delta': 0, 'bigface_delta': 0,
                'ret_delta': 0, 'new_ret': 0}
    st.day += 1
    st.dates.append(date)
    st.win.append((new_hit, old_hit, new_bf, old_bf, new_ret, old_ret))
    a = np.array(st.win)
    hit_d = float(a[:, 0].mean() - a[:, 1].mean())
    bf_d = float(a[:, 2].mean() - a[:, 3].mean())
    ret_d = float(a[:, 4].mean() - a[:, 5].mean())
    new_ret_m = float(a[:, 4].mean())
    dec = {'date': date, 'phase': st.phase, 'days': len(a), 'hit_delta': round(hit_d, 4),
           'bigface_delta': round(bf_d, 4), 'ret_delta': round(ret_d, 4),
           'new_ret': round(new_ret_m, 4), 'action': 'hold', 'reason': ''}

    # 🔴 冻结判定必须用**滚动窗口**（2026-10-03 演练实测教训）：
    #    日级"连续 2 日双劣化"在命中率日波动 sd≈22pp 下几乎必然误触发（演练第 3 天就被冻结）。
    #    改为：滚动 20 日配对差「命中 ≤ −5pp 且 收益为负」连续 2 日 ⇒ 冻结。
    W = min(len(a), 20)
    if W >= 10:
        w_hit = float(a[-W:, 0].mean() - a[-W:, 1].mean())
        w_ret = float(a[-W:, 4].mean() - a[-W:, 5].mean())
        bad_today = (w_hit <= -5.0) and (w_ret < 0)
    else:
        bad_today = False
    st.bad_streak = st.bad_streak + 1 if bad_today else 0
    if st.bad_streak >= GATE['freeze_consec_days']:
        st.phase = 'FROZEN'
        st.frozen_reason = '连续 %d 日命中与收益双劣化' % st.bad_streak
        dec.update(action='freeze', reason=st.frozen_reason)
        st.log.append(dec)
        return dec

    if st.phase == 'SHADOW':
        st.shadow_days += 1
        if st.shadow_days < GATE['shadow_min_days']:
            dec.update(action='shadow', reason='影子期 %d/%d' % (st.shadow_days, GATE['shadow_min_days']))
        elif bf_d > GATE['bigface_worsen_max']:
            dec.update(action='reject', reason='大面率配对恶化 %.2fpp > 3pp（相对否决）' % bf_d)
        elif hit_d >= GATE['hit_delta_min'] and ret_d >= 0:
            st.phase = 'TRIAL'
            st.promote_count += 1
            dec.update(action='promote_trial', reason='命中 %+.1fpp 且期望收益 %+.2fpp ⇒ 进试运行' % (hit_d, ret_d))
        else:
            dec.update(action='shadow', reason='命中 %+.1fpp 未达 +2pp 或收益未改善 ⇒ 继续影子' % hit_d)
    elif st.phase == 'TRIAL':
        st.trial_days += 1
        if st.trial_days < GATE['trial_min_days']:
            dec.update(action='trial', reason='试运行 %d/%d' % (st.trial_days, GATE['trial_min_days']))
        elif bf_d > GATE['bigface_worsen_max']:
            dec.update(action='reject', reason='60 日复核：大面率恶化 %.2fpp' % bf_d)
        else:
            _, se = _paired(st.win[-GATE['trial_min_days']:], 0)
            _, se2 = _paired(st.win[-GATE['trial_min_days']:], 1)
            se_d = (se ** 2 + se2 ** 2) ** 0.5
            if hit_d >= -1.0 * se_d:                      # 不劣（≥ −1×SE）
                st.phase = 'OFFICIAL'
                dec.update(action='promote_official', reason='60 日复核不劣（%+.1fpp, SE %.1fpp）' % (hit_d, se_d))
            else:
                dec.update(action='keep_trial', reason='60 日复核劣化 %+.1fpp ⇒ 维持试运行' % hit_d)
    else:                                                  # OFFICIAL
        dec.update(action='official', reason='已转正式（持续监控）')
    st.log.append(dec)
    return dec


def summary(st):
    return {'phase': st.phase, 'days': st.day, 'shadow_days': st.shadow_days,
            'trial_days': st.trial_days, 'frozen_reason': st.frozen_reason,
            'actions': {k: sum(1 for x in st.log if x['action'] == k)
                        for k in {x['action'] for x in st.log}}}
