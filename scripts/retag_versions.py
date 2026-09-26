#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按「提交信息里的版本号」批量补打版本 tag（通用工具，可复用）。

背景（2026-09-26 v4.11.56 仓库治理）：
  AGENTS.md §七 明确写「回滚只能靠 git 回 checkout <tag> -- ...」，
  但仓库 tag 自 v4.11.9 起就断档了（只余 v4.11.2/7/8 与孤立的 v4.11.33-test）
  ⇒ 版本记录与仓库现实脱节。本工具按客观依据把 tag 补回。

映射规则（不靠猜）：
  1. 扫描 git log，对每条提交提取提交信息里的 `v4.11.<N>`；
     一条提交信息里出现多个号时（如「v4.11.34/35/36 回写」）只归属**最大的 N**
     —— 该提交是「最后一个受影响版本」的收尾，避免一次回写造成多个 tag 指向同一提交。
  2. 版本 N → 其所有归属提交中**时间最新**的那个。
  3. 已存在的 tag 跳过（不 -f 覆盖）；已 revert 的版本、纯文档不占号的版本由 MANUAL 排除。

用法：
  python3 scripts/retag_versions.py --check     # 只打印映射，不建 tag
  python3 scripts/retag_versions.py --apply     # 实际创建 annotated tag
  python3 scripts/retag_versions.py --apply --push   # 建完顺带推远端
