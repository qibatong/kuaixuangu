# -*- coding: utf-8 -*-
"""
通达信一键导入工具 (tdx_import.exe)
====================================
常驻后台小工具: 网页点击"直接导入通达信"时, 本工具自动找到通达信安装目录,
把选股结果写入通达信自选股文件 (T0002\\blocknew\\ZXG.blk), 打开通达信即可看到。

用法: 双击运行一次(无窗口常驻, 最小化到系统托盘旁的进程), 之后网页点"直接导入"即可。
停止: 任务管理器结束 tdx_import.exe。

依赖: 仅标准库, 无需安装 Python。
"""
import json
import os
import socket
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8765
VERSION = "1.0"

# 常见通达信安装目录(可自行添加自己的路径)
CANDIDATE_DIRS = [
    "C:\\new_tdx", "C:\\zd_zsone", "C:\\zd_zszq", "C:\\weituo",
    "D:\\new_tdx", "D:\\zd_zsone", "D:\\zd_zszq", "D:\\weituo",
    "E:\\new_tdx", "E:\\zd_zsone", "E:\\zd_zszq",
    "C:\\Program Files\\tdx", "D:\\Program Files\\tdx",
    "C:\\Program Files (x86)\\tdx", "D:\\Program Files (x86)\\tdx",
]


def find_tdx_dir():
    """探测通达信安装目录: 常见路径 + 注册表卸载列表"""
    # 1. 常见路径
    for d in CANDIDATE_DIRS:
        if os.path.isfile(os.path.join(d, "tdxw.exe")):
            return d
    # 2. 注册表卸载信息(不区分位数)
    try:
        import winreg
        roots = [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]
        subs = [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ]
        for root in roots:
            for sub in subs:
                try:
                    with winreg.OpenKey(root, sub) as key:
                        i = 0
                        while True:
                            try:
                                name = winreg.EnumKey(key, i)
                                i += 1
                                with winreg.OpenKey(root, sub + "\\" + name) as k:
                                    try:
                                        dn, _ = winreg.QueryValueEx(k, "DisplayName")
                                        il, _ = winreg.QueryValueEx(k, "InstallLocation")
                                        if dn and ("通达信" in str(dn) or "tdx" in str(dn).lower()):
                                            if il and os.path.isfile(os.path.join(str(il), "tdxw.exe")):
                                                return str(il)
                                    except OSError:
                                        pass
                            except OSError:
                                break
                except OSError:
                    pass
    except Exception:
        pass
    return None


def read_existing_blk(tdx_dir):
    """读取通达信现有自选股 ZXG.blk, 返回内容列表(失败返回 None)"""
    p = os.path.join(tdx_dir, "T0002", "blocknew", "ZXG.blk")
    if os.path.isfile(p):
        try:
            with open(p, "rb") as f:
                raw = f.read().decode("gbk", "ignore")
            return [ln.strip() for ln in raw.splitlines() if ln.strip()]
        except Exception:
            pass
    return None


def write_zigua(tdx_dir, codes, mode):
    """把选股代码写入通达信自选股。
    codes: [{"code": "600519", "market": "1"}, ...]
    mode: "replace" 覆盖并备份 | "merge" 合并去重
    返回 (ok, msg)
    """
    zxg = os.path.join(tdx_dir, "T0002", "blocknew", "ZXG.blk")
    os.makedirs(os.path.dirname(zxg), exist_ok=True)
    new_lines = ["%s#%s" % (c["market"], c["code"]) for c in codes]

    if mode == "merge":
        old = read_existing_blk(tdx_dir) or []
        merged = []
        seen = set()
        for ln in old + new_lines:
            if ln not in seen:
                seen.add(ln)
                merged.append(ln)
        lines = merged
    else:
        lines = new_lines

    # 备份现有自选股
    if os.path.isfile(zxg):
        try:
            with open(zxg, "rb") as f:
                old_raw = f.read()
            if old_raw.strip():
                with open(zxg + ".bak", "wb") as f:
                    f.write(old_raw)
        except Exception:
            pass

    # 写入(通达信自选股文件为 GBK/ANSI 编码, 内容纯ASCII无碍)
    try:
        with open(zxg, "wb") as f:
            f.write(("\r\n".join(lines) + "\r\n").encode("gbk"))
        return True, "已写入 %d 只 → %s" % (len(lines), zxg)
    except Exception as e:
        return False, "写入失败: %s" % e


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")  # Chrome PNA 预检
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")  # Chrome PNA 预检必需
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/ping":
            self._json({"ok": True, "version": VERSION,
                        "tdx": find_tdx_dir() or None})
            return
        if parsed.path == "/import":
            q = urllib.parse.parse_qs(parsed.query)
            raw = (q.get("codes") or [""])[0]
            mode = (q.get("mode") or ["replace"])[0]
            tdx_dir = find_tdx_dir()
            if not tdx_dir:
                self._json({"ok": False, "msg": "未找到通达信安装目录，请把 tdxw.exe 所在目录路径发给我们"},
                           400)
                return
            codes = []
            for part in raw.split(","):
                part = part.strip()
                if not part:
                    continue
                if "#" in part:
                    m, c = part.split("#", 1)
                    codes.append({"market": m.strip(), "code": c.strip()})
                else:
                    c = part
                    m = "1" if c.startswith(("6", "9")) else ("2" if c.startswith(("4", "8")) else "0")
                    codes.append({"market": m, "code": c})
            if not codes:
                self._json({"ok": False, "msg": "没有可导入的股票代码"}, 400)
                return
            ok, msg = write_zigua(tdx_dir, codes, mode)
            self._json({"ok": ok, "msg": msg, "count": len(codes), "tdx": tdx_dir})
            return
        self._json({"ok": False, "msg": "not found"}, 404)


