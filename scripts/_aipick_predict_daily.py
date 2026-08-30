# -*- coding: utf-8 -*-
"""
AI 竞价选股 - 每日预测 (增强版 2026-08-27)
9:25-9:30 运行：拉取当日竞价快照 → 模型预测涨停概率 → 生成 HTML 预测报告。

本版改动:
  1) 过滤规则参数化(predict 可传 mv_min/mv_max/bid_amt_min/bid_chg_max), 默认与页面一致
  2) json 同时保存"过滤前全量候选集" all(含 ai_prob/circ_mv/bid_amount/bid_change/...),
     供 App 前端按用户自定义规则实时过滤并放宽/收紧; 默认规则的 top(≤30) 与 HTML 仍保持
  3) 新增 backfill(): 遍历历史上已生成的 predictions_*.json, 凡缺 all 的重新调用 predict(d)
     重算全量候选(从快选 9_25 竞价快照库读 stock), 兼容 8-14 起的全市场快照

输出：output/predictions_YYYY-MM-DD.html / .json（直接浏览器打开）
"""
import json
import os
import sys
import glob
import sqlite3 as _sqlite3
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import today  # noqa E402
from collector import fetch_from_kuaixuan, fetch_market, to_features  # noqa E402

import pandas as pd
import numpy as np
import xgboost as xgb

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")

FEATURES = ["bid_change", "bid_amount", "bid_turnover", "circ_mv", "yesterday_chg", "price"]

# 默认过滤规则(与页面文案一致)
DEFAULT_MV_MIN, DEFAULT_MV_MAX = 30, 500
DEFAULT_BID_AMT_MIN = 2000
DEFAULT_BID_CHG_MAX = 10

# 后端概念库(开盘啦概念映射): 由 concept_refresh 每日采集, 存 code -> 全量概念(board_full)
CONCEPT_DB = "/opt/kuaixuan/kuaixuan.db"


def _concept_map():
    """返回 {code: [全部概念]}。取概念库最近已采集日期的全量概念(board_full); 兼容旧库仅有前N(board)。
    概念本质按日归属, 用最近可用日期作为当日概念; 失败返回空。"""
    try:
        conn = _sqlite3.connect(CONCEPT_DB)
        conn.text_factory = str
        row = conn.execute(
            "SELECT MAX(date) FROM stock_concept WHERE board_full IS NOT NULL AND board_full<>''"
        ).fetchone()
        if not row or not row[0]:
            row = conn.execute("SELECT MAX(date) FROM stock_concept").fetchone()
        if not row or not row[0]:
            conn.close()
            return {}
        d = row[0]
        out = {}
        for code, joined in conn.execute(
                "SELECT code, COALESCE(board_full, board) FROM stock_concept "
                "WHERE date=? AND COALESCE(board_full, board) IS NOT NULL "
                "AND COALESCE(board_full, board)<>''", (d,)):
            parts = [p.strip() for p in str(joined).split("\u3001") if p.strip()]
            if parts:
                out[str(code)] = parts
        conn.close()
        return out
    except Exception:
        return {}


