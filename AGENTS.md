# 项目代理约定 / Agent 工作规则

本文件的规则在每次工作前必须遵守，尤其是与 git 和代码改动相关的操作。

## 一、Git 硬性规则（必须遵守）

1. **改动代码前，先同步远程代码**
   - 先 `git fetch` / `git pull` 更新到最新远程状态，确保基于最新代码改动，不基于旧代码或过期浅克隆。

2. **合并 / 改写 commit 前，先确认历史 log 是否完整**
   - 先看历史：`git log --oneline --decorate`。
   - 检查是否浅克隆：`git rev-parse --is-shallow-repository`；若为浅克隆，补齐历史：`git fetch --unshallow origin`，确认历史完整后再操作。
   - **绝不**用 squash / reset 等操作把已有的历史提交覆盖或丢弃。
   - 确需合并/改写历史时，必须先创建备份分支或 tag 并推到远程（例如 `backup/**`），保证随时可回滚。

3. 提交信息要如实记录实际改动内容，不用占位符。

## 二、常用命令速查

```bash
# 是否有未提交/浅克隆检查
git status
git rev-parse --is-shallow-repository
git log --oneline --decorate | head -40

# 补全浅克隆历史
git fetch --unshallow origin

# 合并多个 commit 为单个（保留父提交，非覆盖）
git commit-tree <tip>^{tree} -p <parent> -m "<msg>"
# 备份分支（推远程）
git branch backup/<name> <commit> && git push origin refs/heads/backup/<name>
```

## 三、项目简介

快选 Kuaixuan · 竞价 AI 选股系统（A 股竞价/盘中选股）。
本仓库真实历史较深（含 v2.0.1…v2.0.7 版本线，共数百条提交），仓库默认是浅克隆，注意补全历史后再做历史操作。