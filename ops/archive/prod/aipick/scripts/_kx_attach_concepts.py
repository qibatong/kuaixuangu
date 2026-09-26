# -*- coding: utf-8 -*-
"""生产机: 给历史 predictions json 附加 concepts(仅注入字段, 不重算预测)。
遍历 /opt/kuaixuan/aipick/output/predictions_*.json, 对每行(top/all/result/rows 可能的数组)
附加 concepts(全量概念数组) 与 concept(前2), 用 predict_daily._concept_map() 从 stock_concept.board_full 读取。"""
import os, sys, json, glob

sys.path.insert(0, "/opt/kuaixuan/aipick/scripts")
import predict_daily  # noqa E402

OUT_DIR = "/opt/kuaixuan/aipick/output"
cmap = predict_daily._concept_map()
print(f"概念库载入: {len(cmap)} 只", flush=True)

ARRAY_KEYS = ("top", "all", "result", "rows")


def attach_row(r):
    if not isinstance(r, dict):
        return r
    cl = cmap.get(str(r.get("code", "")))
    r["concepts"] = cl or []
    if not r.get("concept") and cl:
        r["concept"] = "、".join(cl[:2])
    return r


for jp in sorted(glob.glob(os.path.join(OUT_DIR, "predictions_*.json"))):
    try:
        with open(jp, "r", encoding="utf-8") as f:
            cur = json.load(f)
    except Exception as e:
        print(f"跳过 {os.path.basename(jp)}: {e}", flush=True)
        continue
    changed = False
    for k in ARRAY_KEYS:
        arr = cur.get(k)
        if isinstance(arr, list) and arr:
            cur[k] = [attach_row(r) for r in arr]
            changed = True
    if changed:
        with open(jp, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=None, separators=(",", ":"))
        print(f"已附加 concepts → {os.path.basename(jp)}", flush=True)
    else:
        print(f"无内容变更 → {os.path.basename(jp)}", flush=True)

print("done")