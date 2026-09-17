# -*- coding: utf-8 -*-
"""按 settings.set 语义写入本机开关(v4.11.27 测试机同步用)。

🔴 必须走 settings.set(而非裸 SQL UPDATE): 读侧是 `json.loads(row[0])`,
   裸写字符串会让 json 解析失败 → **静默回退默认值**(曾踩: token 类键)。
   走 settings.set 则写的是 json.dumps 后的形态, 读侧永远能解析。

用法: <venv>/bin/python /tmp/_set_switch_v41127.py [key=value ...]
      不带参数 = 用下面的 DEFAULTS
"""
import sys

BE = "/opt/kuaixuan/backend"
if BE not in sys.path:
    sys.path.insert(0, BE)

from app.services import settings as st                                # noqa: E402

# 明早(2026-09-18 周五)要在测试机验**新闸门口径**, 所以必须显式把开关打开
# (测试机此前没有这个键 → 靠代码默认值 1 在跑, 属于"隐式开启", 不可控)。
DEFAULTS = [
    ("pick_window_guard", 1),
]


def parse(argv):
    out = []
    for a in argv:
        if "=" not in a:
            print("跳过非法参数(需 key=value): %s" % a)
            continue
        k, v = a.split("=", 1)
        try:
            v = int(v)
        except ValueError:
            if v.lower() in ("true", "false"):
                v = (v.lower() == "true")
        out.append((k.strip(), v))
    return out or DEFAULTS


def main():
    for k, v in parse(sys.argv[1:]):
        old = st.get(k)
        ok = st.set(k, v)
        new = st.get(k)
        flag = "OK " if (ok and new == v) else "FAIL"
        print("%s %-24s %r -> %r   (set_ok=%s)" % (flag, k, old, new, ok))
        if not (ok and new == v):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
