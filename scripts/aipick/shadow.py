# -*- coding: utf-8 -*-
"""影子模型 / 晋级闸门 / 回滚（方案第 1、8、12 条落地）

定位（主人 2026-10-03）：系统是**涨停概率评分 + 排序名单**（排序器），
⇒ 晋级主判据只看**排序命中率**（逐日 topN 封板率，同日配对）；可买率/大面率仅作监控。

设计要点
  ① 版本目录 + 指针（不动加载逻辑 ⇒ 零风险）
       models/versions/v{ts}/model.txt + meta.json      ← 版本归档（可复现：特征版本/超参/指标）
       models/model_lgb.txt                             ← **线上加载路径**，晋级时原子替换（temp+rename）
       models/backup/model_lgb.{ts}.txt                  ← 替换前自动备份 ⇒ 回滚就是换回来
  ② 影子出名单：每日用候选模型出名单，写 pick_daily(line='shadow', model_ver=...)，**不推给用户**
  ③ 两段式闸门（硬约束 → 排序命中率）：
       阶段1 影子期 ≥20 交易日：逐日配对（同日 topN 封板率差）
       阶段2 试运行：累计 60 交易日复核，变差自动回滚
       防抖：晋级/回滚都需**连续 2 个评估窗同向**；单日最多晋级 1 次；连续 2 日劣化冻结+告警
  ④ 训练/推理在独立进程，失败不影响线上（候选训练失败 ⇒ 保持影子，不切换）

用法
  # 历史回放（只读；验证闸门逻辑，不写任何线上文件）
  python3 shadow.py replay --days 100 --out /tmp/kx_v3/shadow_replay.json
  # 训练候选版本（写 models/versions/，不切换线上）
  python3 shadow.py train --tag v20261003
  # 影子出名单（写 pick_daily，需 --commit）
  python3 shadow.py picks --date 20260930 --commit
  # 闸门判定（默认只打印，--commit 才落库）
  python3 shadow.py gate --asof 20260930 --commit
  # 晋级 / 回滚（原子替换 + 自动备份；需 --commit）
  python3 shadow.py promote --ver v20261003 --commit
  python3 shadow.py rollback --commit
"""
import argparse
import glob
import json
import os
import shutil
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

BASE = '/opt/kuaixuan/aipick'
MODELS = os.path.join(BASE, 'models')
VERS = os.path.join(MODELS, 'versions')
BACKUP = os.path.join(MODELS, 'backup')
AP = os.path.join(BASE, 'scripts/data/aipick.db')
AUC_DB = '/tmp/kx_v3/auc_open.db'
LIVE = os.path.join(MODELS, 'model_lgb.txt')          # 线上加载路径（ai_predict.py 读它）
# 影子候选：由 `shadow.py train` 产出（**含 9:25 竞价特征**，与线上 11 维不同），
#   经指针 POINTER['shadow'] 指向最新版本；绝不覆盖线上模型。
#   ⚠️ 不能用 models/v3/ranker_v3.txt —— train_v3.py 的特征清单只有 11 维（无 auc_*），
#      与影子推理的特征集不一致（2026-10-03 核对后修正）。
AUC_TABLE = 'auc_open'                                 # 9:25 全市场竞价特征（aipick.db）
POINTER = os.path.join(MODELS, 'current.json')
FREEZE = os.path.join(MODELS, 'shadow_freeze.json')    # 连续劣化冻结标记
WORSEN_PP = 2.0                                        # 单次判定恶化阈值(pp)

TOPN = (3, 5, 10, 30)
MIN_SHADOW_DAYS = 20          # 影子期下限（交易日）
REVIEW_DAYS = 60              # 复核窗
GATE_MIN_EFFECT = 2.0         # 主判据：同日配对 topN 命中率提升 ≥2pp（点估计）
# 🔴 弱显著护栏（2026-10-03 回放实测：20 日窗符号在 +8.3/0/+(-1.7) 间来回跳，
#    只看点估计 ⇒ 闸门在噪声里频繁开合）⇒ 追加 t ≥ 1.0 与"次档位不劣"。
GATE_MIN_T = 1.0
GATE_SECOND_TOL = 0.0         # 次档位（top5）配对差不劣于 0
GATE_MAX_WORSE = 3.0          # 复核期非劣上限：命中率恶化 ≤3pp
DECAY_CONFIRM = 2             # 防抖：连续 N 个评估窗同向才执行
S2 = dict(objective='lambdarank', metric='ndcg', lambdarank_truncation_level=30,
          label_gain=[0.0, 0.5, 1.0], learning_rate=0.05, num_leaves=7, min_data_in_leaf=50,
          feature_fraction=0.9, bagging_fraction=0.9, bagging_freq=1, seed=42,
          num_threads=4, verbose=-1)
ROUNDS = 400


