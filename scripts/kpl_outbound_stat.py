# -*- coding: utf-8 -*-
"""竞价窗口「上游出网 + 接口耗时」统计（2026-10-01 竞价链路 P1-5 / 清单 §六 验收口径）

**纯日志解析，不改代码、不连网**。用法（在机器上跑，读 journalctl 的 stdin）：

    journalctl -u kuaixuan --since "2026-10-08 09:15" --until "2026-10-08 09:28" --no-pager \\
      | python3 /opt/kuaixuan/scripts/_kx_kpl_outbound_stat.py

    # 也可以喂日志文件：python3 _kx_kpl_outbound_stat.py < /opt/kuaixuan/logs/app.log

统计两层（正好对应清单 §六 要的三项：**上游调用次数 / 429 条数 / P95 耗时**）：

  ① 接口层 —— 应用自带的结构化访问日志
       `[app.main] <ip> <METHOD> <path> uid=N <status> <ms>ms`
     ⇒ 每个 path 的请求数 / p50 / p95 / max / 非 200 明细（重点看 504）
  ② 上游层 —— 开盘啦出网埋点（`_call` 唯一出网口，60s 汇总一行）
       `KPL出网统计 60s: total=N fail=N http429=N | a=GetStockIDPlate|Type=2=N ...`
     ⇒ 出网总次数按接口聚合 + 失败 + 429

退出码: 0 = 无告警; 1 = 有告警(429>0 / 5xx>0 / p95 超阈值) —— 可直接接 CI/巡检。
"""
import collections
import re
import statistics
import sys

RE_ACCESS = re.compile(
    r"\[app\.main\]\s+\S+\s+(\w+)\s+(\S+?)(?:\?\S*)?\s+uid=\S+\s+(\d{3})\s+(\d+)ms")
RE_UP = re.compile(
    r"KPL出网统计\s+([\d.]+)s:\s+total=(\d+)\s+fail=(\d+)\s+http429=(\d+)\s*\|\s*(.*)")
RE_UP_ITEM = re.compile(r"([^=\s]+)=(\d+)(?:\(fail(\d+)\))?")

# 告警阈值（10-08 验收: 竞价窗口内 P95 超过它 / 出现 429 / 5xx 就该有人看）
P95_WARN_MS = 3000


def _pct(vals, q):
    if not vals:
        return 0
    vals = sorted(vals)
    i = min(len(vals) - 1, int(round(q * (len(vals) - 1))))
    return vals[i]


def main():
    ms_by_path = collections.defaultdict(list)
    st_by_path = collections.defaultdict(collections.Counter)
    up_calls = collections.Counter()
    up_fail = collections.Counter()
    tot = [0, 0, 0]          # [上游总调用, 上游失败, 429]
    up_windows = 0

    for line in sys.stdin:
        m = RE_ACCESS.search(line)
        if m:
            path, st, ms = m.group(2), int(m.group(3)), int(m.group(4))
            ms_by_path[path].append(ms)
            st_by_path[path][st] += 1
            continue
        m = RE_UP.search(line)
        if m:
            up_windows += 1
            tot[0] += int(m.group(2))
            tot[1] += int(m.group(3))
            tot[2] += int(m.group(4))
            for name, c, f in RE_UP_ITEM.findall(m.group(5)):
                up_calls[name] += int(c)
                if f:
                    up_fail[name] += int(f)

    print("=" * 78)
    print("① 接口层（应用访问日志）")
    print("=" * 78)
    if not ms_by_path:
        print("  （窗口内没有访问日志）")
    else:
        print("  %-46s %6s %8s %8s %8s %s" % ("path", "请求", "p50", "p95", "max", "非200"))
        for path, vals in sorted(ms_by_path.items(), key=lambda kv: -len(kv[1])):
            bad = {k: v for k, v in st_by_path[path].items() if k >= 400}
            print("  %-46s %6d %7dms %7dms %7dms %s"
                  % (path[:46], len(vals), _pct(vals, 0.5), _pct(vals, 0.95),
                     max(vals), bad or "-"))

    print()
    print("=" * 78)
    print("② 上游层（开盘啦出网埋点, %d 个统计窗口）" % up_windows)
    print("=" * 78)
    if not up_calls:
        print("  （窗口内没有出网统计行；若确实有请求，检查 KX_KPL_OUTBOUND_STAT 是否被设为 0）")
    else:
        print("  %-44s %8s %8s" % ("开盘啦接口(a=|Type=)", "调用", "失败"))
        for name, c in up_calls.most_common():
            print("  %-44s %8d %8s" % (name[:44], c, up_fail.get(name, 0) or "-"))
        print("  " + "-" * 62)
        print("  合计: 调用 %d / 失败 %d / **429 %d**" % (tot[0], tot[1], tot[2]))

    # ---- 告警 ----
    warns = []
    if tot[2]:
        warns.append("上游 429 %d 次（配额被打满/限流）" % tot[2])
    if tot[1] and not tot[2]:
        warns.append("上游失败 %d 次（非 429，查日志 err=）" % tot[1])
    for path, vals in ms_by_path.items():
        p95 = _pct(vals, 0.95)
        if p95 > P95_WARN_MS:
            warns.append("%s p95=%dms(>%dms)" % (path, p95, P95_WARN_MS))
        bad5 = st_by_path[path].get(504, 0) + st_by_path[path].get(500, 0)
        if bad5:
            warns.append("%s 出现 %d 个 5xx" % (path, bad5))
    print()
    if warns:
        print("⚠️ 告警 %d 条:" % len(warns))
        for w in warns:
            print("   · " + w)
        return 1
    print("✅ 无告警（429=0 / 无 5xx / p95 均在 %dms 内）" % P95_WARN_MS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
