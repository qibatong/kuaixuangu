# -*- coding: utf-8 -*-
"""
群总结 PDF 上传 + 在线预览 (2026-08-31 主人需求)
=================================================
飞书群总结定时任务生成的 PDF 上传到此, 得到 /s/<id> 链接发到外部群, 点开即可在线预览完整总结。

接口:
  POST /api/summary/upload     上传 PDF (body 直接为 PDF 二进制)
                               鉴权: header X-Api-Token == config.SUMMARY_UPLOAD_TOKEN
                               可选 header X-Title (URL 编码标题)
                               返回: {"ok":true,"id":"...","url":"/s/xxx","pdf_url":"/s/xxx/pdf"}
  GET  /s/<id>                 公开预览页(不要求登录, ID 含日期+随机, 外部群直接可开)
  GET  /s/<id>/pdf             原始 PDF 文件(浏览器原生预览)
  GET  /api/summary/list       文件列表(需 X-Api-Token)
"""
import os
import re
import time
import uuid

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from urllib.parse import unquote

from ..core import config, logger

log = logger.get_logger(__name__)
router = APIRouter()

MAX_PDF_SIZE = 50 * 1024 * 1024  # 50MB 上限
IMG_DPI = 90  # 转图分辨率: A4 -> ~750px 宽, 兼顾手机加载速度

