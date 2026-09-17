# -*- coding: utf-8 -*-
"""流通市值口径**数据修复** (v4.11.28, 2026-09-18)

背景(生产实锤 9/17): 9:24/9:25 东财全分区失败时, `_fetch_kpl_fallback()` 把开盘啦的
`floatMv`(=**实际流通**, 量级为流通市值的 0.28~0.57 倍)直接写进了 `float_mv` 列
(该列全系统语义 = 东财 f21 **流通市值**) → 真大盘股被 floatMvFloor 系统性误剔
(9/17 实测 56 只真流通≥30亿被落库成<30亿; 其中 3 只其它门槛全过)。
起始日 9/11, 连续 5 个交易日。代码侧已在 v4.11.28 修好, 本脚本修**历史数据**。

修复范围(默认 2026-09-11 ~ 2026-09-17):
  ① snapshot_bid       行签名: float_mv>0 AND (free_mv IS NULL OR free_mv=0)
                        → float_mv 改成真值; 原值挪到 free_mv(它本就是"实际流通")
  ② stock_float_mv_daily  float_mv < 真值×0.75 的行 → 改成真值, src 标 'fix'
  ③ stock_score_daily    同上(只改 float_mv 列, **不重算评分** —— 历史名单已定型,
                        重算会改 probability/rank, 属另一个决策, 不在本次范围内)

真值来源(**优先级从高到低, 越靠前越不需要网络**):
  1. 同日任意时点的**东财行**(free_mv>0)的 float_mv → 同一天, 无需网络, 最准
  2. stock_float_mv_daily 同日 free_mv>0 的行
  3. 当前东财 ulist f21(网络) —— 仅在 1/2 都取不到时使用, 且只在
     `现值 < 真值×0.75` 时才采信(避免用"今天的价格"覆盖"当天的市值")

安全:
  * 默认 **dry-run**(只打印计划与样例, 不写库); 加 `--apply` 才真正写入
  * `--apply` 前自动 `cp` 一份 DB 备份到同目录 `.bak_fixmv_<时间戳>`
  * 单事务提交, 任一步异常整体回滚
"""
import argparse
import json
import shutil
import sqlite3
import ssl
import time
import urllib.request

DB = "/opt/kuaixuan/kuaixuan.db"
DATES = ["2026-09-11", "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17"]
BIAS = 0.80          # 现值 < 真值×0.80 → 认定被写小
"""为什么必须是 0.80 而不是"有签名就修"(2026-09-18 实测定标):
   `float_mv>0 且 free_mv=0` 这个签名**不能区分两种行**:
     a) 开盘啦兜底错值(真错, 实测 0.28~0.73 倍)
     b) mv_cache 缓存补值(好数据, 实测 0.90~1.10 倍 —— 缓存行 free_mv 为 NULL 时
        补不到自由流通, 于是留下 free_mv=0; float_mv 跨日恒定即此特征, 如 300142
        连续 5 日恒为 206.70 亿)
   加上 9_15 与 9_25 两个时点的竞价价差(±10%), 两类行的比值域在 0.8 附近干净分开:
   错值最大 0.73 / 好数据最小 ~0.90。故只修 <0.80 的, 其余一律不动 —— 拿 9_25 的值
   覆盖 9_15 的值本身就是另一种污染。"""
CHUNK = 60

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}