# ---------------------------------------------------------------- 数据
def days_next(last_date):
    """训练末端之后的"影子窗口起点"：用库中已有的下一个交易日；若无（末端即最新）则返回 None"""
    try:
        c = sqlite3.connect(AP)
        r = c.execute("SELECT MIN(trade_date) FROM features WHERE REPLACE(trade_date,'-','') > ?",
                      (norm8(last_date),)).fetchone()
        c.close()
        return norm8(r[0]) if r and r[0] else None
    except Exception:
        return None


def norm8(x):
    return ''.join(ch for ch in str(x) if ch.isdigit())[:8]


def load_frame(ap=AP, auc_db=AUC_DB):
    """候选/线上共同的设计矩阵：11 维（线上）+ 9:25 特征 + 软标签"""
    c = sqlite3.connect(ap)
    cols = [r[1] for r in c.execute("PRAGMA table_info(features)")]
    want = [x for x in ('trade_date', 'code', 'bid_change', 'bid_amount', 'bid_turnover', 'price',
                        'mv_rank', 'amt_rank', 'rank_diff', 'price_inv', 'yday_zt', 'yday_lb',
                        'prev_mkt_zt', 'is_limit_up_v3', 'is_limit_up') if x in cols]
    df = pd.read_sql_query("SELECT %s FROM features" % ','.join(want), c)
    try:
        lt = pd.read_sql_query("SELECT trade_date, code, zt FROM label_truth", c)
    except Exception:
        lt = None
    c.close()
    df['trade_date'] = df['trade_date'].map(norm8)
    df['code'] = df['code'].astype(str).str.zfill(6)
    for col in df.columns:
        if col not in ('trade_date', 'code'):
            df[col] = pd.to_numeric(df[col], errors='coerce')
    df['y'] = df['is_limit_up_v3'].fillna(df['is_limit_up'])
    df = df[df.y.notna()].copy()
    if lt is not None and len(lt):
        lt['trade_date'] = lt['trade_date'].map(norm8)
        lt['code'] = lt['code'].astype(str).str.zfill(6)
        lt['zt'] = pd.to_numeric(lt['zt'], errors='coerce').fillna(0)
        lt['y_soft'] = np.where(lt.zt == 1, 2.0, 1.0)
        df = df.merge(lt[['trade_date', 'code', 'y_soft']], on=['trade_date', 'code'], how='left')
    if 'y_soft' not in df.columns:
        df['y_soft'] = df['y'] * 2
    df['y_soft'] = df['y_soft'].fillna(0.0)
    # 9:25 竞价特征：优先 aipick.db 的 auc_open 表（每日任务追加当日），回退独立库
    adf = None
    try:
        c2 = sqlite3.connect(ap)
        has = c2.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                         (AUC_TABLE,)).fetchone()[0]
        if has:
            adf = pd.read_sql_query("SELECT * FROM %s" % AUC_TABLE, c2)
        c2.close()
    except Exception as e:
        print("  auc_open 表读取失败: %s" % str(e)[:60])
    if (adf is None or not len(adf)) and os.path.exists(auc_db):
        a = sqlite3.connect(auc_db)
        adf = pd.read_sql_query("SELECT * FROM auc_open", a)
        a.close()
    # 🔴 防御性去重（2026-10-03 实测踩坑）：auc_open 里曾同时存在 '2026-09-30' 与 '20260930'
    #    两种字面量 ⇒ 规范化后同 (日期,代码) 两行 ⇒ merge 后每只票翻倍、名单出现重复代码。
    if adf is not None and len(adf):
        adf = adf.rename(columns={'date': 'trade_date'})
        adf['trade_date'] = adf['trade_date'].map(norm8)
        adf['code'] = adf['code'].astype(str).str.zfill(6)
        adf = adf.drop(columns=[x for x in ('name', 'src') if x in adf.columns])
        adf = adf.rename(columns={'auc_amt': 'auc_amt_x'})
        adf = adf.drop_duplicates(subset=['trade_date', 'code'], keep='last')
        df = df.merge(adf, on=['trade_date', 'code'], how='left')
    lt = None
    # 最终保险：任何来源的重复键都在此剥掉（影子名单绝不允许重复代码）
    n0 = len(df)
    df = df.drop_duplicates(subset=['trade_date', 'code'], keep='last')
    if len(df) != n0:
        print("  ⚠️ load_frame 去重 %d → %d 行（数据源存在重复键）" % (n0, len(df)))
    return df


def feat_names(df):
    base = [x for x in ('bid_change', 'bid_amount', 'bid_turnover', 'price', 'mv_rank', 'amt_rank',
                        'rank_diff', 'price_inv', 'yday_zt', 'yday_lb', 'prev_mkt_zt')
            if x in df.columns]
    new = [x for x in ('auc_vol_ratio', 'auc_turnover', 'open_bid_pct', 'auc_to_pre_vol_pct',
                       'um_vol', 'auc_vol', 'auc_amt_x', 'm_price') if x in df.columns]
    return base, new


def topn_hit(te, sc, n):
    d = pd.DataFrame({'td': te['trade_date'].values, 'y': te['y'].values, 's': sc})
    return {dt: float(g.nlargest(n, 's')['y'].mean()) for dt, g in d.groupby('td')}


