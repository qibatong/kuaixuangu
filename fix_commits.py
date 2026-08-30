#!/usr/bin/env python3
"""一键修复所有无意义的 commit message，然后 force push 到 GitHub。
直接在本地机器运行: python3 fix_commits.py
"""
import subprocess, os, tempfile, sys

# 获取所有 commit (从旧到新)
result = subprocess.run(
    ['git', 'rev-list', '--reverse', 'HEAD'],
    capture_output=True, text=True
)
all_shas = result.stdout.strip().split('\n')
print(f"共 {len(all_shas)} 个 commit")

# 从 git log --stat 获取每个 commit 的实际修改内容
sha_to_msg = {}
for sha in all_shas:
    # 获取该 commit 的改动统计
    r = subprocess.run(
        ['git', 'diff-tree', '--no-commit-id', '-r', '--name-only', sha],
        capture_output=True, text=True
    )
    files = r.stdout.strip().split('\n') if r.stdout.strip() else []
    
    # 旧 message
    r2 = subprocess.run(
        ['git', 'log', '-1', '--format=%s', sha],
        capture_output=True, text=True
    )
    old_msg = r2.stdout.strip()
    
    # 如果不是无意义的 message，保留原 message
    meaningless = ['feat: 分析项目仓库', 'feat: 分析项目', 'feat: analysis']
    if old_msg not in meaningless:
        sha_to_msg[sha] = old_msg
        print(f"  保留: {sha[:7]} {old_msg}")
        continue
    
    # 根据修改的文件推断正确的 commit message
    feat_type = 'feat'
    if any('test' in f.lower() for f in files):
        feat_type = 'test'
    elif any('doc' in f.lower() or f.endswith('.md') for f in files):
        feat_type = 'docs'
    elif any('script' in f.lower() or 'deploy' in f.lower() for f in files):
        feat_type = 'chore'
    elif any('refactor' in f.lower() or 'clean' in f.lower() for f in files):
        feat_type = 'refactor'
    
    # 前端文件
    frontend = [f for f in files if f.startswith('frontend/')]
    backend = [f for f in files if f.startswith('backend/')]
    scripts = [f for f in files if f.startswith('scripts/')]
    other = [f for f in files if f not in frontend + backend + scripts]
    
    # 按模块分组描述
    parts = []
    if len(frontend) > len(backend):
        # 前端改动为主
        comps = set()
        for f in frontend:
            base = os.path.basename(f)
            if 'FilterPanel' in base: comps.add('筛选面板')
            elif 'MedalPanel' in base: comps.add('奖牌区')
            elif 'StockChart' in base: comps.add('股票图表弹窗')
            elif 'StockPool' in base: comps.add('自选股面板')
            elif 'NavBar' in base: comps.add('导航栏')
            elif 'Auction' in base: comps.add('竞价异动')
            elif 'StockView' in base: comps.add('选股视图')
            elif 'main.css' in base: comps.add('全局样式')
            elif 'pool.js' in base: comps.add('自选股store')
            elif 'App.vue' in base: comps.add('App')
            else: comps.add(base.replace('.vue','').replace('.js',''))
        parts.append(f"前端: {', '.join(sorted(comps))}")
    elif backend and not frontend:
        # 后端改动
        svcs = set()
        for f in backend:
            base = os.path.basename(f)
            if 'fetcher' in base: svcs.add('fetcher')
            elif 'stocks.py' in f: svcs.add('stocks API')
            elif 'config' in base: svcs.add('config')
            elif 'scheduler' in base: svcs.add('scheduler')
            else: svcs.add(base)
        parts.append(f"后端: {', '.join(sorted(svcs))}")
    elif scripts:
        parts.append(f"脚本: {', '.join(os.path.basename(s) for s in scripts)}")
    elif len(files) > 50:
        parts.append(f"项目初始化({len(files)}文件)")
    else:
        parts.append(f"{len(files)}个文件")
    
    desc = ' + '.join(parts)
    new_msg = f"{feat_type}: {desc}"
    sha_to_msg[sha] = new_msg
    print(f"  修复: {sha[:7]} {old_msg} → {new_msg}")

# 创建 filter-branch 脚本
filter_script = tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False)
filter_script.write("#!/bin/bash\n")
filter_script.write("case \"$GIT_COMMIT\" in\n")
for sha, msg in sha_to_msg.items():
    filter_script.write(f'  {sha}) echo "{msg}";;\n')
filter_script.write("  *) cat;;\nesac\n")
filter_script.close()
os.chmod(filter_script.name, 0o755)

# 执行 filter-branch
print(f"\n重写 {len(sha_to_msg)} 个 commit messages...")
env = os.environ.copy()
env['FILTER_BRANCH_SQUELCH_WARNING'] = '1'
ret = subprocess.run(
    ['git', 'filter-branch', '-f', '--msg-filter', filter_script.name, '--', '--all'],
    capture_output=True, text=True, timeout=120, env=env
)
if ret.returncode != 0:
    print("ERROR:", ret.stderr[-500:])
    sys.exit(1)

os.unlink(filter_script.name)

# 验证
print("\n验证重写结果:")
subprocess.run(['git', 'log', '--oneline'], cwd='.')

# 询问是否 push
print("\n" + "="*60)
print("本地历史已重写完成！")
print("接下来执行: git push --force origin main")
print("="*60)

# 自动 push
answer = input("\n是否立即 force push 到 GitHub? (y/N): ").strip().lower()
if answer == 'y':
    ret = subprocess.run(['git', 'push', '--force', 'origin', 'main'])
    if ret.returncode == 0:
        print("✅ Force push 成功！")
    else:
        print("❌ Push 失败，请手动执行 git push --force origin main")
else:
    print("稍后手动执行: git push --force origin main")
