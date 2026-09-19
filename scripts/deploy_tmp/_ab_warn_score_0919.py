# -*- coding: utf-8 -*-
"""2026-09-19 只读 A/B: 同一只票补上 f630(warn_type) 后评分抬多少分
—— 用来预测"周一 9:25 定格带上 f630 后, scoreFloor=80 能否重新有名单"。
"""
import sqlite3

from app.services.picker.contract import QuoteRow
from app.services.picker import score as S

c = sqlite3.connect("/opt/kuaixuan/kuaixuan.db")
c.row_factory = sqlite3.Row

CODES = ["001216", "002584", "300434", "000920", "002339"]
print("=" * 78)
print("A/B: warn_type(异动等级) 对评分的贡献 —— 9/18 定格全员 warn=0, 故当前天花板偏低")
print("=" * 78)

for code in CODES:
    r = c.execute("SELECT * FROM snapshot_bid WHERE date='2026-09-18' AND time_point='9_25' "
                  "AND code=?", (code,)).fetchone()
    if not r:
        print("  %s 不在 9/18 定格里" % code)
        continue
    keys = r.keys()
    row = QuoteRow(
        code=code, name=r["name"],
        bid_change=r["bid_change"] if "bid_change" in keys else None,
        bid_amt=r["bid_amt"] if "bid_amt" in keys else None,
        bid_vol=r["bid_vol"] if "bid_vol" in keys else None,
        float_mv=r["float_mv"] if "float_mv" in keys else None,
        yesterday_change=r["yday_chg"] if "yday_chg" in keys else None,
        price=r["auction_price"] if "auction_price" in keys else None,
    )
    line = "  %-8s %-8s" % (code, r["name"])
    for wt in (None, 0, 1, 2, 3, 4, 5):
        row.warn_type = wt
        try:
            sr = S.compute_score(row)
            line += "  wt=%-4s:%3d" % ("None" if wt is None else wt, sr.probability)
        except Exception as e:
            line += "  wt=%-4s:ERR" % wt
    print(line)

print()
r = c.execute("SELECT * FROM snapshot_bid WHERE date='2026-09-18' AND time_point='9_25' "
              "AND code='001216'").fetchone()
row = QuoteRow(code="001216", name=r["name"], bid_change=r["bid_change"],
               bid_amt=r["bid_amt"], bid_vol=r["bid_vol"], float_mv=r["float_mv"],
               yesterday_change=r["yday_chg"], price=r["auction_price"])
row.warn_type = 0
s0 = S.compute_score(row)
row.warn_type = 4
s4 = S.compute_score(row)
row.warn_type = 5
s5 = S.compute_score(row)
print("  华瓷股份明细: warn=0 → %d 分 / warn=4 → %d 分(+%d) / warn=5 → %d 分(+%d)"
      % (s0.probability, s4.probability, s4.probability - s0.probability,
         s5.probability, s5.probability - s0.probability))
print("  因子构成(warn=0): %s" % s0.parts)
print("  因子构成(warn=4): %s" % s4.parts)
print()
print("  结论: 9/18 定格全员 warn=0 ⇒ 全市场天花板 76 分 < 用户 scoreFloor=80 ⇒ 名单必空;")
print("        补上 f630(warn≥3) 后同票可达 8x 分 ⇒ 周一新定格后名单可恢复。")
c.close()
print("=" * 78)
