
import sys
sys.path.insert(0, '/opt/kuaixuan/aipick/scripts')
import predict_daily as p
p.predict(trade_date="2026-08-28", force=True)
# 验证
import json, os
fp = os.path.join(p.OUT_DIR, "predictions_2026-08-28.json")
d = json.load(open(fp))
print("after fix:", "date=", d.get("date"), "count=", d.get("count"),
      "top.len=", len(d.get("top",[])), "all.len=", len(d.get("all",[])))
# 首行
r = d.get("top", [])[0]
print("  top1: ", r.get("code"), r.get("name"), "prob=", r.get("prob"), "ai_prob=", r.get("ai_prob"),
      "bc=", r.get("bid_change"), "turnover=", r.get("bid_turnover"))
# 再看一个今天有涨停嫌疑的 600479(千金药业)
for r in d.get("top", []) + d.get("all", []):
    if str(r.get("code"))=="600479":
        print("  600479 千金: prob=", r.get("prob"),"ai_prob=", r.get("ai_prob"),
              "bc=", r.get("bid_change"), "amt=", r.get("bid_amount"), "td=", r.get("trade_date"))
        break
