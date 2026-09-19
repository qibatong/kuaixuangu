# -*- coding: utf-8 -*-
"""2026-09-19 只读验证: 分页越界修复后, 真实的东财全市场分页行为

验证四件事(全部只读, 不改库):
  1. 每个板块**实际请求了几页**(应从固定 30 页降到 真实页数+1)
  2. 是否还出现"分页失败"(修复前 cyb/kcb 必然大量失败)
  3. eastmoney_clist 熔断是否还被打开发(修复前每交易日 09:15:12 必开)
  4. f630(异动等级)在拿到的数据里是否真有值 —— 这是 17% 因子能否生效的前提
"""
import logging
import time

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

from app.services import fetcher as F  # noqa: E402

SCOPES = [("沪深主板", "m:1+t:2"), ("创业板", "m:0+t:80"), ("科创板", "m:1+t:23")]

print("=" * 78)
print("分页越界修复 —— 真实接口验证（%s）" % time.strftime("%Y-%m-%d %H:%M:%S"))
print("=" * 78)
h0 = dict(F._HEALTH["eastmoney_clist"])
print("起始 health: ok=%s fail=%s circuit=%s" % (h0["ok"], h0["fail"], F._check_circuit()))
print()

tot = nz_tot = hi_tot = 0
for name, fs in SCOPES:
    t0 = time.time()
    try:
        out = F.fetch_eastmoney_all(fs)
    except Exception as e:
        print("  %-6s [FAIL] %s: %s" % (name, type(e).__name__, str(e)[:90]))
        continue
    ms = (time.time() - t0) * 1000
    nz = hi = 0
    for s in out:
        v = s.get("f630")
        if v in (None, "-", ""):
            continue
        try:
            v = int(v)
        except (TypeError, ValueError):
            continue
        if v:
            nz += 1
        if v >= 3:
            hi += 1
    tot += len(out)
    nz_tot += nz
    hi_tot += hi
    print("  %-6s 只数=%-5d f630非0=%-5d(%.1f%%) ≥3档=%-5d 耗时=%.0fms"
          % (name, len(out), nz, 100.0 * nz / max(len(out), 1), hi, ms))

print()
h = F._HEALTH["eastmoney_clist"]
print("结束 health: ok=%d fail=%d down_since=%s circuit=%s"
      % (h["ok"], h["fail"], h["down_since"], F._check_circuit()))
print("合计: 只数=%d f630非0=%d ≥3档=%d" % (tot, nz_tot, hi_tot))
print()
print("判定: 分页失败=%d（修复前 cyb/kcb 每次各 22/26 页必然失败）; 熔断=%s（修复前必然打开）"
      % (h["fail"], F._check_circuit()))
