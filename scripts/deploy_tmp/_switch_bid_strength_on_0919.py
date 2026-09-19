# -*- coding: utf-8 -*-
"""切换测试机异动口径: use_bid_strength 0 → 1 (2026-09-19)

背景: 9/18 定格 warn_type 全 0 → 17% 异动因子退化 → 主人账号(scoreFloor=80)名单空。
竞价强度口径覆盖 97.9%(快照自算 + 开盘啦, 对东财免疫), 实测可出票。
只改测试机 settings, 生产不动; 随时可用同法设回 "0" 回滚。
"""
import sys
sys.path.insert(0, "/opt/kuaixuan/backend")

from app.services import settings, bid_strength          # noqa: E402


def main():
    before = settings.get("use_bid_strength")
    print("切换前 use_bid_strength = %r   enabled()=%s" % (before, bid_strength.enabled()))
    ok = settings.set("use_bid_strength", "1")
    after = settings.get("use_bid_strength")
    print("写入成功=%s   切换后 = %r   enabled()=%s" % (ok, after, bid_strength.enabled()))
    print()
    print("回滚命令(如需): settings.set('use_bid_strength', '0')")


if __name__ == "__main__":
    main()