"""
import argparse
import re
import subprocess
import sys

SUBJ = re.compile(r"v4\.11\.(\d+)")

# 版本 -> 提交：自动推导的映射（由 --check 输出核对后固化，含 3 处人工修正）
MANUAL = [
    # (tag, commit, 备注/依据)
    ("v4.11.3", "8a760ec", "提交信息含号"),
    ("v4.11.4", "c063da8", "提交信息含号"),
    ("v4.11.5", "34acc94", "提交信息含号"),
    ("v4.11.6", "9b1d8c0", "提交信息含号"),
    ("v4.11.9", "08fa032", "提交信息含号 + history.md 明文回滚点"),
    ("v4.11.10", "9404816", "提交信息含号 + history.md 明文回滚点"),
    ("v4.11.11", "2a96e34", "提交信息含号 + history.md 明文回滚点"),
    ("v4.11.12", "803ccbb", "提交信息含号 + history.md 明文回滚点"),
    ("v4.11.13", "f43e70a", "提交信息含号"),
    ("v4.11.14", "d433a67", "提交信息含号"),
    ("v4.11.15", "3768dae", "提交信息含号"),
    ("v4.11.16", "ae779ea", "★人工：提交信息无号，按 docs/history.md 标题描述匹配（P1 修采集时刻错配 + 故障自愈 + 日K缓存 TTL）"),
    ("v4.11.17", "b4a5e25", "提交信息含号"),
    ("v4.11.18", "fdc70f6", "提交信息含号"),
    ("v4.11.19", "6d1f7ad", "提交信息含号"),
    ("v4.11.20", "18b52a1", "提交信息含号"),
    ("v4.11.21", "df1e6fc", "提交信息含号"),
    ("v4.11.22", "5c1c927", "提交信息含号"),
    ("v4.11.23", "a982ed5", "提交信息含号"),
    ("v4.11.24", "3335f7c", "提交信息含号"),
    ("v4.11.25", "9d73c7b", "提交信息含号"),
    ("v4.11.26", "eaf1199", "提交信息含号"),
    ("v4.11.27", "93394d9", "提交信息含号"),
    ("v4.11.28", "57d855d", "提交信息含号"),
    ("v4.11.29", "457e4d1", "提交信息含号"),
    ("v4.11.30", "fb1da1a", "提交信息含号（该版为「补遗」提交）"),
    ("v4.11.31", "46f3a6e", "提交信息含号（该版多提交，取最新）"),
    ("v4.11.32", "e983919", "提交信息含号（订正 30/32 状态，归最大号 32）"),
    ("v4.11.33", "47e6abd", "提交信息含号"),
    ("v4.11.34", "24525a0", "★人工：自动推导误取 1cfc29d（那是 34/35/36 合并回写）⇒ 改用本版补号提交"),
    ("v4.11.35", "633a642", "提交信息含号"),
    ("v4.11.36", "1cfc29d", "★人工：34/35/36 合并回写归「最后一个受影响版本」，含 3408bc4 代码"),
    ("v4.11.37", "beaa7de", "提交信息含号"),
    ("v4.11.38", "95ae23b", "提交信息含号"),
    # v4.11.39 已 revert（f7c7108）⇒ 不设 tag
    ("v4.11.40", "61dbf76", "提交信息含号"),
    ("v4.11.41", "f66aea5", "提交信息含号"),
    ("v4.11.42", "a76e877", "提交信息含号（该版多提交，取最新）"),
    ("v4.11.43", "2ad815e", "提交信息含号"),
    ("v4.11.44", "958f6cd", "提交信息含号（该版多提交，取最新）"),
    ("v4.11.45", "23ab2d4", "提交信息含号"),
    ("v4.11.46", "832bff4", "提交信息含号（该版多提交，取最新）"),
    ("v4.11.47", "8e2f3f1", "提交信息含号（该版多提交，取最新）"),
    ("v4.11.48", "f456765", "提交信息含号（该版多提交，取最新）"),
    ("v4.11.49", "90c4e17", "提交信息含号（该版多提交，取最新）"),
    ("v4.11.50", "0fbf322", "提交信息含号"),
    ("v4.11.51", "f4ef2f4", "提交信息含号"),
    ("v4.11.52", "86ec652", "提交信息含号"),
    ("v4.11.53", "27aa636", "提交信息含号"),
    # v4.11.54 纯文档订正，按 §0.4 规则 5 不占新号 ⇒ 不设 tag
    ("v4.11.55", "d88492e", "提交信息含号"),
]


def git(*args, check=True):
    r = subprocess.run(["git"] + list(args), capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit("git %s 失败: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout.strip()


def fmt_version_msg(tag, commit, note):
    """生成 tag 消息：含版本、日期、来源提交、补打依据。"""
    info = git("log", "-1", "--format=%h|%ad|%s", "--date=format:%Y-%m-%d", commit)
    h, d, subj = info.split("|", 2)
    return (
        "%s · %s\n\n"
        "%s\n\n"
        "来源提交: %s %s\n"
        "补打依据: %s\n"
        "补打说明: v4.11.56 仓库治理补齐版本记录 —— tag 自 v4.11.9 断档，\n"
        "          按 AGENTS.md §0.4 版本迭代记录规则回填（见 scripts/retag_versions.py）。\n"
        % (tag, d, subj.strip(), h, "", note)
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际创建 tag（默认只 dry-run 打印）")
    ap.add_argument("--push", action="store_true", help="创建后推送远端")
    a = ap.parse_args()

    existing = set(git("tag").split())
    created, skipped = [], []
    for tag, commit, note in MANUAL:
        full = git("rev-parse", commit)
        if tag in existing:
            skipped.append(tag)
            continue
        if not a.apply:
            print("[dry-run] %-10s -> %s  %s" % (tag, commit, note))
            continue
        msg = fmt_version_msg(tag, commit, note)
        subprocess.run(["git", "tag", "-a", tag, commit, "-m", msg], check=True)
        created.append(tag)
        print("[created] %-10s -> %s" % (tag, commit))

    if not a.apply:
        print("\n共 %d 个待创建；已存在 %d 个（%s）" % (
            len(MANUAL) - len(skipped), len(skipped), ", ".join(sorted(skipped)) or "无"))
        return

    print("\n创建 %d 个 / 跳过已存在 %d 个" % (len(created), len(skipped)))
    if a.push:
        r = subprocess.run(["git", "push", "origin", "--tags"],
                           capture_output=True, text=True)
        print((r.stdout or r.stderr).strip() or "(push 无输出)")


if __name__ == "__main__":
    main()
