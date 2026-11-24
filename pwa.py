"""PWA 支持：Web App Manifest、Service Worker、离线写队列、离线兜底页。

设计要点（结合本站部署网关的特殊性）：
- 网关对所有资源强制 no-store，且需要 sharecode cookie 鉴权。
- manifest / sw / lib / offline 走根路径路由，scope 天然为 '/'，便于鉴权与注册。
- Cache API 不受 HTTP no-store 头影响，SW 仍可正常缓存静态资源与页面。
- API 一律不缓存（登录态、实时净值/持仓数据必须走网络）。
- 页面导航网络优先，断网时回退最近缓存页或离线页。
- 写请求由页面 api() 离线入队 IndexedDB，SW 后台同步重放（详见 static/pwa-offline.js、static/sw.js）。
"""
import json
import os

from flask import Blueprint, Response, request

pwa_bp = Blueprint("pwa", __name__)

_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def _sharecode():
    """从当前请求读取 sharecode，供 manifest 内资源 URL 与 start_url 携带。"""
    return request.args.get("sharecode", "")


def _read_static(name):
    with open(os.path.join(_STATIC, name), "r", encoding="utf-8") as f:
        return f.read()


# ---------------- Web App Manifest ----------------
@pwa_bp.route("/manifest.json")
def manifest():
    sc = _sharecode()
    q = ("?sharecode=" + sc) if sc else ""
    start = "/" + q
    data = {
        "name": "现金理财监控",
        "short_name": "理财监控",
        "description": "银行现金理财产品收益与定期存单监控",
        "lang": "zh-CN",
        "start_url": start,
        "scope": "/",
        "display": "standalone",
        "display_override": ["standalone", "minimal-ui"],
        "orientation": "portrait",
        "background_color": "#f4f6fb",
        "theme_color": "#ef4444",
        "icons": [
            {"src": "/static/favicon-192.png" + q, "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/static/favicon-512.png" + q, "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/static/favicon-512.png" + q, "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    return Response(
        json.dumps(data, ensure_ascii=False),
        mimetype="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )


# ---------------- 离线队列共享库（供 SW importScripts） ----------------
@pwa_bp.route("/pwa-offline-lib.js")
def offline_lib():
    return Response(
        _read_static("pwa-offline.js"),
        mimetype="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


# ---------------- Service Worker ----------------
@pwa_bp.route("/sw.js")
def service_worker():
    return Response(
        _read_static("sw.js"),
        mimetype="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


# ---------------- 离线兜底页 ----------------
OFFLINE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="#ef4444">
<title>离线 · 现金理财监控</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{min-height:100vh;display:flex;align-items:center;justify-content:center;
    background:#f4f6fb;color:#1e293b;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
    padding:24px;-webkit-tap-highlight-color:transparent}
  .box{width:100%;max-width:340px;text-align:center;background:#fff;border-radius:20px;
    padding:40px 28px;box-shadow:0 6px 30px rgba(16,24,40,.06)}
  .ic{width:72px;height:72px;margin:0 auto 18px;border-radius:20px;display:flex;align-items:center;justify-content:center;
    background:linear-gradient(135deg,#fb7137,#ef4444)}
  .ic svg{width:38px;height:38px;stroke:#fff;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
  h1{font-size:19px;margin-bottom:8px}
  p{font-size:14px;color:#8b97ab;line-height:1.7;margin-bottom:24px}
  button{width:100%;padding:13px;border:none;border-radius:12px;background:linear-gradient(135deg,#fb7137,#ef4444);
    color:#fff;font-size:15px;font-weight:700;cursor:pointer}
  button:active{opacity:.85}
</style>
</head>
<body>
  <div class="box">
    <div class="ic"><svg viewBox="0 0 24 24"><path d="M2 8.8A15.9 15.9 0 0 1 12 5c3.4 0 6.6 1 9.2 2.8"/><path d="M5 12.3A10.7 10.7 0 0 1 12 10c2.3 0 4.4.7 6.2 1.9"/><path d="M8.5 15.7A5.4 5.4 0 0 1 12 15c1.2 0 2.3.4 3.2 1"/><path d="M12 19h.01"/><path d="M2 2l20 20"/></svg></div>
    <h1>当前处于离线状态</h1>
    <p>暂时无法连接网络，请检查网络后重试。<br>离线期间的添加 / 修改操作会自动暂存，联网后同步。</p>
    <button onclick="location.reload()">重新加载</button>
  </div>
</body>
</html>
"""


@pwa_bp.route("/offline.html")
def offline():
    return Response(OFFLINE_HTML, mimetype="text/html", headers={"Cache-Control": "no-cache"})