def find_download_dir():
    """获取系统"下载"目录(注册表 Shell Folders), 失败退回用户目录\\Downloads"""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
        v, _ = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")
        if v:
            return str(v)
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Downloads")


def find_latest_blk():
    """找下载目录里最新的 自选股_*.blk 文件"""
    dl = find_download_dir()
    if not dl or not os.path.isdir(dl):
        return None
    cands = []
    try:
        for fn in os.listdir(dl):
            if fn.startswith("自选股") and fn.endswith(".blk"):
                cands.append(os.path.join(dl, fn))
    except OSError:
        pass
    if not cands:
        return None
    return max(cands, key=os.path.getmtime)


def parse_blk(path):
    """解析 .blk 文件(每行 市场#代码 或 纯代码), 返回 [{"market","code"}, ...]"""
    codes = []
    try:
        with open(path, "rb") as f:
            raw = f.read().decode("gbk", "ignore")
        for ln in raw.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            if "#" in ln:
                m, c = ln.split("#", 1)
                codes.append({"market": m.strip(), "code": c.strip()})
            else:
                c = ln
                m = "1" if c.startswith(("6", "9")) else ("2" if c.startswith(("4", "8")) else "0")
                codes.append({"market": m, "code": c})
    except Exception:
        pass
    return codes


