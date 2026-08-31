# -*- coding: utf-8 -*-
"""
群总结 PDF 上传与预览测试 (2026-08-31)
=======================================
覆盖: 上传鉴权 / 非 PDF 拒绝 / 上传成功返回链接 / 公开预览页 / PDF 文件 / 列表鉴权
"""
import os

from app.core import config

PDF_MAGIC = (b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
             b"2 0 obj\n<< /Type /Page /MediaBox [0 0 595 842] >>\nendobj\n"
             b"%%EOF\n")


def _hdrs(token=None, title=None):
    h = {}
    if token:
        h["X-Api-Token"] = token
    if title:
        h["X-Title"] = title
    return h


def test_upload_requires_token(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SUMMARY_UPLOAD_TOKEN", "secret123")
    monkeypatch.setattr(config, "SUMMARY_DIR", str(tmp_path))
    r = client.post("/api/summary/upload", content=PDF_MAGIC, headers=_hdrs())
    assert r.status_code == 403


def test_upload_wrong_token(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SUMMARY_UPLOAD_TOKEN", "secret123")
    monkeypatch.setattr(config, "SUMMARY_DIR", str(tmp_path))
    r = client.post("/api/summary/upload", content=PDF_MAGIC,
                    headers=_hdrs(token="wrong"))
    assert r.status_code == 403


def test_upload_not_pdf(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SUMMARY_UPLOAD_TOKEN", "secret123")
    monkeypatch.setattr(config, "SUMMARY_DIR", str(tmp_path))
    r = client.post("/api/summary/upload", content=b"hello world not pdf",
                    headers=_hdrs(token="secret123"))
    assert r.status_code == 400


def test_upload_ok_and_preview(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SUMMARY_UPLOAD_TOKEN", "secret123")
    monkeypatch.setattr(config, "SUMMARY_DIR", str(tmp_path))
    r = client.post("/api/summary/upload", content=PDF_MAGIC,
                    headers=_hdrs(token="secret123"))
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    fid = d["id"]
    assert d["url"] == "/s/" + fid
    assert d["pdf_url"] == "/s/" + fid + "/pdf"
    assert os.path.isfile(os.path.join(str(tmp_path), fid + ".pdf"))

    # 预览页: 公开访问(无 token), 含 iframe
    v = client.get("/s/" + fid)
    assert v.status_code == 200
    assert "text/html" in v.headers.get("content-type", "")
    assert "飞书群消息总结" in v.text
    assert "iframe" in v.text

    # PDF 文件
    p = client.get("/s/" + fid + "/pdf")
    assert p.status_code == 200
    assert p.headers.get("content-type") == "application/pdf"
    assert p.content.startswith(b"%PDF")


def test_upload_title_header(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SUMMARY_UPLOAD_TOKEN", "secret123")
    monkeypatch.setattr(config, "SUMMARY_DIR", str(tmp_path))
    from urllib.parse import quote
    r = client.post("/api/summary/upload", content=PDF_MAGIC,
                    headers=_hdrs(token="secret123", title=quote("收盘总结")))
    assert r.status_code == 200
    assert r.json()["title"] == "收盘总结"


def test_view_404(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SUMMARY_DIR", str(tmp_path))
    assert client.get("/s/not_exist_id").status_code == 404
    assert client.get("/s/not_exist_id/pdf").status_code == 404


def test_list_requires_token(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SUMMARY_UPLOAD_TOKEN", "secret123")
    monkeypatch.setattr(config, "SUMMARY_DIR", str(tmp_path))
    assert client.get("/api/summary/list").status_code == 403
    r = client.get("/api/summary/list", headers=_hdrs(token="secret123"))
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["items"] == []
