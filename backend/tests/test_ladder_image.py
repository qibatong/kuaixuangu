# -*- coding: utf-8 -*-
"""连板天梯图片生成测试"""
import os

import pytest

from app.services import ladder_image


def _sample():
    return {
        1: [{"code": "600001", "name": "示范股份", "reason": "算力", "boardName": "AI",
             "ztCount": 1, "limitTime": 9 * 3600 + 1200, "seal": 5.2e8,
             "turnover": 8.3}],
        2: [{"code": "300001", "name": "连板二号", "reason": "机器人", "boardName": "机器人",
             "ztCount": 2, "limitTime": 9 * 3600 + 4000, "seal": 1.8e8,
             "turnover": 5.2}],
        3: [], 4: [],
        # 五板+ 里含真实 6 板/8 板/9 板(limitUpDays 注入), 验证重分 8 档
        5: [
            {"code": "600005", "name": "六板王者", "reason": "低空经济", "boardName": "低空",
             "ztCount": 5, "limitUpDays": 6, "limitTime": 9 * 3600 + 5000,
             "seal": 3.2e8, "turnover": 4.1},
            {"code": "600008", "name": "八板妖龙", "reason": "AI算力", "boardName": "算力",
             "ztCount": 5, "limitUpDays": 8, "limitTime": 9 * 3600 + 6000,
             "seal": 6.6e8, "turnover": 6.7},
        ],
    }


def _sample_fanbao():
    return [
        {"code": "603001", "name": "反包先锋", "reason": "数据要素",
         "change": 10.0},
        {"code": "002002", "name": "二度反包", "reason": "车路云",
         "change": 19.97},
    ]


def test_build_png_writes_valid_image(tmp_path):
    out = str(tmp_path / "2026-08-26.png")
    ladder_image.build_png(_sample(), "2026-08-26", out)
    assert os.path.isfile(out)
    from PIL import Image
    im = Image.open(out)
    assert im.mode == "RGB"
    assert im.size[0] == ladder_image.WIDTH
    assert im.size[1] > 0


def test_collect_counts(tmp_path):
    tiers, total, rows = ladder_image._collect(_sample())
    assert total == 4
    # 8 档全量统计(含空档: 首板~七板精确档 + 「八板+」溢出档)
    assert len(tiers) == 8
    by_pid = {t["pid"]: t["count"] for t in tiers}
    assert by_pid[1] == 1      # 首板
    assert by_pid[2] == 1      # 二板
    assert by_pid[3] == 0      # 空档
    assert by_pid[4] == 0
    assert by_pid[5] == 0      # 五板: 注入后 5 板无(6/8 板被分出)
    assert by_pid[6] == 1      # 六板(真实 limitUpDays=6, 记录精确板数)
    assert by_pid[7] == 0
    assert by_pid["8+"] == 1   # 八板(limitUpDays=8) 归「八板+」(≥8 板含 8 板)
    # 非空档高板在前: 八板+ → 六板 → 二板 → 首板
    assert [tier["pid"] for tier, _lst in rows] == ["8+", 6, 2, 1]
    # 最高连板 = 8
    mx = max(r["zt"] for _t, lst in rows for r in lst)
    assert mx == 8


def test_collect_overflow_over8(tmp_path):
    """≥8 板(8 板/9 板)均放入「八板+」溢出档, 记录真实板数(9板)"""
    data = {
        5: [
            {"code": "600009", "name": "九板妖刀", "reason": "AI", "boardName": "算力",
             "ztCount": 5, "limitUpDays": 9, "limitTime": 9 * 3600 + 6000,
             "seal": 6.6e8, "turnover": 6.7},
        ],
    }
    tiers, total, rows = ladder_image._collect(data)
    assert total == 1
    by_pid = {t["pid"]: t["count"] for t in tiers}
    assert by_pid["8+"] == 1       # 9 板 → 溢出档
    # 连板池记录真实板数
    item = rows[0][1][0]
    assert item["zt"] == 9
    out = str(tmp_path / "2026-08-26-over8.png")
    ladder_image.build_png(data, "2026-08-26", out)
    assert os.path.isfile(out)


def test_eight_board_goes_to_over8(tmp_path):
    """8 板本身也归入「八板+」(不单列八板档), 记录真实板数 8"""
    data = {
        5: [
            {"code": "600008", "name": "八板妖龙", "reason": "AI算力", "boardName": "算力",
             "ztCount": 5, "limitUpDays": 8, "limitTime": 9 * 3600 + 6000,
             "seal": 6.6e8, "turnover": 6.7},
        ],
    }
    tiers, total, rows = ladder_image._collect(data)
    assert total == 1
    by_pid = {t["pid"]: t["count"] for t in tiers}
    assert by_pid["8+"] == 1       # 8 板 → 溢出档
    assert 8 not in by_pid        # 无单独八板档
    item = rows[0][1][0]
    assert item["zt"] == 8
    out = str(tmp_path / "2026-08-26-8b.png")
    ladder_image.build_png(data, "2026-08-26", out)
    assert os.path.isfile(out)


def test_build_png_with_fanbao(tmp_path):
    out = str(tmp_path / "2026-08-26-fb.png")
    ladder_image.build_png(_sample(), "2026-08-26", out, fanbao=_sample_fanbao())
    assert os.path.isfile(out)
    from PIL import Image
    im = Image.open(out)
    assert im.size[1] > 0


def test_truncate_respects_width():
    long_text = "算力基建数据中心大规模集群扩容光伏储能一体化项目超长期特别国债受益标的西部大开发算力枢纽"
    t = ladder_image._truncate(long_text, 360, 16)
    assert t.endswith("…")
    assert len(t) < len(long_text)
