package com.kuaixuan.app;

import android.app.DownloadManager;
import android.content.Context;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.webkit.CookieManager;
import android.webkit.URLUtil;
import android.widget.Toast;

import com.getcapacitor.BridgeActivity;

/**
 * 快选安卓壳（2026-10-04，Capacitor 远端加载）。
 *
 * ★ 这里只做一件事：**接管下载**。
 *   原生 WebView **默认不处理下载**（既不弹框也不落盘），网页里那些
 *   `a[download]` / Blob 下载在壳内点了毫无反应——这是壳方案**必踩**的坑，
 *   不写这段，用户会以为"下载功能坏了"。
 *
 * 覆盖范围：`/api/ladder/image/.../download`（图片）、用户 CSV 等**服务端文件下载**。
 * ⚠️ 已知限制：`blob:` / `data:` 开头的下载（自选股 .blk 走的是 Blob）WebView 拿不到
 *    字节流 ⇒ 只能提示用户到浏览器打开（前端侧 `PoolView` 已在壳内隐藏该按钮）。
 */
public class MainActivity extends BridgeActivity {

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setupDownloadListener();
    }

    private void setupDownloadListener() {
        if (getBridge() == null || getBridge().getWebView() == null) return;

        getBridge().getWebView().setDownloadListener(
            (url, userAgent, contentDisposition, mimeType, contentLength) -> {
                if (url == null) return;

                if (url.startsWith("blob:") || url.startsWith("data:")) {
                    toast("该内容在 App 内暂不支持下载，请在浏览器中打开", true);
                    return;
                }

                try {
                    String name = URLUtil.guessFileName(url, contentDisposition, mimeType);
                    // 带上会话 Cookie：接口下载要鉴权(Authorization 走 header 的除外,
                    // 那些是前端 fetch 后自己造 Blob 的场景, 不经过这里)
                    String cookie = CookieManager.getInstance().getCookie(url);

                    DownloadManager.Request req = new DownloadManager.Request(Uri.parse(url));
                    if (cookie != null && !cookie.isEmpty()) {
                        req.addRequestHeader("Cookie", cookie);
                    }
                    if (userAgent != null && !userAgent.isEmpty()) {
                        req.addRequestHeader("User-Agent", userAgent);
                    }
                    req.setTitle(name);
                    req.setMimeType(mimeType);
                    req.setNotificationVisibility(
                        DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                    req.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name);
                    req.allowScanningByMediaScanner();

                    DownloadManager dm = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
                    if (dm != null) {
                        dm.enqueue(req);
                        toast("已开始下载：" + name, false);
                    } else {
                        toast("下载服务不可用", true);
                    }
                } catch (Exception e) {
                    toast("下载失败：" + (e.getMessage() == null ? "未知错误" : e.getMessage()), true);
                }
            }
        );
    }

    private void toast(String msg, boolean longDuration) {
        Toast.makeText(this, msg, longDuration ? Toast.LENGTH_LONG : Toast.LENGTH_SHORT).show();
    }
}