def _msgbox(title, text):
    """Windows 弹窗提示(无控制台窗口时仍可见)"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)
    except Exception:
        pass


def tdx_running():
    """检测通达信是否在运行: 进程名宽松匹配 + 窗口标题识别 双保险"""
    # 1. 进程名: 兼容 tdxw.exe / tdx.exe / 券商定制版(含tdx) 等
    try:
        out = os.popen('tasklist').read().lower()
        if "tdx" in out:
            return True
    except Exception:
        pass
    # 2. 窗口标题: 通达信窗口标题通常含"通达信"
    try:
        import ctypes
        user32 = ctypes.windll.user32
        found = []

        def _cb(hwnd, lparam):
            try:
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    if "通达信" in buf.value:
                        found.append(1)
            except Exception:
                pass
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        user32.EnumWindows(WNDENUMPROC(_cb), 0)
        return bool(found)
    except Exception:
        pass
    return False


def read_clipboard():
    """读取剪贴板文本(纯 ctypes, 失败返回空串)"""
    try:
        import ctypes
        CF_UNICODETEXT = 13
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not user32.OpenClipboard(0):
            return ""
        try:
            h = user32.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return ""
            p = kernel32.GlobalLock(h)
            try:
                return ctypes.wstring_at(p)
            finally:
                kernel32.GlobalUnlock(h)
        finally:
            user32.CloseClipboard()
    except Exception:
        return ""


def log(msg):
    """诊断日志: 用户目录 tdx_import.log"""
    try:
        with open(os.path.join(os.path.expanduser("~"), "tdx_import.log"), "a",
                  encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def write_clipboard(text):
    """写入剪贴板(双通道): ctypes 优先, 失败回退 PowerShell Set-Clipboard"""
    try:
        if _write_clipboard_ctypes(text):
            return True
    except Exception:
        pass
    try:
        return _write_clipboard_powershell(text)
    except Exception:
        return False


def _write_clipboard_ctypes(text):
    """ctypes 写剪贴板(纯标准库)"""
    import ctypes
    CF_UNICODETEXT = 13
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    data = text.encode("utf-16-le") + b"\x00\x00"
    if not user32.OpenClipboard(0):
        return False
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(0x0042, len(data))
        if not h:
            return False
        p = kernel32.GlobalLock(h)
        try:
            ctypes.memmove(p, data, len(data))
        finally:
            kernel32.GlobalUnlock(h)
        user32.SetClipboardData(CF_UNICODETEXT, h)
        return True
    finally:
        user32.CloseClipboard()


def _write_clipboard_powershell(text):
    """PowerShell Set-Clipboard 写剪贴板(无窗口)"""
    import subprocess
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return False
    arr = ",".join("'%s'" % l for l in lines)
    cmd = 'powershell -NoProfile -Command "Set-Clipboard -Value @(%s)"' % arr
    subprocess.run(cmd, timeout=10, creationflags=0x08000000,  # CREATE_NO_WINDOW
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def _restore_clipboard_later(orig, cur, delay):
    """延迟恢复剪贴板: 期间用户没改过才恢复"""
    time.sleep(delay)
    try:
        if read_clipboard() == cur:
            write_clipboard(orig)
    except Exception:
        pass


def notify_import(codes, msg):
    """导入成功后的提示: 弹窗 + (通达信运行中)写剪贴板配合"监控剪贴板"免重启"""
    running = tdx_running()
    log("导入成功 codes=%d 通达信运行=%s" % (len(codes), running))
    if running:
        lines = "\n".join(c["code"] for c in codes)
        ok = write_clipboard(lines)
        log("剪贴板写入=%s 内容前40=%s" % (ok, lines[:40].replace("\n", "|")))
        note = ""
        if ok:
            note = ("\n\n【免重启·需先设置一次】通达信「选项」(新版)或「工具」(老版)菜单里勾选「监控剪贴板」后，"
                    "导入时会弹出确认框，点确定即可免重启更新！\n"
                    "若还没勾选，请现在去通达信勾选（只需一次）。"
                    "\n（15秒后自动恢复你原来的剪贴板内容）")
        threading.Thread(target=_msgbox, args=(
            "通达信导入工具",
            "已自动导入 %d 只自选股！\n%s\n\n"
            "通达信正在运行：按 F6 打开自选股；若没看到，请完全退出通达信再重新打开。%s"
            % (len(codes), msg, note),
        ), daemon=True).start()
        if ok:
            threading.Thread(target=_restore_clipboard_later,
                             args=(read_clipboard(), lines, 15), daemon=True).start()
    else:
        threading.Thread(target=_msgbox, args=(
            "通达信导入工具",
            "已自动导入 %d 只自选股！\n%s\n\n"
            "启动通达信后按 F6 即可查看。" % (len(codes), msg),
        ), daemon=True).start()


_last_imported = None   # (路径, mtime), 避免重复导入


def monitor_loop():
    """监视下载目录: 发现新的 自选股_*.blk 自动导入通达信(不依赖网页网络)"""
    global _last_imported
    while True:
        try:
            tdx = find_tdx_dir()
            blk = find_latest_blk()
            if tdx and blk:
                try:
                    stat = os.stat(blk)
                    key = (blk, stat.st_mtime)
                except OSError:
                    key = None
                if key and key != _last_imported:
                    codes = parse_blk(blk)
                    if codes:
                        ok, msg = write_zigua(tdx, codes, "replace")
                        if ok:
                            _last_imported = key
                            notify_import(codes, msg)
        except Exception:
            pass
        time.sleep(2.5)


def main():
    # 已在运行则退出(避免重复)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", PORT))
        sock.close()
    except OSError:
        return  # 端口被占 = 已有实例

    # HTTP 服务先启动(独立线程, 供部分浏览器网页直连使用)
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    tdx = find_tdx_dir()

    if tdx:
        threading.Thread(target=_msgbox, args=(
            "通达信导入工具",
            "工具已启动，后台运行中。\n\n发现通达信目录：%s\n\n"
            "【重要·只设置一次】请先开通达信，在「选项」（新版）或「工具」（老版）菜单里勾选「监控剪贴板」。\n"
            "这样导入时可免重启自动更新（通达信会弹确认框，点确定即可）。\n\n"
            "使用方法：网页选完股后点「下载自选股」，文件保存后本工具自动导入通达信。\n"
            "（本提示可关闭，工具仍在后台运行）" % tdx,
        ), daemon=True).start()
    else:
        threading.Thread(target=_msgbox, args=(
            "通达信导入工具",
            "工具已启动，但**未找到通达信安装目录**。\n\n"
            "请确认电脑上安装了通达信，或联系我们处理。",
        ), daemon=True).start()

    # 监视下载目录, 自动导入
    threading.Thread(target=monitor_loop, daemon=True).start()

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
