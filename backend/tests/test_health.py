# -*- coding: utf-8 -*-
"""健康检查测试"""


def hdrs(token):
    return {"Authorization": "Bearer " + token}


def test_health_requires_auth(client):
    assert client.get("/api/health").status_code == 401


def test_health_structure(client, first_user):
    token, _, _ = first_user
    r = client.get("/api/health", headers=hdrs(token))
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok")
    assert d["overall"] in ("ok", "degraded", "down")
    assert "eastmoney_clist" in d["sources"]
    assert "eastmoney_kline" in d["sources"]
    assert "ths_kline" in d["sources"]
    for src in d["sources"].values():
        assert src["status"] in ("ok", "degraded", "down")
        assert src["ok"] >= 0 and src["fail"] >= 0
