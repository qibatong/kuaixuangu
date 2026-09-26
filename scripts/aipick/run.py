# -*- coding: utf-8 -*-
"""
AI 竞价选股 - 一键运行入口
用法:
  python run.py init      # 初始化数据库
  python run.py backfill --days 120   # 回补历史数据（首次跑）
  python run.py train     # 训练模型
  python run.py backtest --top-n 5    # 回测
  python run.py predict   # 当日预测（9:25-9:30 用）
  python run.py collect   # 采集当日竞价快照
  python run.py label     # 收盘后打标签

LightGBM 平行链路（2026-09-25 新增，产物隔离到 output/lgb）:
  python run.py train_lgbm              # 训练 LightGBM
  python run.py predict_lgbm            # 用 LightGBM 做当日预测
  python run.py backfill_lgbm --days 30 # 用 LightGBM 补历史报告
"""
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def run(args):
    print(f"\n>>> python {' '.join(args)}\n")
    subprocess.run([PY, os.path.join(SCRIPT_DIR, args[0])] + args[1:], check=False)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    rest = sys.argv[2:]

    if cmd == "init":
        run(["db.py"])
    elif cmd == "backfill":
        run(["backfill.py"] + rest)
    elif cmd == "train":
        run(["train_model.py"])
    elif cmd == "backtest":
        run(["backtest.py"] + rest)
    elif cmd == "predict":
        run(["predict_daily.py"])
    elif cmd == "collect":
        run(["collector.py"])
    elif cmd == "label":
        run(["collector.py", "--label"])
    # ---- 2026-09-25 LightGBM 平行链路（产物走 ../output/lgb，与 XGB 隔离）----
    elif cmd == "train_lgbm":
        run(["train_lgbm.py"] + rest)
    elif cmd == "predict_lgbm":
        lgb_out = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "output", "lgb"))
        run(["predict_daily.py", "--algo", "lgbm", "--out-dir", lgb_out] + rest)
    elif cmd == "backfill_lgbm":
        lgb_out = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "output", "lgb"))
        run(["predict_daily.py", "backfill"] + (rest or ["30"]) +
            ["--algo", "lgbm", "--out-dir", lgb_out])
    else:
        print(f"未知命令: {cmd}\n{__doc__}")


if __name__ == "__main__":
    main()
