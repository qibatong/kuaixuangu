# -*- coding: utf-8 -*-
"""前端规范闸门（对齐 docs/前端视觉规范审计-20261004.md）

四层检查：
  A. 值域层：字号落在规范档位内且 ≥12px；字重 400/600/700；圆角 4 档；
     间距 4px 栅格；行高 1.2/1.5；<style> 段无裸 hex。
  B. 字号档数层：本页**只允许 2 档**（主人要求「不超过 3 种，最好 2 种」）——
     全页字号取值只能是 --fs-body / --fs-strong，且除白名单外一律内容档。
  C. 令牌层：所有 var(--x) 必须能找到定义 —— 防「用了没定义的令牌」静默失效
     （曾踩过：--fs-3xl 未定义，字号悄悄回落，肉眼看不出来）。
  D. 角色层：白名单选择器必须用重点档，其余必须内容档。

用法: python3 style_guard_css.py <生成脚本.py>
"""
import re
import sys

SRC = sys.argv[1] if len(sys.argv) > 1 else "/tmp/cz_preview2_gen.py"
text = open(SRC, encoding="utf-8").read()
m = re.search(r'CSS = r"""(.*?)"""', text, re.S)
if not m:
    print("找不到 CSS 段")
    sys.exit(2)
css = m.group(1)
CUT = r':root\{.*?\}|body\[data-bg="light"\]\{.*?\}'
root_blocks = re.findall(CUT, css, re.S)
body = css
for b in root_blocks:
    body = body.replace(b, "")


def strip_media(s):
    out, i = [], 0
    while i < len(s):
        j = s.find("@media", i)
        if j < 0:
            out.append(s[i:])
            break
        out.append(s[i:j])
        k = s.find("{", j)
        depth, p = 1, k + 1
        while p < len(s) and depth:
            depth += {"{": 1, "}": -1}.get(s[p], 0)
            p += 1
        i = p
    return "".join(out)


body = strip_media(body)
body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)   # 剥注释: 说明里提到色号不算裸色
rules = {}
for blk in body.split("}"):
    if "{" not in blk:
        continue
    sel, decls = blk.split("{", 1)
    sel = sel.split("\n")[-1].strip()
    fs = re.search(r"font-size:\s*([^;]+)", decls)
    if fs:
        rules[sel] = fs.group(1).strip()

bad = []
VALUE_FS = {"--fs-body", "--fs-strong"}          # 本页只启用这两档
STRONG_ALLOW = [".sec-t", ".fold-t", ".emo-val", ".idx-item .num", ".kpi .v", ".mcard .v"]

# ---------- A. 值域层 ----------
for v in re.findall(r"font-size:\s*([^;}]+)", body):
    v = v.strip()
    if re.match(r"^\d+(\.\d+)?px$", v):
        bad.append("[值域] 字号用 px：font-size:%s（规范：全部 rem）" % v)
    elif v.endswith("rem"):
        r = float(v[:-3])
        if r < 0.75:
            bad.append("[值域] 字号 %s < 12px 下限" % v)
        if r not in (0.75, 0.8125, 0.875, 0.9375, 1.0, 1.125, 1.25, 1.5, 1.75, 2.875):
            bad.append("[值域] 字号 %s 不在规范档位内" % v)
    elif not v.startswith("var(--fs-"):
        bad.append("[值域] 字号取值异常：%s" % v)

for v in re.findall(r"font-weight:\s*(\d+)", body):
    if v not in ("400", "600", "700"):
        bad.append("[值域] 字重 %s 越档（只允许 400/600/700）" % v)

for v in re.findall(r"border-radius:\s*([^;}]+)", body):
    v = v.strip()
    if v.startswith("var(--r-") or v == "50%":
        continue
    if any(p not in ("0",) for p in v.split()):
        bad.append("[值域] 圆角 %s 不在 4 档（--r-sm/md/lg/pill）" % v)

for prop in ("padding", "margin", "gap", "row-gap", "column-gap"):
    for v in re.findall(r"(?<![\w-])%s:\s*([^;}]+)" % prop, body):
        # 只挑字面 px 值来判栅格；var(--s2)/calc(...) 由令牌保证，不在此展开
        for num in re.findall(r"(?<![\w.-])(\d+(?:\.\d+)?)px", v):
            n = float(num)
            if n in (1, 2):
                continue  # 徽章/胶囊内部微距，主站同样用法
            if n % 4 != 0:
                bad.append("[值域] %s 非 4px 栅格：%s" % (prop, v))
                break

for v in re.findall(r"line-height:\s*([^;}]+)", body):
    if v.strip() not in ("1.2", "1.5", "1", "inherit"):
        bad.append("[值域] 行高 %s 不属 1.2/1.5 两档" % v.strip())

for h in set(re.findall(r"#[0-9a-fA-F]{3,8}\b", body)):
    if h.lower() not in ("#fff", "#ffffff", "#000", "#000000"):
        bad.append("[值域] 裸色 %s 未收敛进 token" % h)

# ---------- B. 字号档数层（全页 ≤2 档，含 BODY 内联样式） ----------
def bare(t):
    return t.replace("var(", "").replace(")", "").strip()


all_fs = [bare(v) for v in re.findall(r'font-size:\s*([^;"\'}]*)', text)]
nonstd = sorted({v for v in all_fs if v not in VALUE_FS})
if nonstd:
    bad.append("[档数] 全文出现非两档字号：%s（只允许 %s）"
               % (", ".join(nonstd), " / ".join(sorted(VALUE_FS))))

# ---------- C. 令牌层：引用的 token 必须有定义 ----------
defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", css))
used = set(re.findall(r"var\((--[a-z0-9-]+)", text))
missing = sorted(used - defined)
if missing:
    bad.append("[令牌] 引用了未定义的令牌（会静默失效）：%s" % ", ".join(missing))

# ---------- D. 角色层 ----------
for sel, fs in sorted(rules.items()):
    if sel in STRONG_ALLOW:
        if fs != "var(--fs-strong)":
            bad.append("[角色] %s 属重点档（区块标题/关键数字），实际 %s" % (sel, fs))
    elif fs != "var(--fs-body)":
        bad.append("[角色] %s 应属内容档 var(--fs-body)，实际 %s" % (sel, fs))
for s in STRONG_ALLOW:
    if s not in rules:
        bad.append("[角色] 重点档选择器未声明字号：%s" % s)

print("=== 规范闸门：%s ===" % SRC)
print("\n-- 本页字号令牌 --")
for t in sorted(VALUE_FS):
    d = re.search(r"%s\s*:\s*([^;]+)" % t, css)
    print("   %s = %s" % (t, d.group(1) if d else "未定义!"))
print("\n-- 重点档（18px）--")
print("   %s" % ", ".join(".".join(s.split()) for s in STRONG_ALLOW))
print("-- 其余 %d 个声明均为内容档（13px）--" % len([s for s in rules if s not in STRONG_ALLOW]))
print()
if bad:
    for b in sorted(set(bad)):
        print("  ✗", b)
    print("\n共 %d 项不合规" % len(set(bad)))
    sys.exit(1)
print("  ✓ 值域 / 档数≤2 / 令牌 / 角色 全部通过")
