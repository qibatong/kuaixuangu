# -*- coding: utf-8 -*-
"""
存量回溯: 把系统日志(journald)里的历史请求折算进 usage_daily / login_log
========================================================================
2026-09-22 v4.11.35 配套脚本。**在生产机/测试机上跑**, 本机没有 journald。

背景: 「用户功能使用记录」是本次新上的功能, 上线当天表是空的。而 journald 里
      已经有 08-17 至今的完整请求流水(每行带 uid), 所以可以一次性回溯出历史,
      让功能上线当天就有数据可看。
      ⚠️ journald 默认上限 4G, 已占 3.9G —— **一旦开始丢弃最老日志就回溯不了了**,
         所以这件事越早做越好(主人 2026-09-22 已确认「需要回溯」)。

🔴 口径差异(必须知道, 否则会误判数字) —— **默认只回溯登录**:
    - **登录记录: 口径无歧义, 默认回溯**。一次登录就是一次登录, journald 里
      `/api/login` 的成/败、被顶出、重置密码都有明确事件, 与线上实时记录同口径。
    - **功能使用: 口径冲突, 默认不回溯**(主人 2026-09-22 拍板「改回登录口径」)。
      日志里能数到的只是「**接口请求次数**」, 而主人拍板的口径是「**用户主动操作一次记一次**」;
      日志没有 query string, 无法区分 `/api/stocks` 是用户点了「应用」还是 30 秒轮询
      (实测单日 8 千多条里绝大多数是轮询) ⇒ 回溯值**系统性偏大**,
      与上线后的数字**不可直接比较**, 放在同一张表里会被误读成「点了 N 次」。
      若确有需要, 用 `--with-usage` 显式开启(界面会另行标注口径)。

用法:
    # 预演(只统计不写库, 默认只回溯登录)
    /opt/kuaixuan-venv/bin/python kx_activity_backfill.py --days 30 --dry-run
    # 真写(回溯 30 天, 不含今天)
    /opt/kuaixuan-venv/bin/python kx_activity_backfill.py --days 30
    # 指定范围
    /opt/kuaixuan-venv/bin/python kx_activity_backfill.py --from 2026-08-20 --to 2026-09-21
    # 连功能使用一起回溯(口径为接口请求次数, 与实时记录不可比)
    /opt/kuaixuan-venv/bin/python kx_activity_backfill.py --days 30 --with-usage

幂等: 写入取 MAX(count, 新值) 且按 (uid,date,feature) 聚合 ⇒ 同一批日志跑几遍都不会翻倍。
"""
import argparse
import datetime
import os
import re
import subprocess
import sys
import time

DEFAULT_BACKEND = os.environ.get("KX_BACKEND", "/opt/kuaixuan/backend")
if DEFAULT_BACKEND not in sys.path:
    sys.path.insert(0, DEFAULT_BACKEND)

from app.db import database          # noqa: E402
from app.services import activity    # noqa: E402

# ---------------------------------------------------------------- 路径 → 功能
# 只收「明确由用户主动操作触发」的入口端点。**故意不含**:
#   /api/stocks            —— 无法与 30s 轮询区分(日志没有 query string), 单日 8 千多条
#   /api/kpl/lhb           —— 竞价异动「昨上榜」与市场雷达「龙虎榜」共用, 归属有歧义
#   /api/kpl/index-brief / market-brief / sentiment / yidong-* —— 30s 级轮询, 非用户动作
TARGETS = [
    ("/api/picker/snapshot", "picker"),
    ("/api/aipick/data", "aipick"),
    ("/api/kpl/bid-seal", "auction"),
    ("/api/kpl/bid-boom", "auction"),
    ("/api/kpl/bid-net", "auction"),
    ("/api/kpl/bid-qiangcang", "auction"),
    ("/api/kpl/broken", "auction"),
    ("/api/kpl/yest-zt", "auction"),
    ("/api/kpl/yest-broken", "auction"),
    ("/api/kpl/wpqc", "auction"),
    ("/api/stats/auction-overview", "auction"),
    ("/api/stats/bid-snapshot", "auction"),
    ("/api/kpl/em-concept-rank", "concept"),
    ("/api/kpl/em-board-members", "concept"),
    ("/api/kpl/zt-echelon", "ladder"),
    ("/api/kpl/board-rank", "market"),
    ("/api/kpl/board-stocks", "market"),
    ("/api/kpl/hot-rank", "market"),
    ("/api/history", "history"),
    ("/api/member/overview", "member"),
    ("/api/member/checkin", "member"),
]
# 前缀匹配时按长度降序, 保证 /api/history/query 命中 history 而不是被更短的规则先吃掉
TARGETS.sort(key=lambda x: -len(x[0]))

TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4})")
REQ_RE = re.compile(r"\s(GET|POST)\s+(/api/\S*?)\s+uid=(\d+)\s+(\d{3})(?:\s|$)")
LOGIN_OK_RE = re.compile(r"登录成功 uid=(\d+) user=(\S+) ip=(\S+)")
LOGIN_FAIL_RE = re.compile(r"登录失败 login=(\S+) ip=(\S+)")
LOGIN_RESET_RE = re.compile(r"手机短信找回密码成功 uid=(\d+) user=(\S+)")
LOGIN_KICKED_RE = re.compile(r"账号被顶出访问 \S+ uid=(\d+) ip=(\S+)")


def feature_of(path):
    for p, f in TARGETS:
        if path == p or path.startswith(p):
            return f
    return None


def _epoch(ts_str):
    """journald short-iso 的 `2026-09-21T16:47:51+0000` → epoch(带时区偏移, 无歧义)"""
    try:
        return int(datetime.datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S%z").timestamp())
    except Exception:
        return 0


def _bj_date(epoch):
    return time.strftime("%Y-%m-%d", time.gmtime(epoch + 8 * 3600))


def _day_bounds(date_str):
    """北京日期 → (当日 00:00 的 epoch, 次日 00:00 的 epoch)"""
    t0 = int(datetime.datetime.strptime(date_str, "%Y-%m-%d")
             .replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8))).timestamp())
    return t0, t0 + 86400


def iter_journal(t0, t1):
    """取 [t0, t1) 的系统日志行。
    ★ 用 `--since @<epoch>` 而不是日期字符串: journalctl 的日期串按**服务器本地时区**
      解释, 服务器是 UTC 而口径是北京日期, 用字符串必然错 8 小时。"""
    cmd = ["journalctl", "-u", "kuaixuan", "--no-pager", "-o", "short-iso",
           "--since", "@%d" % t0, "--until", "@%d" % t1]
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        for raw in p.stdout:
            yield raw.decode("utf-8", "replace").rstrip("\n")
        p.wait()
    except FileNotFoundError:
        raise SystemExit("找不到 journalctl(本脚本只能在装有 systemd 的服务器上跑)")


def scan_day(date_str, verbose=False):
    """扫一天 → (usage_rows, login_rows, unmapped 计数)"""
    t0, t1 = _day_bounds(date_str)
    usage = {}   # (uid, feature) -> [count, first_ts, last_ts]
    logins = []  # (uid, login_try, result, ip, epoch)
    unmapped = {}
    n = 0
    for line in iter_journal(t0, t1):
        n += 1
        m = REQ_RE.search(line)
        if m:
            path, uid_s, status = m.group(2), m.group(3), int(m.group(4))
            if status == 401 and "kicked" not in line:
                pass          # 普通未登录请求: 不算使用
            f = feature_of(path)
            uid = int(uid_s)
            if f is None:
                if not path.startswith("/api/admin") and not path.startswith("/api/activity"):
                    unmapped[path] = unmapped.get(path, 0) + 1
                continue
            ts = _epoch(TS_RE.match(line).group(1)) if TS_RE.match(line) else t0
            rec = usage.setdefault((uid, f), [0, ts, ts])
            rec[0] += 1
            rec[1] = min(rec[1], ts or t0)
            rec[2] = max(rec[2], ts or t0)
            continue
        # 登录类(只在登录/退出相关行里出现, 量小)
        ts_m = TS_RE.match(line)
        ts = _epoch(ts_m.group(1)) if ts_m else t0
        m = LOGIN_OK_RE.search(line)
        if m:
            logins.append((int(m.group(1)), m.group(2), "success", m.group(3), ts))
            continue
        m = LOGIN_FAIL_RE.search(line)
        if m:
            logins.append((0, m.group(1), "fail", m.group(2), ts))
            continue
        m = LOGIN_RESET_RE.search(line)
        if m:
            logins.append((int(m.group(1)), m.group(2), "reset", "", ts))
            continue
        m = LOGIN_KICKED_RE.search(line)
        if m:
            logins.append((int(m.group(1)), "", "kicked", m.group(2), ts))

    rows = [(uid, date_str, f, v[0], v[1], v[2]) for (uid, f), v in usage.items()]
    if verbose:
        print("    journal 行数 %d, 命中 %d 人×功能, 登录事件 %d" % (n, len(rows), len(logins)))
        if unmapped:
            top = sorted(unmapped.items(), key=lambda x: -x[1])[:5]
            print("    (未映射路径 Top5: %s)" % ", ".join("%s×%d" % (k, v) for k, v in top))
    return rows, logins