# ---------------------------------------------------------------- 模型
def train_model(tr, names, soft=True, params=None, rounds=ROUNDS):
    import lightgbm as lgb
    prm = dict(S2 if params is None else params)
    if soft:
        prm['label_gain'] = [0.0, 0.5, 1.0]
        lab = tr['y_soft'].astype(int)
    else:
        prm['label_gain'] = [0.0, 1.0]
        lab = tr['y'].astype(int)
    t2 = tr.sort_values('trade_date')
    grp = t2.groupby('trade_date', sort=True).size().tolist()
    ds = lgb.Dataset(t2[names], label=(t2['y_soft'].astype(int) if soft else t2['y'].astype(int)),
                     group=grp, params=prm)
    return lgb.train(prm, ds, num_boost_round=rounds)


def predict(model_path, te, names):
    import lightgbm as lgb
    b = lgb.Booster(model_file=model_path)
    return b.predict(te[names])


def _window_days(since):
    """自 since 起（不含）到最新数据日的交易日数"""
    try:
        c = sqlite3.connect(AP)
        n = c.execute("SELECT COUNT(DISTINCT trade_date) FROM features "
                      "WHERE REPLACE(trade_date,'-','') > ?", (norm8(since),)).fetchone()[0]
        c.close()
        return int(n or 0)
    except Exception:
        return 0