def predict(trade_date=None, mv_min=DEFAULT_MV_MIN, mv_max=DEFAULT_MV_MAX,
            bid_amt_min=DEFAULT_BID_AMT_MIN, bid_chg_max=DEFAULT_BID_CHG_MAX,
            force=False):
    """生成某日预测报告。
    - 保护规则(2026-08-27): 若当日 predictions_{d}.json 已存在且非 force=True，
      主文件(前端展示的那一份)不覆盖；新结果另存 predictions_{d}_rerun.json + _rerun.html
      作为模型/参数对比参照，避免覆盖上午 9:30 竞价结束时的报告。"""
    os.makedirs(OUT_DIR, exist_ok=True)
    d = trade_date or today()
    model_path = os.path.join(MODEL_DIR, "model_xgb.json")
    if not os.path.exists(model_path):
        print("⚠️ 模型不存在，请先运行 train_model.py")
        return None

    model = xgb.XGBClassifier(use_label_encoder=False, verbosity=0)
    model.load_model(model_path)

    stocks = fetch_from_kuaixuan(d) or to_features(fetch_market(), d)
    df = pd.DataFrame(stocks or [])
    for col in FEATURES:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=FEATURES)

    X = df[FEATURES].astype(float)
    proba = model.predict_proba(X)[:, 1]
    df["ai_prob"] = np.round(proba, 4)

    # 开盘啦全量概念(供 App 展示: 默认前2 + 悬浮显示全部)
    cmap = _concept_map()

    def _attach(r):
        r = dict(r)
        cl = cmap.get(str(r.get("code", "")))
        r["concepts"] = cl or []
        if not r.get("concept") and cl:
            r["concept"] = "、".join(cl[:2])
        return r

    # 过滤前全量候选(按概率降序), 供 App 自定义规则过滤
    full = df.sort_values("ai_prob", ascending=False)
    all_rows = [_attach(r) for r in full.to_dict(orient="records")]

    # 按(可配置)规则过滤 → 默认结果 top(≤30) 与 HTML
    df = df[(df["circ_mv"] >= mv_min) & (df["circ_mv"] <= mv_max)]
    df = df[(df["bid_amount"] >= bid_amt_min)]
    df = df[(df["bid_change"] <= bid_chg_max)]
    result = df.sort_values("ai_prob", ascending=False).head(30)
    result_rows = [_attach(r) for r in result.to_dict(orient="records")]

    payload = {
        "date": d,
        "count": len(result_rows),
        "top": result_rows,
        "all": all_rows,
    }
    json_path = os.path.join(OUT_DIR, f"predictions_{d}.json")

    # === 上午版报告保护 ===
    existing = os.path.exists(json_path) and os.path.getsize(json_path) > 0
    write_main = force or (not existing)
    # 下午重跑版无论如何都写到 _rerun 文件，做历史对照
    rerun_json = os.path.join(OUT_DIR, f"predictions_{d}_rerun.json")
    rerun_html = os.path.join(OUT_DIR, f"predictions_{d}_rerun.html")

    def _dump_payload(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        try:
            os.chmod(path, 0o644)
        except Exception:
            pass

    # 始终写一份 rerun 对照(含模型哈希、时间戳等元信息可选)
    try:
        _dump_payload(rerun_json)
        print(f"[rerun] 对照版 → {rerun_json} (主文件写={write_main}, force={force}, existing={existing})")
    except Exception as e:
        print(f"⚠️ 写入 rerun 对照失败: {e}")

    # 主文件按规则决定是否覆盖(上午 9:27 首次 → 写；19:00 或任何时段二次 → 不覆盖)
    if write_main:
        _dump_payload(json_path)
        gen_html(result, d)
        print(f"预测完成: {len(result)} 只 → {json_path} (全量候选 {len(all_rows)} 只)")
    else:
        # 只写 _rerun.html 主 HTML 不动
        _gen_html(result, d, rerun_html)
        print(f"[保护] 主文件已存在，跳过覆盖 {json_path}；新结果仅保留对照版 _rerun.*")

    return result


def _gen_html(df, d, path=None):
    """生成预测 HTML。path=None 时写入 predictions_{d}.html 并同步 latest.html。"""
    rows = ""
    for i, (_, r) in enumerate(df.iterrows(), 1):
        prob = r["ai_prob"] * 100
        bg = f"rgba(200,40,40,{0.08 + prob/100*0.35:.2f})"
        rows += f"""<tr style="background:{bg}">
<td>{i}</td><td class="code">{r['code']}</td><td><b>{r['name']}</b></td>
<td class="prob">{prob:.1f}%</td>
<td>{r['bid_change']:+.2f}%</td>
<td>{r['bid_amount']:.0f}万</td>
<td>{r['circ_mv']:.1f}亿</td>
<td>{r['bid_turnover']:.2f}%</td>
</tr>"""

    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>AI竞价预测 {d}</title>
<style>
body{{font-family:'Microsoft YaHei',sans-serif;background:#0f1219;color:#eef2ff;margin:0;padding:24px}}
h1{{font-size:22px;margin:0}} .sub{{color:#8899bb;font-size:13px;margin:8px 0 20px}}
.card{{background:#181c28;border-radius:10px;padding:20px;margin-bottom:16px;border:1px solid #2a3040}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{background:#2a3040;color:#ffbcbc;padding:9px;text-align:left;position:sticky;top:0}}
td{{padding:8px 9px;border-bottom:1px solid #242a38}}
.code{{font-family:Consolas,monospace;color:#ffd700}}
.prob{{font-weight:700;font-size:15px;color:#ff6b5e}}
.note{{color:#7788aa;font-size:12px;line-height:1.8;margin-top:14px}}
.tag{{display:inline-block;background:#c0392b;color:#fff;padding:2px 10px;border-radius:12px;font-size:12px;margin-left:10px}}
.warn{{background:#3d2a10;border:1px solid #a07020;border-radius:10px;padding:14px 18px;font-size:12px;color:#e0b060;margin-bottom:16px;line-height:1.8}}
</style></head><body>
<div class="card"><h1>AI 竞价选股 · 涨停概率预测 <span class="tag">{d}</span></h1>
<div class="sub">模型：快选・金睛 · 预测当日涨停概率 · 默认过滤（市值30-100亿 / 竞价金额&gt;3000万 / 竞价涨幅&lt;7%）· 供研究参考</div>
<table><thead><tr><th>#</th><th>代码</th><th>名称</th><th>AI涨停概率</th><th>竞价涨幅</th><th>竞价金额</th><th>流通市值</th><th>换手率</th></tr></thead>
<tbody>{rows}</tbody></table>
</div>
<div class="warn">⚠️ 免责声明：AI 预测基于历史统计规律，不构成投资建议。竞价打板风险极高，请严格控制仓位。模型每周自动重训练，数据积累越多预测越准。</div>
</body></html>"""
    if path is None:
        path = os.path.join(OUT_DIR, f"predictions_{d}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    try:
        os.chmod(path, 0o644)
    except Exception:
        pass
    # 主输出时同时刷新 latest.html
    if path == os.path.join(OUT_DIR, f"predictions_{d}.html"):
        try:
            with open(os.path.join(OUT_DIR, "latest.html"), "w", encoding="utf-8") as f:
                f.write(html)
            try:
                os.chmod(os.path.join(OUT_DIR, "latest.html"), 0o644)
            except Exception:
                pass
        except Exception as e:
            print(f"⚠️ latest.html 写入失败: {e}")
    print(f"预测报告已生成: {path}")


def gen_html(df, d):
    """兼容旧调用: 写到 predictions_{d}.html 并刷新 latest.html"""
    _gen_html(df, d, None)


def backfill():
    """给历史 predictions json 补全 all/concepts。
    保护规则(2026-08-27): 当日 predictions_{今天}.json 视为上午版(9:30竞价后生成)，
    不做重跑覆盖；重跑仅 _rerun 文件(非主文件)。
    历史日：缺少 'all' 或为空者，predict(d, force=False) → 已存在 → 只写 rerun；
    有 all 的旧报告仅再次注入概念字段(不改 ai_prob)，走 _attach_concepts_only。"""
    os.makedirs(OUT_DIR, exist_ok=True)
    jsons = sorted(glob.glob(os.path.join(OUT_DIR, "predictions_*.json")))
    if not jsons:
        print("没有历史 json")
        return
    today_str = today()
    cmap = _concept_map()

    ARRAY_KEYS = ("top", "all", "result", "rows")

    def _inject_concepts_only(jp):
        """只注入 concepts/concept 两字段，其他不动(避免复算导致 ai_prob 漂移)"""
        try:
            with open(jp, "r", encoding="utf-8") as f:
                cur = json.load(f)
        except Exception as e:
            print(f"  ⚠️ 读取失败: {e}", flush=True)
            return
        changed = False

        def _att(r):
            """原地修改 r，返回是否做了改动"""
            if not isinstance(r, dict):
                return False
            cl = cmap.get(str(r.get("code", "")))
            had = isinstance(r.get("concepts"), list) and r["concepts"]
            if not had:
                r["concepts"] = cl or []
                if not r.get("concept") and cl:
                    r["concept"] = "、".join(cl[:2])
                return bool(cl) or "concepts" not in r
            return False

        for k in ARRAY_KEYS:
            arr = cur.get(k)
            if isinstance(arr, list) and arr:
                for r in arr:
                    if _att(r):
                        changed = True
        if changed:
            with open(jp, "w", encoding="utf-8") as f:
                json.dump(cur, f, ensure_ascii=False, indent=None, separators=(",", ":"))
            print(f"  [概念注入] → {os.path.basename(jp)}", flush=True)
        else:
            print(f"跳过 {os.path.basename(jp)}: 概念/数据齐全", flush=True)

    for jp in jsons:
        fname = os.path.basename(jp)
        d = fname.replace("predictions_", "").replace(".json", "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            continue  # 跳过 _rerun 等非日期主文件
        # 当日上午版保护：不重算 predict；仅补概念字段
        if d == today_str:
            print(f"[保护] 今日 {d} 主报告，不重算 → 仅补概念字段", flush=True)
            _inject_concepts_only(jp)
            continue
        # 历史日：缺少 all → predict(d)；主文件存在会写 rerun；最后对主文件补概念字段
        try:
            with open(jp, "r", encoding="utf-8") as f:
                cur = json.load(f)
        except Exception:
            cur = {}
        if isinstance(cur.get("all"), list) and cur["all"]:
            print(f"跳过 {d}: 已有 all {len(cur['all'])} 条 → 仅补概念字段", flush=True)
            _inject_concepts_only(jp)
            continue
        print(f"回填 {d} (缺少 all)...", flush=True)
        try:
            predict(d)  # 主文件存在 → 仅写 rerun 对照；主文件缺失则生成
        except Exception as e:
            print(f"  ⚠️ {d} 回填失败: {e}", flush=True)
        # 对主文件补概念字段
        _inject_concepts_only(jp)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "backfill":
        backfill()
    else:
        predict()