def write_logins(logins, dry_run=False):
    """登录记录用 INSERT(明细表, 没有唯一键), 因此**按 (uid,result,epoch,ip) 去重**
    再插入 —— 否则重跑一次就多一批重复行。"""
    if not logins:
        return 0
    uniq = {}
    for uid, try_, res, ip, ts in logins:
        uniq[(uid, res, ts, ip, try_)] = True
    rows = list(uniq.keys())
    if dry_run:
        return len(rows)
    n = 0
    conn = database.get_conn()
    try:
        for uid, res, ts, ip, try_ in rows:
            dup = conn.execute(
                "SELECT 1 FROM login_log WHERE uid=? AND result=? AND created_at=? AND COALESCE(ip,'')=? LIMIT 1",
                (uid, res, ts, ip or "")).fetchone()
            if dup:
                continue
            conn.execute(
                "INSERT INTO login_log (uid, login_try, result, ip, ua, remember, created_at) "
                "VALUES (?,?,?,?,'',0,?)", (uid, try_, res, ip or "", ts))
            n += 1
        conn.commit()
    finally:
        conn.close()
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="回溯多少天(不含今天), 默认 30")
    ap.add_argument("--from", dest="d_from", default="", help="起始北京日期 YYYY-MM-DD")
    ap.add_argument("--to", dest="d_to", default="", help="结束北京日期 YYYY-MM-DD(含)")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    # 2026-09-22 主人拍板「改回登录口径」: 功能使用**默认不回溯**(接口请求次数口径,
    # 与「主动操作一次记一次」冲突, 混在一张表里会被误读)。
    ap.add_argument("--with-usage", action="store_true",
                    help="连功能使用一起回溯(⚠️ 口径=接口请求次数, 与实时记录不可比)")
    ap.add_argument("--no-logins", action="store_true", help="不回溯登录(配 --with-usage 只回溯使用)")
    args = ap.parse_args()
    do_usage = bool(args.with_usage)
    do_logins = not args.no_logins
    if not do_usage and not do_logins:
        ap.error("--no-logins 与「默认不回溯使用」同时成立 ⇒ 什么都不做; "
                 "若要只回溯使用请加 --with-usage")

    today = _bj_date(int(time.time()))
    if args.d_from and args.d_to:
        d0, d1 = args.d_from, args.d_to
    else:
        d1 = (datetime.datetime.strptime(today, "%Y-%m-%d")
              - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        d0 = (datetime.datetime.strptime(d1, "%Y-%m-%d")
              - datetime.timedelta(days=max(1, args.days) - 1)).strftime("%Y-%m-%d")
    if d1 >= today:
        print("⚠️ 结束日 %s 不早于今天(%s) —— 今天的数据由线上实时记录, 回溯会与它打架。"
              "已自动改为 %s" % (d1, today, (datetime.datetime.strptime(today, "%Y-%m-%d")
                                        - datetime.timedelta(days=1)).strftime("%Y-%m-%d")))
        d1 = (datetime.datetime.strptime(today, "%Y-%m-%d")
              - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    print("=" * 68)
    print("回溯范围: %s ~ %s   库: %s" % (d0, d1, os.environ.get("BID_DB_PATH", "(app 默认)")))
    print("模式: %s" % ("预演(不写库)" if args.dry_run else "写库"))
    print("回溯内容: %s" % (" + ".join(
        (["登录记录"] if do_logins else [])
        + (["功能使用⚠️接口请求次数口径"] if do_usage else [])) or "(无)"))
    if not do_usage:
        print("口径: 只回溯登录(一次登录=一次, 与实时记录同口径); "
              "功能使用不回溯 —— 日志只能数接口请求, 与「主动操作一次记一次」冲突。")
    print("=" * 68)

    cur = datetime.datetime.strptime(d0, "%Y-%m-%d")
    end = datetime.datetime.strptime(d1, "%Y-%m-%d")
    total_rows = total_logins = 0
    t_start = time.time()
    while cur <= end:
        ds = cur.strftime("%Y-%m-%d")
        rows, logins = scan_day(ds, verbose=True)
        wrote = 0
        if do_usage and rows:
            wrote = len(rows) if args.dry_run else activity.upsert_absolute(rows)
        nlogin = 0
        if do_logins and logins:
            nlogin = write_logins(logins, dry_run=args.dry_run)
        total_rows += wrote
        total_logins += nlogin
        print("  %s  使用 %d 行 / 登录 %d 条" % (ds, wrote, nlogin))
        if ds == d0:
            pass
        cur += datetime.timedelta(days=1)

    print("=" * 68)
    print("完成: 登录 %d 条%s, 耗时 %.1fs"
          % (total_logins,
             (", 使用 %d 行(⚠️接口请求次数口径)" % total_rows) if do_usage else "",
             time.time() - t_start))
    if do_logins:
        print("提示: login_log 的历史记录**没有 UA**(日志里没打), 与实时记录的差别仅此一项。")
    if not do_usage:
        print("提示: 功能使用未回溯(口径不同) —— 使用记录只从 2026-09-22 功能上线当天开始有数。")
    print("提示: 回溯只覆盖到 %s; 之后的数据由线上实时记录。" % d1)


if __name__ == "__main__":
    main()