def em_f21(codes):
    """当前东财 f21 流通市值(元)。失败返回 {} —— 兜底源, 取不到不影响主流程。"""
    def secid(c):
        return ("1." if c[0] in "65" else "0.") + c

    out = {}
    for i in range(0, len(codes), CHUNK):
        ch = codes[i:i + CHUNK]
        url = ("https://push2dycalc.eastmoney.com/api/qt/ulist.np/get"
               "?fltt=2&invt=2&fields=f12,f21,f117&secids="
               + ",".join(secid(c) for c in ch))
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20, context=_ctx) as r:
                j = json.loads(r.read().decode("utf-8", "ignore"))
        except Exception as e:                                    # noqa: BLE001
            print("  [网络] 东财块 %d 失败(%s), 跳过该块" % (i // CHUNK, e))
            continue
        for d in ((j.get("data") or {}).get("diff") or []):
            try:
                v = float(d.get("f21") or 0)
            except (TypeError, ValueError):
                continue
            if v > 0:
                out[d.get("f12")] = v
        time.sleep(0.05)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写库(默认只预演)")
    ap.add_argument("--db", default=DB)
    ap.add_argument("--dates", default=",".join(DATES))
    args = ap.parse_args()
    dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    d0, d1 = dates[0], dates[-1]

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    # ---------- 1) 收集待修行 ----------
    print("=" * 78)
    print("流通市值口径修复 %s  范围 %s ~ %s  mode=%s"
          % ("(APPLY)" if args.apply else "(DRY-RUN)", d0, d1, args.apply))
    print("=" * 78)

    # ① snapshot_bid 签名行(开盘啦兜底污染)
    sig = cur.execute(
        "SELECT date, time_point, code, float_mv, free_mv FROM snapshot_bid "
        "WHERE date BETWEEN ? AND ? AND float_mv > 0 "
        "AND (free_mv IS NULL OR free_mv = 0) ORDER BY date, time_point, code",
        (d0, d1)).fetchall()
    print("\n① snapshot_bid 污染签名行(float_mv>0 且 free_mv=0): %d 行" % len(sig))

    # ② 缓存表现存值
    cmv = cur.execute(
        "SELECT date, code, float_mv, free_mv FROM stock_float_mv_daily "
        "WHERE date BETWEEN ? AND ? AND float_mv > 0", (d0, d1)).fetchall()
    print("② stock_float_mv_daily 有值行: %d" % len(cmv))

    # ③ 物化评分表现存值
    smv = cur.execute(
        "SELECT date, code, float_mv FROM stock_score_daily "
        "WHERE date BETWEEN ? AND ? AND float_mv > 0", (d0, d1)).fetchall()
    print("③ stock_score_daily 有值行: %d" % len(smv))

    # ---------- 2) 构造真值 ----------
    truth = {}                                   # (date, code) -> 真值(元)
    # 来源 1: 同日东财行(free_mv>0)的最大 float_mv
    for dt, code, mv in cur.execute(
            "SELECT date, code, MAX(float_mv) FROM snapshot_bid "
            "WHERE date BETWEEN ? AND ? AND float_mv > 0 AND free_mv > 0 "
            "GROUP BY date, code", (d0, d1)):
        truth[(dt, code)] = mv
    n1 = len(truth)
    # 来源 2: 缓存表同日 free_mv>0 的行
    for dt, code, mv in cur.execute(
            "SELECT date, code, MAX(float_mv) FROM stock_float_mv_daily "
            "WHERE date BETWEEN ? AND ? AND float_mv > 0 AND free_mv > 0 "
            "GROUP BY date, code", (d0, d1)):
        truth.setdefault((dt, code), mv)
    print("\n真值来源: 同日东财快照 %d + 同日缓存表补 %d → 覆盖 %d (date,code)"
          % (n1, len(truth) - n1, len(truth)))

    # 来源 3: 网络(只补前两源都没有的 code; 另外始终取一份**当前全市场真值**用于交叉验证)
    need_net = sorted({c for (_d, c) in
                       [(r[0], r[2]) for r in sig]
                       + [(r[0], r[1]) for r in cmv]
                       + [(r[0], r[1]) for r in smv]
                       if (_d, c) not in truth})
    all_codes = sorted({r[2] for r in sig} | {r[1] for r in cmv} | {r[1] for r in smv})
    now_mv = {}
    print("\n网络: 取当前东财 f21 全市场 %d 只(用于兜底 %d 只 + 交叉验证) ..."
          % (len(all_codes), len(need_net)))
    now_mv = em_f21(all_codes)
    print("  取到 %d 只" % len(now_mv))

    # ---------- 2.5) 交叉验证: "同日东财快照"这个基准本身可信吗? ----------
    # 若基准错, 整个修复就是把数据从一种错改成另一种错 —— 必须先证伪。
    # 判据: 同日真值 与 当前网络真值 的差异, 应集中在"5 日累计涨跌"范围内(<15%)。
    print("\n[交叉验证] 同日东财快照 vs 当前东财 f21(抽样 12 只, 取最后一日 %s):" % d1)
    diffs = []
    shown = 0
    for (_dt, code), t in sorted(truth.items()):
        if _dt != d1 or code not in now_mv:
            continue
        diffs.append(abs(t - now_mv[code]) / now_mv[code])
        if shown < 12:
            print("  %s  同日 %.2f亿  vs  当前 %.2f亿  差异 %+.1f%%"
                  % (code, t / 1e8, now_mv[code] / 1e8,
                     (t - now_mv[code]) / now_mv[code] * 100))
            shown += 1
    if diffs:
        diffs.sort()
        med = diffs[len(diffs) // 2]
        p90 = diffs[int(len(diffs) * 0.9)]
        print("  → 样本 %d 只: 差异中位数 %.1f%%  P90 %.1f%%  最大 %.1f%%"
              % (len(diffs), med * 100, p90 * 100, diffs[-1] * 100))
        if med > 0.15:
            print("  ⚠️ 中位数 >15%: 基准与当前真值系统性不符, 请先人工核对再 apply!")

    def get_truth(dt, code, cur_mv, signed=False):
        """返回 (真值, 来源) 或 (None, 原因)

        signed=True(快照签名行): 现值已被**证明**是"实际流通"口径, 网络兜底不必
        再看偏差 —— 拿到真值就修; 否则(缓存/物化表)只在偏差 >25% 时采信网络值,
        避免用"今天的价格"覆盖"当天的市值"。"""
        t = truth.get((dt, code))
        if t:
            return t, "同日东财/缓存"
        t = now_mv.get(code)
        if t and (signed or (cur_mv and cur_mv < t * BIAS)):
            return t, "东财实时"
        return None, "无真值/偏差过小"

    # ---------- 3) 生成修复计划 ----------
    plan_snap, plan_cache, plan_score = [], [], []
    miss_snap = 0
    for dt, tp, code, mv, fmv in sig:
        t, _src = get_truth(dt, code, mv, signed=True)
        if not t:
            # 无真值可用 → **一律不动**。
            # (v1 曾打算"归零为未知" —— 错值确实比未知危险, 但无真值时无法区分
            #  "被写小的错值" 与 "缓存补的正常值"(后者占多数), 归零会把好数据清空,
            #  破坏性更大。宁可留着: 9/18 起采集侧已修好, 新数据不会再错。)
            miss_snap += 1
            continue
        if mv and mv >= t * BIAS:
            continue        # 时点价差 / 缓存补的正常值 → 不动(见 BIAS 注释)
        plan_snap.append((t, mv or 0, dt, tp, code))   # SET float_mv=t, free_mv=原值
    for dt, code, mv, fmv in cmv:
        t, _src = get_truth(dt, code, mv)
        if not t or mv >= t * BIAS:
            continue
        plan_cache.append((t, dt, code))
    for dt, code, mv in smv:
        t, _src = get_truth(dt, code, mv)
        if not t or mv >= t * BIAS:
            continue
        plan_score.append((t, dt, code))

    print("\n" + "-" * 78)
    print("修复计划:")
    print("  snapshot_bid        %d 行改为真值(另有 %d 行无真值可比对 → 保持原样)"
          % (len(plan_snap), miss_snap))
    print("  stock_float_mv_daily %d 行" % len(plan_cache))
    print("  stock_score_daily    %d 行" % len(plan_score))

    print("\n样例(前 12 条 snapshot_bid):")
    for t, mv, dt, tp, code in plan_snap[:12]:
        print("  %s %s %s  %.2f亿 → %.2f亿  (×%.2f)"
              % (dt, tp, code, mv / 1e8, t / 1e8, (mv / t) if t else 0))
    if plan_cache:
        print("\n样例(前 8 条 stock_float_mv_daily):")
        for t, dt, code in plan_cache[:8]:
            old = next((r[2] for r in cmv if r[0] == dt and r[1] == code), 0)
            print("  %s %s  %.2f亿 → %.2f亿" % (dt, code, old / 1e8, t / 1e8))

    if not args.apply:
        print("\n[DRY-RUN] 未写库。确认无误后加 --apply 执行(会自动备份 DB)。")
        conn.close()
        return

    # ---------- 4) 写库 ----------
    bak = "%s.bak_fixmv_%s" % (args.db, time.strftime("%Y%m%d_%H%M%S"))
    print("\n备份数据库 → %s" % bak)
    conn.close()                      # 先关连接再 copy, 避免 SQLite 处于事务中间
    import os
    shutil.copy2(args.db, bak)
    print("  备份完成 %.1f MB" % (os.path.getsize(bak) / 1e6))

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    try:
        cur.executemany(
            "UPDATE snapshot_bid SET float_mv=?, free_mv=? "
            "WHERE date=? AND time_point=? AND code=?", plan_snap)
        n_a = cur.rowcount
        cur.executemany(
            "UPDATE stock_float_mv_daily SET float_mv=?, src='fix' "
            "WHERE date=? AND code=?", plan_cache)
        n_b = cur.rowcount
        cur.executemany(
            "UPDATE stock_score_daily SET float_mv=? WHERE date=? AND code=?",
            plan_score)
        n_c = cur.rowcount
        conn.commit()
        print("\n✅ 已提交: snapshot_bid=%d  stock_float_mv_daily=%d  stock_score_daily=%d"
              % (n_a, n_b, n_c))
        print("   备份: %s" % bak)
    except Exception as e:                                        # noqa: BLE001
        conn.rollback()
        print("\n❌ 写库失败已回滚: %s" % e)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
