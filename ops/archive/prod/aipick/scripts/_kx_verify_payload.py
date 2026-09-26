# -*- coding: utf-8 -*-
import json, glob
jps = sorted(glob.glob("/opt/kuaixuan/aipick/output/predictions_*.json"))
jp = jps[-1]
d = json.load(open(jp))
print("file:", jp.split("/")[-1], "| 概念库条数影响前:", "-")
arr = d.get("all") or d.get("top") or []
print("rows:", len(arr))
for r in arr[:4]:
    print(r["code"], r["name"], "| concept=", r.get("concept"), "| concepts_n=", len(r.get("concepts") or []))
# 统计有多少行有全量概念数组
n_full = sum(1 for r in arr if isinstance(r.get("concepts"), list) and r["concepts"])
print(f"有全量概念数组的行: {n_full}/{len(arr)}")
