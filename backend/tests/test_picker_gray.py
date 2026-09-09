# -*- coding: utf-8 -*-
"""重构 P3/P5 对拍测试

2026-09-09: 选股主链路已切到 picker.pipeline 且**不再回退老链路**, 故"灰度正向
对拍"(_gray_run / parity.compare)随老链路主路径一并删除 —— 它的职责(证明新链路
没选歪)已由 1799 次/日的生产反向对拍完成。本文件只保留反向对拍(_parity_reverse,
观察期用)与 parity.diff_items 的比对语义。

铁律不变: **对拍绝不能影响返回**。旁路执行、只打日志; 无论成功/失败/超时,
/api/stocks 的响应必须与不对拍时完全一致。
"""
import pytest

from app.api import stocks as api
from app.services.picker import parity

FULL = {
    "stSuspend": True, "limitUp": True, "markets": ["hs", "cyb", "kcb"],
    "bidGt": 7, "probLt": 65, "confLt": 65,
    "floatMvFloor": 30, "floatMvGt": 100, "priceGt": 30, "bidAmtFloor": 3000,
}


def test_gray_disabled_by_default(monkeypatch):
    """默认关闭(未配置 picker_gray 时)"""
    from app.services import settings
    monkeypatch.setattr(settings, "get", lambda k, d=None: None)
    assert api._gray_enabled() is False


def test_gray_enabled_by_setting(monkeypatch):
    from app.services import settings
    monkeypatch.setattr(settings, "get", lambda k, d=None: "1" if k == "picker_gray" else None)
    assert api._gray_enabled() is True


