#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""选股闸门 —— 目标机**只读**预检探针(不改任何状态, 不启服务)。

用法(在被测机 backend 目录下):
    PYTHONPATH=/opt/<x>/backend <venv>/bin/python /tmp/_probe_pg.py

覆盖 4 项:
  [1] 时间维边界(9:00/9:25:59 拦, 8:59/9:26 后放)
  [2] 快照维(查**真库** snapshot_bid) —— 当日未落库时 9:26 后应返回 SNAP 文案
  [3] 端点接线证据(源码级: blocked 响应字段 + 闸门位置在 pipeline 之前)
  [4] 模块可导入 + 开关当前取值
"""
import calendar
import io
import re
import sys

FAILS = []


def bj(y, mo, d, H, M, S=0):
    """北京时间墙钟 → epoch(_ts 期望 UTC 秒)"""
    return calendar.timegm((y, mo, d, H, M, S, 0, 0, 0)) - 8 * 3600


def check(name, got, want):
    ok = (got == want)
    print("  %-48s %s" % (name, "PASS" if ok else "*** FAIL ***"))
    if not ok:
        print("        got  = %r" % (got,))
        print("        want = %r" % (want,))
        FAILS.append(name)


def main():
    from app.services.picker import mode as M
    from app.services import auction_snapshot as A
    from app.api import stocks as S

    print("=" * 72)
    print("[0] 常量与开关")
    print("    T_PICK_BLOCK_FROM = %r (9:00 => 540)" % M.T_PICK_BLOCK_FROM)
    print("    T_PICK_OPEN       = %r (9:26 => 566)" % M.T_PICK_OPEN)
    print("    MSG_TIME = %r" % M.PICK_BLOCK_MSG_TIME)
    print("    MSG_SNAP = %r" % M.PICK_BLOCK_MSG_SNAP)
    check("T_PICK_BLOCK_FROM == 540", M.T_PICK_BLOCK_FROM, 540)
    check("T_PICK_OPEN == 566", M.T_PICK_OPEN, 566)

    print("=" * 72)
    print("[1] 时间维边界 —— 交易日 2026-09-17(周四)")
    check("08:59 放行", S._pick_blocked_reason(bj(2026, 9, 17, 8, 59)), None)
    check("09:00 拦截", S._pick_blocked_reason(bj(2026, 9, 17, 9, 0)), M.PICK_BLOCK_MSG_TIME)
    check("09:10 拦截", S._pick_blocked_reason(bj(2026, 9, 17, 9, 10)), M.PICK_BLOCK_MSG_TIME)
    check("09:15 拦截", S._pick_blocked_reason(bj(2026, 9, 17, 9, 15)), M.PICK_BLOCK_MSG_TIME)
    check("09:25 拦截", S._pick_blocked_reason(bj(2026, 9, 17, 9, 25)), M.PICK_BLOCK_MSG_TIME)
    check("09:25:59 仍拦截", S._pick_blocked_reason(bj(2026, 9, 17, 9, 25, 59)), M.PICK_BLOCK_MSG_TIME)

    print("=" * 72)
    print("[2] 快照维 —— 查真库 snapshot_bid")
    print("    has_today_snapshot('2026-09-16') = %r" % A.has_today_snapshot("2026-09-16"))
    print("    has_today_snapshot('2026-09-17') = %r (今晚应为 False, 明早 9:25 落库后变 True)"
          % A.has_today_snapshot("2026-09-17"))
    # 关键: 9:26 后若当日定格未落库 → 必须返回 SNAP 文案(证明第二道闸门真在生效)
    check("09:26:00 当日未落库 => 快照维拦截",
          S._pick_blocked_reason(bj(2026, 9, 17, 9, 26)), M.PICK_BLOCK_MSG_SNAP)
    check("10:30 当日未落库 => 快照维拦截",
          S._pick_blocked_reason(bj(2026, 9, 17, 10, 30)), M.PICK_BLOCK_MSG_SNAP)
    # 已落库的日期 → 9:26 后应放行
    check("09:26:00 定格已落库 => 放行(用 9/16 验证)",
          S._pick_blocked_reason(bj(2026, 9, 16, 9, 26)), None)

    print("=" * 72)
    print("[3] 非交易日一律放行")
    check("周六 09:10 放行", S._pick_blocked_reason(bj(2026, 9, 19, 9, 10)), None)
    check("周日 09:10 放行", S._pick_blocked_reason(bj(2026, 9, 20, 9, 10)), None)

    print("=" * 72)
    print("[4] 端点接线证据(源码级)")
    src = io.open(S.__file__, "r", encoding="utf-8").read()
    check('源码含 blocked 响应字段', '"blocked": True' in src, True)
    check('源码含 blockedUntil 字段', '"blockedUntil": "09:26"' in src, True)
    check('源码调用 _pick_blocked_reason', "block_msg = _pick_blocked_reason()" in src, True)
    ip = src.find("block_msg = _pick_blocked_reason()")
    ipipe = src.find("_run_new_pipeline", ip)
    print("    闸门位置 offset=%d, 其后首次出现 _run_new_pipeline offset=%d" % (ip, ipipe))
    check("闸门在 pipeline 之前", ip > 0 and ipipe > ip, True)

    print("=" * 72)
    print("[5] 开关当前取值")
    from app.services import settings as ST
    v = ST.get("pick_window_guard", 1)
    print("    settings.get('pick_window_guard', 1) = %r  →  %s"
          % (v, "开启(拦截)" if int(v or 0) else "关闭(不拦)"))
    check("开关有效值=1(开启)", int(v or 0), 1)

    print("=" * 72)
    if FAILS:
        print("RESULT: FAIL (%d 项) -> %s" % (len(FAILS), FAILS))
        sys.exit(2)
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
