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
    assert client.get("/s/not_exist_id/p/1").status_code == 404


def test_mobile_preview_shows_page_images(client, tmp_path, monkeypatch):
    """手机端: 预览页展示逐页图片而非 iframe(PDF iframe 手机只能看第一页)"""
    monkeypatch.setattr(config, "SUMMARY_UPLOAD_TOKEN", "secret123")
    monkeypatch.setattr(config, "SUMMARY_DIR", str(tmp_path))
    r = client.post("/api/summary/upload", content=PDF_MAGIC,
                    headers=_hdrs(token="secret123"))
    fid = r.json()["id"]
    # 模拟转图产物: 2 页 PNG
    pdir = os.path.join(str(tmp_path), fid + "_pages")
    os.makedirs(pdir)
    for i in (1, 2):
        with open(os.path.join(pdir, "p%04d.png" % i), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\nfake")
    ua = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
          "AppleWebKit/605.1.15 Mobile/15E148 MicroMessenger/8.0")
    v = client.get("/s/" + fid, headers={"User-Agent": ua})
    assert v.status_code == 200
    assert "text/html" in v.headers.get("content-type", "")
    assert "<img" in v.text
    assert "iframe" not in v.text
    assert "/s/%s/p/1" % fid in v.text
    assert "/s/%s/p/2" % fid in v.text
    # 分页图片接口
    p = client.get("/s/" + fid + "/p/1")
    assert p.status_code == 200
    assert p.headers.get("content-type") == "image/png"
    assert client.get("/s/" + fid + "/p/3").status_code == 404


def test_desktop_preview_keeps_iframe(client, tmp_path, monkeypatch):
    """桌面端: 仍走 iframe PDF 原生预览"""
    monkeypatch.setattr(config, "SUMMARY_UPLOAD_TOKEN", "secret123")
    monkeypatch.setattr(config, "SUMMARY_DIR", str(tmp_path))
    r = client.post("/api/summary/upload", content=PDF_MAGIC,
                    headers=_hdrs(token="secret123"))
    fid = r.json()["id"]
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
    v = client.get("/s/" + fid, headers={"User-Agent": ua})
    assert v.status_code == 200
    assert "iframe" in v.text
    assert "/s/%s/pdf" % fid in v.text


def test_list_requires_token(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SUMMARY_UPLOAD_TOKEN", "secret123")
    monkeypatch.setattr(config, "SUMMARY_DIR", str(tmp_path))
    assert client.get("/api/summary/list").status_code == 403
    r = client.get("/api/summary/list", headers=_hdrs(token="secret123"))
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["items"] == []
