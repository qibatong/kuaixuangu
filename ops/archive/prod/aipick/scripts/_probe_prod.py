
import json, glob, os
TARGET_NAME = "康盛股份"
OUT = "/opt/kuaixuan/aipick/output"
# 遍历所有日期, 报告康盛股份的 ai_prob
for jp in sorted(glob.glob(OUT + "/predictions_*.json")):
    d = json.load(open(jp))
    found = None
    for arr in (d.get("all"), d.get("top"), d.get("result")):
        if isinstance(arr, list):
            for r in arr:
                if r.get("name") == TARGET_NAME or r.get("code") == "002418":
                    if not found or (d.get("all") is arr):
                        found = r
            if found:
                break
    fname = os.path.basename(jp)
    if found:
        print(fname, "| code=", found.get("code"), "| name=", found.get("name"),
              "| ai_prob*100=", round(float(found.get("ai_prob") or 0)*100, 2),
              "| mv=", found.get("circ_mv"), "| bid_chg=", found.get("bid_change"),
              "| bid_amt=", found.get("bid_amount"),
              "| bid_turnover=", found.get("bid_turnover"))
    else:
        print(fname, "| NO_MATCH")
