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
        3: [], 4: [], 5: [],
    }


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
    assert total == 2
    assert tiers[0]["count"] == 1  # 首板
    assert tiers[1]["count"] == 1  # 二板
    assert len(rows) == 5          # 五档齐


def test_truncate_respects_width():
    long_text = "算力基建数据中心大规模集群扩容光伏储能一体化项目超长期特别国债受益标的西部大开发算力枢纽"
    t = ladder_image._truncate(long_text, 360, 16)
    assert t.endswith("…")
    assert len(t) < len(long_text)