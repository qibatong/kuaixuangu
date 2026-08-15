# -*- coding: utf-8 -*-
"""
管理后台真实浏览器回归脚本 (测试机专用, Chromium headless + CDP)
验证: 统计卡片 / 用户列表 / 权重配置 / 打分明细 / 全局默认参数 / 历史竞价回放 渲染

用法:
  /usr/local/python311/bin/python3 scripts/admin_reg.py --user shiren --pwd Felix518
  # 或环境变量: KX_REG_USER / KX_REG_PWD
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request

import websocket

CHROME = "/usr/bin/chromium-browser"
BASE = "http://127.0.0.1"
PORT = 9223  # 与 browser_reg.py(9222) 错开端口


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def run(shot_dir, user, pwd):
    os.makedirs(shot_dir, exist_ok=True)
    # 独立 user-data-dir 避免共享 chromium 默认 profile 中的残留 token(localStorage),
    # 否则路由守卫会把 /login 重定向到 /stock, 导致登录表单永远不出现
    user_data = "/tmp/admin_reg_chrome"
    subprocess.run(["rm", "-rf", user_data], check=False)
    log("启动 headless chromium :%d (独立 profile)" % PORT)
    # 测试机外网受限, cdnjs.cloudflare.com 不可达会卡死 <link rel=stylesheet>(render-blocking),
    # 进而阻塞 module script(等价 defer), Vue 永不初始化. 用 host-resolver-rules 把外网
    # font-awesome CDN 解析到 127.0.0.1:0 立即 ECONNREFUSED, 不阻塞 DOM 解析.
    resolver = "MAP cdnjs.cloudflare.com 127.0.0.1:0"
    proc = subprocess.Popen(
        [CHROME, "--headless", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
         "--remote-allow-origins=*", "--user-data-dir=%s" % user_data,
         "--host-resolver-rules=%s" % resolver,
         "--remote-debugging-port=%d" % PORT, "--window-size=1440,900", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    failed = []
    try:
        tabs = None
        for _ in range(40):
            try:
                tabs = json.load(urllib.request.urlopen("http://127.0.0.1:%d/json" % PORT, timeout=2))
                if tabs:
                    break
            except Exception:
                time.sleep(1)
        if not tabs:
            log("❌ chromium 调试端口未就绪")
            sys.exit(1)

        ws = websocket.create_connection(tabs[0]["webSocketDebuggerUrl"], timeout=30)
        mid = [0]

        def cdp(method, params=None, timeout=40):
            mid[0] += 1
            _id = mid[0]
            ws.send(json.dumps({"id": _id, "method": method, "params": params or {}}))
            ws.settimeout(timeout)
            while True:
                r = json.loads(ws.recv())
                if r.get("id") == _id:
                    return r

        def ev(expr, timeout=15):
            r = cdp("Runtime.evaluate",
                    {"expression": expr, "returnByValue": True, "awaitPromise": True}, timeout)
            res = r.get("result", {}).get("result", {})
            if res.get("exceptionDetails"):
                return "EXC:" + json.dumps(res["exceptionDetails"], ensure_ascii=False)[:200]
            return res.get("value")

        def shot(name):
            r = cdp("Page.captureScreenshot", {"format": "png"})
            data = r.get("result", {}).get("data")
            if data:
                with open(os.path.join(shot_dir, name), "wb") as f:
                    f.write(base64.b64decode(data))
                log("📷 截图 %s" % name)

        cdp("Page.enable")
        cdp("Runtime.enable")

        # ---- 登录 ----
        log("登录 %s" % user)
        cdp("Page.navigate", {"url": BASE + "/login"})
        form_ok = False
        for _ in range(20):
            if ev("!!document.querySelector('input[placeholder*=\"用户名\"]')"):
                form_ok = True
                break
            time.sleep(1)
        if not form_ok:
            log("❌ 登录表单未出现")
            shot("00_login_fail.png")
            sys.exit(1)
        ev("""
        (() => {
          const setVal = (el, v) => {
            const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            s.call(el, v); el.dispatchEvent(new Event('input', {bubbles: true}));
          };
          const u = document.querySelector('input[placeholder*="用户名"]');
          const p = document.querySelector('input[placeholder*="密码"]');
          if (!u || !p) return 'NOFORM';
          setVal(u, '%s'); setVal(p, '%s');
          const b = [...document.querySelectorAll('button')].find(x => x.innerText.includes('登录'));
          if (b) { b.click(); return 'CLICKED'; }
          return 'NOBTN';
        })()
        """ % (user, pwd))
        time.sleep(3)
        url = ev("location.href")
        if "login" in (url or ""):
            log("❌ 登录失败 url=%s" % url)
            shot("00_login_fail.png")
            sys.exit(1)
        log("✅ 登录成功 -> %s" % url)

        # ---- /admin ----
        log("打开 /admin")
        cdp("Page.navigate", {"url": BASE + "/admin"})
        time.sleep(4)
        for _ in range(10):
            n = ev("document.querySelectorAll('.admin-table tbody tr').length")
            if n and int(n) > 0:
                break
            time.sleep(1)
        d = json.loads(ev("""
        (() => {
          const q = s => document.querySelectorAll(s).length;
          const statNums = [...document.querySelectorAll('.stat-num')].map(x => x.innerText.trim());
          const firstRow = (() => {
            const tr = document.querySelector('.admin-table tbody tr');
            return tr ? [...tr.querySelectorAll('td')].map(td => td.innerText.trim()) : [];
          })();
          return JSON.stringify({ url: location.href, cards: q('.stat-card'), statNums, tables: q('.admin-table'), firstRow });
        })()
        """))
        log("  统计卡片=%s 数字=%s 表格数=%s" % (d.get("cards"), d.get("statNums"), d.get("tables")))
        if d.get("cards") != 5:
            failed.append("统计卡片数异常(期望5): %s" % d.get("cards"))
        nums = d.get("statNums") or []
        if len(nums) < 4 or all(x in ("-", "") for x in nums[:4]):
            failed.append("统计数字未渲染: %s" % nums)
        if (d.get("tables") or 0) < 3:
            failed.append("admin-table 数量过少(期望用户列表/权重/打分明细): %s" % d.get("tables"))
        if not d.get("firstRow") or len(d.get("firstRow", [])) < 9:
            failed.append("用户列表首行异常: %s" % d.get("firstRow"))
        shot("01_admin_top.png")

        # ---- 权重配置 + 打分明细 + 全局默认参数 ----
        d2 = json.loads(ev("""
        (() => {
          const t = [...document.querySelectorAll('.weight-total')].map(x => x.innerText.trim());
          const tabs = [...document.querySelectorAll('.factor-tab')].map(x => x.innerText.trim());
          const fields = [...document.querySelectorAll('.field-label')].map(x => x.innerText.trim()).filter(Boolean);
          return JSON.stringify({ weight: t, factorTabs: tabs, defaultFields: fields.length });
        })()
        """))
        log("  权重合计: %s 因子Tab数: %s 默认参数项: %s" % (d2.get("weight"), len(d2.get("factorTabs") or []), d2.get("defaultFields")))
        if not any("权重合计" in (w or "") for w in (d2.get("weight") or [])):
            failed.append("权重配置卡片缺失/权重合计未显示: %s" % d2.get("weight"))
        if len(d2.get("factorTabs") or []) != 5:
            failed.append("打分明细因子Tab数异常(期望5): %s" % d2.get("factorTabs"))
        if (d2.get("defaultFields") or 0) < 6:
            failed.append("全局默认筛选参数项过少: %s" % d2.get("defaultFields"))
        shot("02_admin_scoring.png")

        # ---- 历史竞价回放: 点击回放按钮 ----
        log("历史竞价回放(点击回放)")
        ev("(function(){var b=[...document.querySelectorAll('button')].find(x=>x.innerText.includes('回放'));if(b){b.click();return true;}return false;})()")
        time.sleep(3)
        d3 = json.loads(ev("""
        (() => {
          const cards = [...document.querySelectorAll('.admin-card')].map(x => x.innerText.slice(0, 30));
          const playCard = cards.some(x => x.includes('历史竞价回放'));
          const emptyTip = document.body.innerText.includes('暂无快照');
          return JSON.stringify({ playCard, emptyTip });
        })()
        """))
        log("  回放卡片存在=%s 空态提示=%s" % (d3.get("playCard"), d3.get("emptyTip")))
        if not d3.get("playCard"):
            failed.append("历史竞价回放卡片未渲染")
        shot("03_admin_playback.png")

        ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    log("=" * 50)
    if failed:
        log("❌ 失败项:")
        for f in failed:
            log("  - " + f)
        sys.exit(2)
    log("✅✅ 管理后台(统计卡片/用户列表/权重配置/打分明细/默认参数/历史回放)真实浏览器验证全部通过, 截图: %s" % shot_dir)


def main():
    parser = argparse.ArgumentParser(description="管理后台真实浏览器回归 (测试机专用)")
    parser.add_argument("--user", default=os.environ.get("KX_REG_USER", ""), help="登录用户名")
    parser.add_argument("--pwd", default=os.environ.get("KX_REG_PWD", ""), help="登录密码")
    parser.add_argument("--shot-dir", default="/opt/kuaixuan/logs/admin_reg", help="截图目录")
    args = parser.parse_args()
    if not args.user or not args.pwd:
        parser.error("需提供 --user/--pwd 或设置 KX_REG_USER/KX_REG_PWD 环境变量")
    run(args.shot_dir, args.user, args.pwd)


if __name__ == "__main__":
    main()
