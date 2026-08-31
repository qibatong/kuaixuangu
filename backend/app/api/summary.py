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
    with open(os.path.join(config.SUMMARY_DIR, fn), "wb") as f:
        f.write(data)
    title = unquote(x_title or "") or "飞书群消息总结"
    _save_meta({"id": fid, "file": fn, "title": title,
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "size": "%.1f KB" % (length / 1024)})
    log.info("summary uploaded id=%s title=%s size=%d", fid, title, length)
    return {"ok": True, "id": fid, "title": title,
            "url": "/s/" + fid, "pdf_url": "/s/" + fid + "/pdf"}


@router.get("/s/{fid}")
def view_summary(fid: str):
    """公开预览页: 外部群链接直达, 不要求登录"""
    meta = _load_meta(fid)
    if not meta:
        raise HTTPException(status_code=404, detail={"ok": False, "msg": "not found"})
    pdf_path = os.path.join(config.SUMMARY_DIR, meta["file"])
    html = PAGE_TPL.format(title=meta["title"], time=meta["time"],
                           size=meta["size"], file=meta["file"],
                           pages=_count_pages(pdf_path),
                           pdf_url="/s/" + fid + "/pdf")
    return HTMLResponse(html)


@router.get("/s/{fid}/pdf")
def summary_pdf(fid: str):
    """原始 PDF(浏览器原生预览)"""
    path = os.path.join(config.SUMMARY_DIR, fid + ".pdf")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail={"ok": False, "msg": "not found"})
    return Response(content=open(path, "rb").read(), media_type="application/pdf")


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
