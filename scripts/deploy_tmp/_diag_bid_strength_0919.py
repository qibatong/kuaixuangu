# -*- coding: utf-8 -*-
"""只读探针: 验证「竞价强度」能否为 9/18 那份定格补出异动分 (2026-09-19)

背景
--------------------------------------------------------------------------------
测试机 9/18 的快照 warn_type 全 0(东财熔断 + 采集侧 f630 落库代码当时还没上线),
导致 17% 异动因子退化为常数 → 天花板 76 < 主人账号门槛 80 → 名单空。

问题: 能不能**不改门槛**, 改用对东财免疫的「竞价强度」口径把异动分补回来?
本探针只读, 不做任何写操作、不改 settings。检查四件事:
  ① settings.use_bid_strength 当前值
  ② snapshot_bid 各时点数据是否齐(9_25 有量比基准 / 9_24 有加速度基准 / 昨日 9_25)
  ③ 开盘啦抢筹(qcDelta / listLast)对 9/18 是否可查(历史可用性)
  ④ 直接调 bid_strength.load + score_map, 看全市场能算出多少非 None 强度、档位分布
"""
import sys, json, logging

logging.disable(logging.WARNING)
sys.path.insert(0, "/opt/kuaixuan/backend")

from app.services import bid_strength          # noqa: E402
from app.services import settings              # noqa: E402
from app.db import database                    # noqa: E402


def main():
    print("=" * 78)
    print("① settings.use_bid_strength")
    print("=" * 78)
    raw = settings.get("use_bid_strength")
    print("   raw=%r  enabled()=%s" % (raw, bid_strength.enabled()))

    conn = database.get_conn()
    cur = conn.cursor()
    try:
        print()
        print("=" * 78)
        print("② snapshot_bid 时点覆盖(按日)")
        print("=" * 78)
        rows = cur.execute(
            "SELECT date, time_point, COUNT(*) FROM snapshot_bid "
            "WHERE date >= '2026-09-14' GROUP BY date, time_point "
            "ORDER BY date, time_point").fetchall()
        by_date = {}
        for d, tp, n in rows:
            by_date.setdefault(str(d), {})[str(tp)] = n
        for d in sorted(by_date):
            tps = by_date[d]
            print("   %s  %s" % (d, "  ".join("%s=%d" % (k, v) for k, v in sorted(tps.items()))))
        # 9_24 有值率 + 昨日 9_25 竞价额有效率(量比的两个前提)
        print()
        print("   --- 量比/加速度两个前提 ---")
        for d in sorted(by_date):
            n24 = cur.execute(
                "SELECT COUNT(*) FROM snapshot_bid WHERE date=? AND time_point='9_24' "
                "AND bid_change IS NOT NULL", (d,)).fetchone()[0]
            n25 = cur.execute(
                "SELECT COUNT(*) FROM snapshot_bid WHERE date=? AND time_point='9_25' "
                "AND bid_amt IS NOT NULL AND bid_amt > 0", (d,)).fetchone()[0]
            print("   %s  9_24涨幅非空=%4d   9_25竞价额>0=%4d" % (d, n24, n25))

        # warn_type 现状对照
        print()
        print("   --- 定格行 warn_type 现状(证明缺口) ---")
        for d in sorted(by_date):
            r = cur.execute(
                "SELECT COUNT(*), SUM(CASE WHEN warn_type IS NULL OR warn_type=0 THEN 1 ELSE 0 END), "
                "COUNT(DISTINCT warn_type) FROM snapshot_bid WHERE date=? AND time_point='9_25'",
                (d,)).fetchone()
            print("   %s  9_25行=%5d  其中warn=0/空=%5d  取值种数=%s" % (d, r[0], r[1], r[2]))
    finally:
        conn.close()

    print()
    print("=" * 78)
    print("③ 开盘啦抢筹 历史可用性")
    print("=" * 78)
    try:
        from app.services import kpl
        for d in ("2026-09-18", "2026-09-17"):
            try:
                qc = kpl.fetch_bid_qiangcang(d) or {}
                l20 = qc.get("list20") or []
                llast = qc.get("listLast") or []
                lchg = qc.get("list20Chg") or []
                print("   %s  list20=%d  listLast=%d  list20Chg=%d"
                      % (d, len(l20), len(llast), len(lchg)))
                if l20:
                    print("        样例: %s" % json.dumps(l20[0], ensure_ascii=False)[:150])
            except Exception as e:                                # noqa: BLE001
                print("   %s  查询异常: %r" % (d, e))
    except Exception as e:                                        # noqa: BLE001
        print("   kpl 导入失败: %r" % (e,))

    print()
    print("=" * 78)
    print("④ bid_strength.load 全市场实算(不判开关, 直接看数据够不够)")
    print("=" * 78)
    try:
        st = bid_strength.load(None, date="2026-09-18")
        sm = bid_strength.score_map(st)
        got = {c: v for c, v in sm.items() if v is not None}
        print("   载入票数=%d   能算出强度=%d   占比=%.1f%%"
              % (len(st), len(got), 100.0 * len(got) / max(1, len(st))))
        # 三层各自覆盖
        has_vol = sum(1 for s in st.values() if s.bid_vol_ratio is not None)
        has_acc = sum(1 for s in st.values() if s.accel is not None)
        has_qc = sum(1 for s in st.values() if s.qc_delta is not None or s.qc_last)
        print("   量比覆盖=%d   加速度覆盖=%d   抢筹覆盖=%d" % (has_vol, has_acc, has_qc))
        # 档位映射: >=0.85 强(5) / >=0.65 中(4) / >=0.40 弱(3) / 其他 -
        buckets = {"5(强>=.85)": 0, "4(中>=.65)": 0, "3(弱>=.40)": 0, "2(<.40)": 0}
        for v in got.values():
            if v >= 0.85:
                buckets["5(强>=.85)"] += 1
            elif v >= 0.65:
                buckets["4(中>=.65)"] += 1
            elif v >= 0.40:
                buckets["3(弱>=.40)"] += 1
            else:
                buckets["2(<.40)"] += 1
        print("   档位分布: %s" % json.dumps(buckets, ensure_ascii=False))
        # 抽查主人候选池那几只(上轮实验用的)
        print()
        print("   --- 抽查上轮实验的 6 只 ---")
        for c in ("001216", "300434", "000920", "002584", "600603", "603335"):
            s = st.get(c)
            v = sm.get(c)
            if s is None:
                print("   %s  快照里没有" % c)
            else:
                print("   %s  量比=%s  加速=%s  抢筹=%s/%s  → 强度=%s  missing=%s"
                      % (c, s.bid_vol_ratio, s.accel, s.qc_delta, s.qc_last, v, s.missing))
    except Exception as e:                                        # noqa: BLE001
        import traceback
        print("   异常: %r" % (e,))
        traceback.print_exc()

    print()
    print("探针结束(全程只读)")


if __name__ == "__main__":
    main()