def cmd_train(a):
    # 🔴 窗口冻结（2026-10-03 实测踩坑）：若当前候选的 20 日影子窗口尚未走完，
    #    每日重训会让窗口早期数据进入训练集 ⇒ 闸门评估变成**样本内**、虚高。
    # 🔴 冻结规则（2026-10-03 二次修正）：**只要存在影子指针就跳过重训**，直到闸门完成对该
    #    候选的 20 日评估并清空指针。原因：若写成"窗口走完即可重训"，第 20 天 19:15 会用
    #    含窗口全部数据的样本重训 ⇒ 19:25 的闸门评估变成**样本内**、虚高（实测已踩：+16.67pp 虚高）。
    if not a.force:
        cur = _pointer().get('shadow')
        if cur:
            mf = os.path.join(VERS, cur, 'meta.json')
            try:
                ms = json.load(open(mf, encoding='utf-8'))
                ws = ms.get('train_end') or (ms.get('train_range') or [None, None])[1]
                print("影子候选 %s 在评估中（训练末端 %s，之后 %d 个交易日）⇒ 跳过重训，保持冻结"
                      % (cur, ws, _window_days(ws) if ws else 0))
            except Exception:
                print("影子候选 %s 在评估中 ⇒ 跳过重训" % cur)
            return cur
    df = load_frame()
    _base, new = feat_names(df)
    auc_cols = [x for x in new if x in df.columns]
    d = df.dropna(subset=auc_cols).copy()
    if a.asof:
        d = d[d.trade_date < norm8(a.asof)]
    names = _base + auc_cols
    trn = d.dropna(subset=names)
    t0 = time.time()
    bst = train_model(trn, names, soft=True)
    run = a.tag or ('v' + time.strftime('%Y%m%d_%H%M'))
    out = os.path.join(VERS, run)
    os.makedirs(out, exist_ok=True)
    mp = os.path.join(out, 'model.txt')
    bst.save_model(mp)
    t_end = str(trn.trade_date.max())
    meta = {'ver': run, 'kind': 'lgb', 'objective': 'lambdarank+soft', 'feat_ver': 'v2_11+auc925',
            'train_end': t_end,           # 训练末端（窗口冻结/样本外校验的锚点）
            'window_start': days_next(t_end),   # 影子窗口起点（末端之后第一个交易日）
            'features': names, 'params': {k: v for k, v in S2.items()}, 'rounds': ROUNDS,
            'train_rows': int(len(trn)), 'train_days': int(trn.trade_date.nunique()),
            'train_range': [trn.trade_date.min(), trn.trade_date.max()],
            'trained_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'secs': round(time.time() - t0, 1)}
    with open(os.path.join(out, 'meta.json'), 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print("候选版本已训练: %s\n  %d 行 / %d 天 (%s~%s) / %d 特征 / %.0fs"
          % (mp, meta['train_rows'], meta['train_days'], meta['train_range'][0],
             meta['train_range'][1], len(names), meta['secs']))
    ptr = _pointer()
    ptr['shadow'] = run
    ptr['shadow_trained_at'] = meta['trained_at']
    _write_pointer(ptr)
    print("  ⚠️ 尚未切换线上（线上仍是 %s）；影子指针已指向 %s" % (LIVE, run))
    return run


def cmd_fetch_auc(a):
    """抓当日 9:25 全市场竞价特征 → aipick.db 的 auc_open 表（幂等 upsert）
    ⚠️ 影子名单依赖它：没有当日竞价特征就无法给候选模型喂特征。"""
    sys.path.insert(0, '/opt/kuaixuan/backend')
    from app.services import meoz_client as M
    d8 = norm8(a.date) if a.date else time.strftime('%Y%m%d')
    # 🔴 内置重试（2026-10-03 主人追问"9:31 是否太晚"后加）：竞价接口在 9:25 刚过时常未就绪，
    #    而调度器 _run_task 的 setnx 每天只允许执行一次 ⇒ 必须**在脚本内**等数据就绪，
    #    否则一天白跑。默认最多等 240s（每 20s 重试一次）。
    wait = getattr(a, 'wait_sec', 240)
    t0 = time.time()
    got = {}
    while True:
        try:
            got = M.auc_open_bid(date=d8) or {}
        except Exception as e:
            print("  fetch 异常: %s" % str(e)[:70])
            got = {}
        if got:
            break
        if time.time() - t0 >= wait:
            print("⚠️ %s 竞价数据等待 %ds 仍为空（非交易日？接口延迟？）" % (d8, wait))
            return
        print("  %s 竞价数据未就绪，20s 后重试（已等 %ds/%ds）"
              % (d8, int(time.time() - t0), wait), flush=True)
        time.sleep(20)
    print("  %s 竞价数据就绪：%d 只（等待 %ds）" % (d8, len(got), int(time.time() - t0)))
    cols = ['date', 'code', 'name', 'auc_pct_chg', 'auc_vol_ratio', 'auc_turnover', 'open_bid_pct',
            'auc_to_pre_vol_pct', 'um_vol', 'auc_amt', 'm_price', 'auc_vol', 'is_st', 'src']
    rows = []
    for code, v in got.items():
        rows.append((d8, str(code).zfill(6), v.get('name'), v.get('auc_pct_chg'),
                     v.get('auc_vol_ratio'), v.get('auc_turnover'), v.get('open_bid_pct'),
                     v.get('auc_to_pre_vol_pct'), v.get('um_vol'), v.get('auc_amt'),
                     v.get('m_price'), v.get('auc_vol'),
                     1 if v.get('is_st') in (True, 1, '1', 'true', 'True') else 0, 'meoz_open_bid'))
    if a.commit:
        c = sqlite3.connect(AP)
        c.execute("CREATE TABLE IF NOT EXISTS %s(%s, PRIMARY KEY(date, code))"
                  % (AUC_TABLE, ','.join('%s %s' % (x, 'TEXT' if x in ('date', 'code', 'name', 'src')
                                                    else ('INTEGER' if x == 'is_st' else 'REAL'))
                                          for x in cols)))
        c.executemany("INSERT OR REPLACE INTO %s VALUES (%s)"
                      % (AUC_TABLE, ','.join('?' * len(cols))), rows)
        c.commit()
        n = c.execute("SELECT COUNT(*) FROM %s WHERE date=?" % AUC_TABLE, (d8,)).fetchone()[0]
        c.close()
        print("已写入 %s：%s 当日 %d 只（表内该日累计 %d 行）" % (AUC_TABLE, d8, len(rows), n))
    else:
        print("（未 --commit，仅预览）%s 抓到 %d 只" % (d8, len(rows)))


# ---------------------------------------------------------------- 影子名单 / 闸门
def cmd_picks(a):
    df = load_frame()
    base, new = feat_names(df)
    auc_cols = [x for x in new if x in df.columns]
    d8 = norm8(a.date) if a.date else None
    if not d8:
        cand_days = sorted(df.dropna(subset=auc_cols).trade_date.unique())
        d8 = cand_days[-1] if cand_days else time.strftime('%Y%m%d')
    d = df[df.trade_date == d8].dropna(subset=auc_cols).copy()
    if not len(d):
        print("无 %s 的可用样本（9:25 特征缺失？先跑 fetch_auc）" % d8)
        return
    mp = a.cand or _shadow_cand()
    if not mp or not os.path.exists(mp):
        print("影子候选不存在（先跑 shadow.py train，或等 19:15 的 aipick_shadow_train）")
        return
    ver = os.path.basename(os.path.dirname(mp))
    names = base + auc_cols
    d = d.dropna(subset=names)
    d['sc'] = predict(mp, d, names)
    rows = []
    for n in TOPN:
        top = d.nlargest(n, 'sc')
        for r in top.itertuples():
            rows.append((d8, 'shadow', r.code, n, round(float(r.sc), 6), ver, r.y))
    if a.commit:
        c = sqlite3.connect(AP)
        c.execute("CREATE TABLE IF NOT EXISTS pick_daily_shadow(trade_date TEXT, line TEXT, code TEXT,"
                  " topn INTEGER, score REAL, model_ver TEXT, y REAL, created_at TEXT,"
                  " PRIMARY KEY(trade_date, line, code, topn))")
        now = time.strftime('%Y-%m-%d %H:%M:%S')
        c.executemany("INSERT OR REPLACE INTO pick_daily_shadow VALUES (?,?,?,?,?,?,?,?)",
                      [r + (now,) for r in rows])
        c.commit()
        c.close()
        print("已写 pick_daily_shadow %d 行（影子，不推用户）" % len(rows))
    else:
        print("（未 --commit，仅预览）%s top3: %s" % (d8, [r[2] for r in rows if r[3] == 3]))


def paired_daily(online, shadow):
    common = sorted(set(online) & set(shadow))
    if not common:
        return None
    dv = np.array([shadow[d] - online[d] for d in common])
    t = (dv.mean() / (dv.std(ddof=1) / np.sqrt(len(dv)))
         if len(dv) > 1 and dv.std(ddof=1) > 0 else 0.0)
    return {'n': len(common), 'diff_pp': 100 * dv.mean(), 'sd_pp': 100 * dv.std(ddof=1), 't': t}


def gate_decision(stat20, stat60, cfg=None):
    """两段式闸门：阶段1 影子期≥20 日（点估计≥2pp）→ 阶段2 累计 60 日复核（非劣）"""
    c = cfg or {}
    eff = c.get('min_effect', GATE_MIN_EFFECT)
    worse = c.get('max_worse', GATE_MAX_WORSE)
    if stat20 is None or stat20['n'] < MIN_SHADOW_DAYS:
        return 'continue_shadow', '影子期不足 %d 日（现 %s）' % (MIN_SHADOW_DAYS,
                                                                stat20['n'] if stat20 else 0)
    min_t = c.get('min_t', GATE_MIN_T)
    if stat20['diff_pp'] < eff:
        return 'continue_shadow', '20 日配对提升 %.2fpp < %.1fpp' % (stat20['diff_pp'], eff)
    if stat20.get('t', 0) < min_t:
        return 'continue_shadow', ('20 日提升 %.2fpp 但 t=%.2f < %.1f（噪声护栏）'
                                   % (stat20['diff_pp'], stat20.get('t', 0), min_t))
    if stat60 and stat60['n'] >= REVIEW_DAYS and stat60['diff_pp'] < -worse:
        return 'rollback', '60 日复核恶化 %.2fpp > %.1fpp' % (-stat60['diff_pp'], worse)
    if stat60 and stat60['n'] >= REVIEW_DAYS:
        return 'promote', ('20 日 +%.2fpp 且 60 日复核 %.2fpp（非劣）'
                           % (stat20['diff_pp'], stat60['diff_pp']))
    return 'trial', '20 日闸门通过（+%.2fpp）⇒ 进入试运行，待 60 日复核' % stat20['diff_pp']


def cmd_gate(a):
    df = load_frame()
    base, new = feat_names(df)
    auc_cols = [x for x in new if x in df.columns]
    d = df.dropna(subset=auc_cols).copy()
    names = base + auc_cols
    days = sorted(d.trade_date.unique())
    asof = norm8(a.asof) if a.asof else days[-1]
    hist = [x for x in days if x <= asof]
    if len(hist) < MIN_SHADOW_DAYS + 1:
        print("历史不足")
        return
    test = hist[-MIN_SHADOW_DAYS:]
    tr = d[d.trade_date < test[0]].dropna(subset=names)
    te = d[d.trade_date.isin(test)].dropna(subset=names)
    online_path = LIVE
    shadow_ver = a.ver or _pointer().get('shadow')
    shadow_path = (os.path.join(VERS, shadow_ver, 'model.txt') if shadow_ver
                   else _shadow_cand())
    if not os.path.exists(shadow_path):
        print("⚠️ 影子模型不存在: %s（先 train）" % shadow_path)
        return
    import lightgbm as lgb
    bq = lgb.Booster(model_file=online_path)
    qnames = [x for x in bq.feature_name() if x in te.columns]
    sc_q = bq.predict(te[qnames])
    sc_s = predict(shadow_path, te, names)
    stats = {}
    for n in TOPN:
        stats[n] = paired_daily(topn_hit(te, sc_q, n), topn_hit(te, sc_s, n))
    # 样本外校验：窗口起点必须晚于该候选的训练末端，否则评估是样本内（虚高）
    oos_ok, oos_msg = True, ''
    try:
        ms = json.load(open(os.path.join(os.path.dirname(shadow_path), 'meta.json'),
                            encoding='utf-8'))
        t_end = str(ms.get('train_range', ['', ''])[1] or '')
        ws = str(ms.get('window_start') or '')
        if t_end and test[0] <= t_end:
            oos_ok = False
            oos_msg = ('窗口起点 %s ≤ 候选训练末端 %s ⇒ 样本内评估，禁止据此晋级'
                       % (test[0], t_end))
        elif ws and test[0] != ws:
            oos_ok, oos_msg = False, ('窗口起点 %s 与候选登记起点 %s 不一致 ⇒ 待窗口走满'
                                      % (test[0], ws))
    except Exception as e:
        oos_ok, oos_msg = False, '候选 meta 缺失/不可读: %s' % str(e)[:50]
    print("闸门判定 asof=%s（影子期 %d 日: %s~%s）| 候选=%s"
          % (asof, len(test), test[0], test[-1], os.path.basename(os.path.dirname(shadow_path))))
    for n in TOPN:
        s = stats[n]
        print("   top%-3d 线上→影子 配对差 %+6.2fpp (sd %.2f, t=%5.2f, n=%d)"
              % (n, s['diff_pp'], s['sd_pp'], s['t'], s['n']))
    dec, why = gate_decision(stats[3], None,
                             {'min_t': getattr(a, 'min_t', GATE_MIN_T)})
    if not oos_ok:
        print("   ⚠️ 样本外校验未通过：%s" % oos_msg)
        dec, why = 'continue_shadow', oos_msg
    if dec in ('trial', 'promote') and stats[5]['diff_pp'] < GATE_SECOND_TOL:
        dec, why = 'continue_shadow', 'top5 配对 %.2fpp < 0（次档位不劣护栏）' % stats[5]['diff_pp']
    print("   ⇒ 决策(以 top3 为主判据): %s | %s" % (dec, why))
    prev = _recent_gate(2)
    frozen = _is_frozen()
    if len(prev) >= 2 and all(p['top3_pp'] < -WORSEN_PP for p in prev[:2]):
        _set_frozen('连续 2 日命中率劣化 > %.1fpp' % WORSEN_PP)
        _alert('【快选·影子】触发冻结：连续 2 日排序命中率劣化 > %.1fpp，已暂停晋级' % WORSEN_PP)
        frozen = True
    if dec in ('promote', 'trial') and frozen:
        print("   ⚠️ 已冻结（%s）⇒ 本次不执行晋级" % _freeze_reason())
        dec = 'continue_shadow'
    streak = 1 + sum(1 for p in prev if p['decision'] == dec and dec in ('trial', 'promote',
                                                                        'rollback'))
    if a.act and dec in ('promote', 'rollback') and streak >= DECAY_CONFIRM:
        if dec == 'promote':
            _atomic_switch(_shadow_cand(), '20日闸门通过')
            _alert('【快选·影子】已自动晋级 v3（top3 +%.2fpp, t=%.2f，连续 %d 窗同向）'
                   % (stats[3]['diff_pp'], stats[3]['t'], streak))
        else:
            _rollback_file()
            _alert('【快选·影子】已自动回滚（%s）' % why)
        print("   ⇒ 已执行 %s（连续 %d 窗同向）" % (dec, streak))
    elif a.act and dec in ('promote', 'rollback'):
        print("   ⇒ 待确认（连续 %d/%d 窗同向，暂不执行）" % (streak, DECAY_CONFIRM))
    # 窗口走完但未通过 ⇒ 清空影子指针，下一轮重新训练新候选（避免"陈旧候选"长期占位）
    if a.act and oos_ok and dec == 'continue_shadow' and len(test) >= MIN_SHADOW_DAYS:
        ptr = _pointer()
        if ptr.get('shadow'):
            ptr['shadow_prev'], ptr['shadow'] = ptr['shadow'], None
            _write_pointer(ptr)
            print("   ⇒ 本轮窗口未通过，影子指针已清空（次日晚间重训新候选）")
    if a.commit:
        c = sqlite3.connect(AP)
        c.execute("CREATE TABLE IF NOT EXISTS shadow_gate(trade_date TEXT, shadow_ver TEXT,"
                  " online_ver TEXT, n_days INTEGER, top3_pp REAL, top5_pp REAL, top10_pp REAL,"
                  " top30_pp REAL, decision TEXT, reason TEXT, created_at TEXT,"
                  " PRIMARY KEY(trade_date, shadow_ver))")
        c.execute("INSERT OR REPLACE INTO shadow_gate VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (asof, shadow_ver, _pointer().get('live'), len(test),
                   stats[3]['diff_pp'], stats[5]['diff_pp'], stats[10]['diff_pp'],
                   stats[30]['diff_pp'], dec, why, time.strftime('%Y-%m-%d %H:%M:%S')))
        c.commit()
        c.close()
        print("   已落库 shadow_gate")


def _recent_gate(n):
    try:
        c = sqlite3.connect(AP)
        rows = c.execute("SELECT decision, top3_pp FROM shadow_gate ORDER BY trade_date DESC "
                         "LIMIT ?", (n,)).fetchall()
        c.close()
        return [{'decision': r[0], 'top3_pp': r[1] or 0.0} for r in rows]
    except Exception:
        return []


def _is_frozen():
    if not os.path.exists(FREEZE):
        return False
    try:
        with open(FREEZE, encoding='utf-8') as f:
            return bool(json.load(f).get('frozen'))
    except Exception:
        return False


def _freeze_reason():
    try:
        with open(FREEZE, encoding='utf-8') as f:
            return json.load(f).get('reason', '')
    except Exception:
        return ''


def _set_frozen(reason):
    with open(FREEZE + '.tmp', 'w', encoding='utf-8') as f:
        json.dump({'frozen': True, 'reason': reason,
                   'at': time.strftime('%Y-%m-%d %H:%M:%S')}, f, ensure_ascii=False)
    os.replace(FREEZE + '.tmp', FREEZE)


def _alert(msg):
    """飞书告警（复用 NOTIFY_FEISHU_WEBHOOK；失败只记日志，不影响流程）"""
    line = '[shadow] %s' % msg
    try:
        import urllib.request
        hook = os.environ.get('NOTIFY_FEISHU_WEBHOOK', '')
        if not hook:
            print(line + '（未配置 webhook，仅记录）')
            return
        req = urllib.request.Request(hook, data=json.dumps(
            {'msg_type': 'text', 'content': {'text': msg}}).encode(),
            headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=5)
        print(line + '（已推送飞书）')
    except Exception as e:
        print(line + '（推送失败: %s）' % str(e)[:60])


def _atomic_switch(src, why):
    """原子切换线上模型：备份 → temp+rename（加载方永远看到完整文件）"""
    os.makedirs(BACKUP, exist_ok=True)
    ts = time.strftime('%Y%m%d_%H%M%S')
    if os.path.exists(LIVE):
        shutil.copy2(LIVE, os.path.join(BACKUP, 'model_lgb.%s.txt' % ts))
    tmp = LIVE + '.tmp'
    shutil.copy2(src, tmp)
    os.replace(tmp, LIVE)
    p = _pointer()
    p['live_prev'] = p.get('live')
    p['live'] = 'v3_ranker'
    p['promoted_at'] = ts
    p['promoted_reason'] = why
    _write_pointer(p)
    print("   ✓ 已切换 %s → %s（备份 model_lgb.%s.txt）" % (src, LIVE, ts))


def _rollback_file():
    baks = sorted(glob.glob(os.path.join(BACKUP, 'model_lgb.*.txt')))
    if not baks:
        print("   ⚠️ 无备份可回滚")
        return
    tmp = LIVE + '.tmp'
    shutil.copy2(baks[-1], tmp)
    os.replace(tmp, LIVE)
    print("   ✓ 已回滚到 %s" % baks[-1])


# ---------------------------------------------------------------- 晋级 / 回滚
def _shadow_cand():
    """当前影子候选模型文件：指针优先，否则取 versions/ 下最新一个"""
    p = _pointer().get('shadow')
    if p:
        f = os.path.join(VERS, p, 'model.txt')
        if os.path.exists(f):
            return f
    cands = sorted(glob.glob(os.path.join(VERS, '*', 'model.txt')))
    return cands[-1] if cands else None


def _pointer():
    if os.path.exists(POINTER):
        try:
            with open(POINTER, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {'live': None, 'shadow': None}


def _write_pointer(p):
    with open(POINTER + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(p, f, ensure_ascii=False, indent=2)
    os.replace(POINTER + '.tmp', POINTER)


def cmd_promote(a):
    """原子晋级：备份当前线上 → temp+rename 覆盖 → 写指针（单日最多 1 次由调度层保证）"""
    ver = a.ver or _pointer().get('shadow')
    src = os.path.join(VERS, ver, 'model.txt')
    if not os.path.exists(src):
        print("候选不存在: %s" % src)
        return
    os.makedirs(BACKUP, exist_ok=True)
    ts = time.strftime('%Y%m%d_%H%M%S')
    bak = os.path.join(BACKUP, 'model_lgb.%s.txt' % ts)
    if os.path.exists(LIVE):
        shutil.copy2(LIVE, bak)
    p = _pointer()
    p['live_prev'] = p.get('live')
    p['live'] = ver
    p['promoted_at'] = ts
    if a.commit:
        tmp = LIVE + '.tmp'
        shutil.copy2(src, tmp)
        os.replace(tmp, LIVE)                       # 原子替换：加载方永远看到完整文件
        _write_pointer(p)
        print("已晋级: %s → %s\n  备份(回滚源): %s" % (ver, LIVE, bak))
    else:
        print("（未 --commit）将晋级 %s → %s，备份 %s" % (ver, LIVE, bak))


def cmd_rollback(a):
    p = _pointer()
    prev = p.get('live_prev')
    baks = sorted(glob.glob(os.path.join(BACKUP, 'model_lgb.*.txt')))
    src = os.path.join(VERS, prev, 'model.txt') if prev else (baks[-1] if baks else None)
    if not src or not os.path.exists(src):
        print("找不到回滚源（prev=%s, backups=%d）" % (prev, len(baks)))
        return
    if a.commit:
        tmp = LIVE + '.tmp'
        shutil.copy2(src, tmp)
        os.replace(tmp, LIVE)
        p['live'], p['live_prev'] = prev, p.get('live')
        p['rolled_back_at'] = time.strftime('%Y%m%d_%H%M%S')
        _write_pointer(p)
        print("已回滚到 %s" % src)
    else:
        print("（未 --commit）将回滚到 %s" % src)


# ---------------------------------------------------------------- 历史回放
def cmd_replay(a):
    """用历史数据回放整条影子流程：5 折 × 20 日 ⇒ 每折末做闸门判定（含防抖）
    只读（除 --out 输出 json）；用于验证闸门不会误触发、也不会永不触发。"""
    df = load_frame()
    base, new = feat_names(df)
    auc_cols = [x for x in new if x in df.columns]
    d = df.dropna(subset=auc_cols).copy()
    names, qnames = base + auc_cols, base
    days = sorted(d.trade_date.unique())
    win, step = 20, 20
    need = a.days
    segs = [days[len(days) - need + i:len(days) - need + i + win]
            for i in range(0, max(need - win + 1, 1), step)]
    segs = [s for s in segs if len(s) == win]
    print("回放 %d 个影子窗 × %d 日（共 %d 日）" % (len(segs), win, len(segs) * win))
    out, prev_dec, streak = [], None, 0
    import lightgbm as lgb
    bq = lgb.Booster(model_file=LIVE)
    for k, seg in enumerate(segs, 1):
        tr = d[d.trade_date < seg[0]].dropna(subset=names)
        te = d[d.trade_date.isin(seg)].dropna(subset=names)
        bst = train_model(tr, names, soft=True)
        sc_s = bst.predict(te[names])
        sc_q = bq.predict(te[[x for x in bq.feature_name() if x in te.columns]])
        stats = {n: paired_daily(topn_hit(te, sc_q, n), topn_hit(te, sc_s, n)) for n in TOPN}
        dec, why = gate_decision(stats[3], None,
                                 {'min_t': a.min_t, 'second_tol': GATE_SECOND_TOL})
        if dec == 'trial' and stats[5]['diff_pp'] < GATE_SECOND_TOL:
            dec, why = ('continue_shadow',
                        'top5 配对 %.2fpp < 0（次档位不劣护栏）' % stats[5]['diff_pp'])
        # 防抖：连续 2 个窗口同向才执行晋级/回滚
        if dec == prev_dec and dec in ('trial', 'promote', 'rollback'):
            streak += 1
        else:
            streak = 1 if dec in ('trial', 'promote', 'rollback') else 0
        act = dec if streak >= DECAY_CONFIRM or dec == 'continue_shadow' else 'hold(待确认)'
        row = {'window': k, 'seg': [seg[0], seg[-1]], 'train_days': int(tr.trade_date.nunique()),
               'decision': dec, 'action': act, 'streak': streak, 'reason': why,
               **{'top%d_pp' % n: round(stats[n]['diff_pp'], 2) for n in TOPN},
               **{'top%d_t' % n: round(stats[n]['t'], 2) for n in TOPN}}
        out.append(row)
        print("  窗%d %s~%s 训练%3d天 | top3 %+5.2fpp(t=%5.2f) top5 %+5.2f top30 %+5.2f | %s%s"
              % (k, seg[0], seg[-1], row['train_days'], row['top3_pp'], row['top3_t'],
                 row['top5_pp'], row['top30_pp'], dec, (' ×%d' % streak) if streak > 1 else ''))
        prev_dec = dec
    n_cont = sum(1 for r in out if r['decision'] == 'continue_shadow')
    print("\n汇总: %d 窗 | 通过闸门 %d | 继续影子 %d" % (len(out), len(out) - n_cont, n_cont))
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print("回放明细: %s" % a.out)


def main():
    p = argparse.ArgumentParser(description='影子模型 / 闸门 / 晋级 / 回滚')
    sub = p.add_subparsers(dest='cmd', required=True)
    t = sub.add_parser('train'); t.add_argument('--tag'); t.add_argument('--asof')
    t.add_argument('--force', action='store_true', help='忽略窗口冻结，强制重训')
    t.set_defaults(func=cmd_train)
    k = sub.add_parser('picks'); k.add_argument('--date'); k.add_argument('--ver')
    k.add_argument('--cand'); k.add_argument('--commit', action='store_true')
    k.set_defaults(func=cmd_picks)
    fa = sub.add_parser('fetch_auc'); fa.add_argument('--date')
    fa.add_argument('--wait-sec', type=int, default=240, help='数据未就绪时的最长等待秒数')
    fa.add_argument('--commit', action='store_true'); fa.set_defaults(func=cmd_fetch_auc)
    g = sub.add_parser('gate'); g.add_argument('--asof'); g.add_argument('--ver')
    g.add_argument('--min-t', type=float, default=GATE_MIN_T)
    g.add_argument('--act', action='store_true', help='达标自动晋级 / 恶化自动回滚')
    g.add_argument('--commit', action='store_true'); g.set_defaults(func=cmd_gate)
    pr = sub.add_parser('promote'); pr.add_argument('--ver'); pr.add_argument('--commit',
                                                                             action='store_true')
    pr.set_defaults(func=cmd_promote)
    rb = sub.add_parser('rollback'); rb.add_argument('--commit', action='store_true')
    rb.set_defaults(func=cmd_rollback)
    rp = sub.add_parser('replay'); rp.add_argument('--days', type=int, default=100)
    rp.add_argument('--out'); rp.add_argument('--min-t', type=float, default=GATE_MIN_T)
    rp.set_defaults(func=cmd_replay)
    a = p.parse_args()
    a.func(a)


if __name__ == '__main__':
    main()