# 移动端图片版预览页(2026-08-31 用户反馈: 手机 iframe 只能看第一页, 改逐页图片滑动)
PAGE_TPL_MOBILE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ margin:0; font-family:"Microsoft YaHei","PingFang SC",sans-serif; background:#eef0f3; }}
  .hd {{ background:#0b3d91; color:#fff; padding:12px 16px; position:sticky; top:0; z-index:9; }}
  .hd h1 {{ font-size:16px; margin:0 0 2px; }}
  .hd .t {{ font-size:11px; opacity:.85; }}
  .page {{ position:relative; margin:10px 0; background:#fff; box-shadow:0 1px 3px rgba(0,0,0,.08); }}
  .page img {{ width:100%; display:block; }}
  .pgno {{ position:absolute; right:8px; bottom:6px; background:rgba(0,0,0,.55); color:#fff;
           font-size:11px; padding:2px 8px; border-radius:10px; }}
  .tip {{ text-align:center; color:#999; font-size:12px; padding:10px 0 24px; }}
</style>
</head>
<body>
<div class="hd">
  <h1>📄 {title}</h1>
  <div class="t">共 {pages} 页 · 上传 {time} · 左右滑动查看</div>
</div>
{pages_html}
<div class="tip">内容由飞书群消息自动汇总生成，仅供参考，不构成投资建议</div>
</body>
</html>"""

PAGE_TPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ margin:0; font-family: "Microsoft YaHei", "PingFang SC", sans-serif; background:#f0f2f5; }}
  .hd {{ background:#0b3d91; color:#fff; padding:14px 20px; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px; }}
  .hd h1 {{ font-size:18px; margin:0; font-weight:600; }}
  .hd .t {{ font-size:12px; opacity:.85; }}
  .wrap {{ max-width:1100px; margin:14px auto; padding:0 12px; }}
  .info {{ background:#fff; border-radius:8px; padding:10px 16px; font-size:13px; color:#444; margin-bottom:12px;
          box-shadow:0 1px 3px rgba(0,0,0,.08); display:flex; gap:20px; flex-wrap:wrap; }}
  .info b {{ color:#0b3d91; }}
  iframe {{ width:100%; height:calc(100vh - 150px); border:1px solid #ddd; border-radius:8px; background:#fff; }}
  .tip {{ text-align:center; color:#999; font-size:12px; padding:8px 0 20px; }}
</style>
</head>
<body>
<div class="hd">
  <h1>📄 {title}</h1>
  <span class="t">上传时间 {time} · 大小 {size}</span>
</div>
<div class="wrap">
  <div class="info">
    <span><b>文件</b>：{file}</span>
    <span><b>页数</b>：{pages}</span>
    <span><b>来源</b>：飞书群消息总结</span>
  </div>
  <iframe src="{pdf_url}" frameborder="0"></iframe>
  <div class="tip">内容由飞书群消息自动汇总生成，仅供参考，不构成投资建议</div>
</div>
</body>
</html>"""


def _ensure_dir() -> None:
    os.makedirs(config.SUMMARY_DIR, exist_ok=True)


def _check_token(x_api_token: str) -> bool:
    """上传/列表鉴权: 未配置 token 时视为关闭鉴权(本地/测试)"""
    if not config.SUMMARY_UPLOAD_TOKEN:
        return True
    return x_api_token == config.SUMMARY_UPLOAD_TOKEN


def _meta_path(fid: str) -> str:
    return os.path.join(config.SUMMARY_DIR, fid + ".json")


def _load_meta(fid: str):
    try:
        import json
        return json.load(open(_meta_path(fid), encoding="utf-8"))
    except Exception:
        return None


def _save_meta(meta: dict) -> None:
    import json
    json.dump(meta, open(_meta_path(meta["id"]), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


def _count_pages(pdf_path: str) -> int:
    """极简页数统计: PDF 内容流中 /Type /Page 出现次数(排除 /Pages)"""
    try:
        data = open(pdf_path, "rb").read()
        return len(re.findall(rb"/Type\s*/Page[^s]", data))
    except Exception:
        return 0


def _pages_dir(fid: str) -> str:
    return os.path.join(config.SUMMARY_DIR, fid + "_pages")


def _render_pages(fid: str, pdf_path: str) -> int:
    """用 PyMuPDF 把 PDF 每页转成 PNG 存到 <fid>_pages/，返回页数；无依赖/失败返回 0
    线程池并行转图; 已生成过则直接复用"""
    try:
        import fitz
    except Exception:
        return 0
    pdir = _pages_dir(fid)
    try:
        doc = fitz.open(pdf_path)
        n = doc.page_count
        if n <= 0:
            return 0
        os.makedirs(pdir, exist_ok=True)
        existing = [f for f in os.listdir(pdir) if f.endswith(".png")]
        if len(existing) >= n:
            doc.close()
            return n
        import threading
        def render(i: int) -> None:
            try:
                pix = doc[i].get_pixmap(dpi=IMG_DPI)
                pix.save(os.path.join(pdir, "p%04d.png" % (i + 1)))
            except Exception:
                pass
        ths = [threading.Thread(target=render, args=(i,)) for i in range(n)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        doc.close()
        return n
    except Exception:
        return 0


def _is_mobile(request: Request) -> bool:
    ua = (request.headers.get("user-agent") or "").lower()
    return any(k in ua for k in ("mobile", "android", "iphone",
                                 "ipad", "micromessenger", "windows phone"))


def _list_pages(fid: str) -> list:
    pdir = _pages_dir(fid)
    if not os.path.isdir(pdir):
        return []
    return sorted(f for f in os.listdir(pdir) if f.endswith(".png"))


@router.post("/api/summary/upload")
async def upload_summary(request: Request,
                         x_api_token: str = Header("", alias="X-Api-Token"),
                         x_title: str = Header("", alias="X-Title")):
    """上传群总结 PDF, 返回预览链接(定时任务调用)"""
    if not _check_token(x_api_token):
        log.warning("summary upload forbidden ip=%s", request.client.host if request.client else "-")
        raise HTTPException(status_code=403, detail={"ok": False, "msg": "forbidden"})
    try:
        length = int(request.headers.get("content-length", 0) or 0)
    except ValueError:
        length = 0
    if length <= 0:
        raise HTTPException(status_code=400, detail={"ok": False, "msg": "empty body"})
    if length > MAX_PDF_SIZE:
        raise HTTPException(status_code=400, detail={"ok": False, "msg": "too large"})
    data = await request.body()
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail={"ok": False, "msg": "not a pdf"})

    _ensure_dir()
    fid = time.strftime("%Y%m%d") + "_" + uuid.uuid4().hex[:8]
    fn = fid + ".pdf"
    pdf_path = os.path.join(config.SUMMARY_DIR, fn)
    with open(pdf_path, "wb") as f:
        f.write(data)
    title = unquote(x_title or "") or "飞书群消息总结"
    pages = _render_pages(fid, pdf_path)  # 预转图供手机端预览, 失败不影响上传
    _save_meta({"id": fid, "file": fn, "title": title,
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "size": "%.1f KB" % (length / 1024),
                "pages": pages})
    log.info("summary uploaded id=%s title=%s size=%d pages=%d", fid, title, length, pages)
    return {"ok": True, "id": fid, "title": title,
            "url": "/s/" + fid, "pdf_url": "/s/" + fid + "/pdf"}


@router.get("/s/{fid}")
def view_summary(fid: str, request: Request):
    """公开预览页: 外部群链接直达, 不要求登录; 手机端展示逐页图片, 桌面端 iframe PDF"""
    meta = _load_meta(fid)
    if not meta:
        raise HTTPException(status_code=404, detail={"ok": False, "msg": "not found"})
    pdf_path = os.path.join(config.SUMMARY_DIR, meta["file"])
    pages = meta.get("pages") or _count_pages(pdf_path)
    imgs = _list_pages(fid)
    if _is_mobile(request) and imgs:
        # 移动端: 逐页图片, 滑动查看(iframe 在手机上只能显示第一页)
        pages_html = "\n".join(
            '<div class="page"><img src="/s/{fid}/p/{i}" loading="lazy" '
            'alt="第{i}页"><span class="pgno">{i}/{total}</span></div>'.format(
                fid=fid, i=i, total=len(imgs))
            for i in range(1, len(imgs) + 1))
        html = PAGE_TPL_MOBILE.format(title=meta["title"], time=meta["time"],
                                      pages=len(imgs), pages_html=pages_html)
    else:
        html = PAGE_TPL.format(title=meta["title"], time=meta["time"],
                               size=meta["size"], file=meta["file"],
                               pages=pages,
                               pdf_url="/s/" + fid + "/pdf")
    return HTMLResponse(html)


@router.get("/s/{fid}/pdf")
def summary_pdf(fid: str):
    """原始 PDF(浏览器原生预览)"""
    path = os.path.join(config.SUMMARY_DIR, fid + ".pdf")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail={"ok": False, "msg": "not found"})
    return Response(content=open(path, "rb").read(), media_type="application/pdf")


@router.get("/s/{fid}/p/{page_no}")
def summary_page_image(fid: str, page_no: int):
    """移动端分页图片(转图产物)"""
    if page_no < 1:
        raise HTTPException(status_code=404, detail={"ok": False, "msg": "not found"})
    path = os.path.join(_pages_dir(fid), "p%04d.png" % page_no)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail={"ok": False, "msg": "not found"})
    return Response(content=open(path, "rb").read(), media_type="image/png")


@router.get("/api/summary/list")
def list_summaries(request: Request,
                   x_api_token: str = Header("", alias="X-Api-Token")):
    """文件列表(按时间倒序)"""
    if not _check_token(x_api_token):
        raise HTTPException(status_code=403, detail={"ok": False, "msg": "forbidden"})
    _ensure_dir()
    items = []
    for fn in os.listdir(config.SUMMARY_DIR):
        if fn.endswith(".json"):
            meta = _load_meta(fn[:-5])
            if meta:
                items.append(meta)
    items.sort(key=lambda m: m.get("time", ""), reverse=True)
    return {"ok": True, "items": items}
