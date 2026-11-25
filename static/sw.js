/* Service Worker：应用外壳预缓存 + 数据离线缓存 + 离线写队列 + 后台同步。
 * 与页面共用 static/pwa-offline.js（IndexedDB outbox）。 */
importScripts('/pwa-offline-lib.js');

var CACHE = 'lgb-pwa-v5';       // 应用外壳 / 页面 / 静态资源
var DATA_CACHE = 'lgb-data-v5'; // 数据型 GET API 的离线快照（独立于页面，便于登出清理）
var OFFLINE = '/offline.html';
var SYNC_TAG = 'lgb-outbox-sync';
// SW 脚本注册 URL 带 sharecode（/sw.js?sharecode=xxx），据此构造过网关鉴权的预缓存 URL
var SW_SEARCH = self.location.search || '';

// 只缓存"数据型"只读 GET 接口；登录态/对外分享/动作类接口绝不缓存
function isDataApi(path) {
  return /^\/api\/(holdings|additions|withdrawals|fixed\/deposits|users|login-history)(\/|$)/.test(path);
}

self.addEventListener('install', function (e) {
  // 关键修复：安装阶段主动预缓存应用外壳（登录页 + 首页），
  // 保证冷启动 / 首次弱网 / 缓存被回收后，本地一定有应用页面，不再直接落离线页。
  e.waitUntil(
    caches.open(CACHE).then(function (c) {
      // 只缓存稳定的离线页与库文件。应用页面不在此处 fetch 预缓存——
      // 因为这会在未登录态抓到 302 重定向响应，登录跳转时命中它会导致 ERR_FAILED。
      // 应用外壳改由页面在线加载成功后通过 LGB_CACHE_PAGE 消息缓存完整 HTML。
      return Promise.allSettled([
        c.add(new Request(OFFLINE, { credentials: 'same-origin' })),
        c.add(new Request('/pwa-offline-lib.js', { credentials: 'same-origin' }))
      ]);
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      // 清理所有旧版本缓存（含旧数据快照），避免串号与陈旧数据
      return Promise.all(keys.filter(function (k) {
        return k !== CACHE && k !== DATA_CACHE;
      }).map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); })
  );
});

// 通知所有页面客户端
function notifyClients(payload) {
  return self.clients.matchAll({ type: 'window' }).then(function (clients) {
    clients.forEach(function (c) { c.postMessage(payload); });
  });
}

// 重放离线队列（供 sync 事件与页面 message 触发）
// 加锁：Background Sync 与页面触发可能同时到达，避免同一批请求重复提交
var _replaying = false;
function replayOutbox() {
  if (_replaying) return Promise.resolve({ synced: 0, failed: 0 });
  _replaying = true;
  return LGBOffline.flushOutbox().then(function (res) {
    _replaying = false;
    return notifyClients({ type: 'LGB_SYNC_RESULT', synced: res.synced, failed: res.failed }).then(function () { return res; });
  }).catch(function () {
    _replaying = false;
  });
}

// Background Sync：页面关闭时也能在联网后自动重放
self.addEventListener('sync', function (e) {
  if (e.tag === SYNC_TAG) {
    e.waitUntil(replayOutbox());
  }
});

self.addEventListener('message', function (e) {
  var d = e.data || {};
  if (d.type === 'LGB_FLUSH') {
    e.waitUntil(replayOutbox());
  } else if (d.type === 'LGB_CLEAR_DATA') {
    // 登出时清理数据快照，避免不同用户之间串号
    e.waitUntil(caches.delete(DATA_CACHE));
  } else if (d.type === 'LGB_CACHE_PAGE' && d.html) {
    // 页面在线加载成功后，主动把自己的完整 HTML 交给 SW 缓存，
    // 作为"外壳预缓存"之外的补充，确保用户实际访问过的页面都能离线打开。
    e.waitUntil(
      caches.open(CACHE).then(function (c) {
        return c.put(d.url, new Response(d.html, {
          headers: { 'Content-Type': 'text/html; charset=utf-8' }
        }));
      })
    );
  }
});

self.addEventListener('fetch', function (e) {
  var req = e.request;
  var url;
  try { url = new URL(req.url); } catch (err) { return; }
  if (url.origin !== self.location.origin) return;
  var path = url.pathname;

  // ---- 写请求：由页面 api() 离线入队处理，SW 不缓存 ----
  if (req.method !== 'GET') return;

  // ---- 数据型 API：网络优先 + 离线缓存兜底 ----
  // 在线永远取最新（保证实时性与登录态正确），成功后写快照；离线返回最近快照。
  if (path.indexOf('/api/') === 0 && isDataApi(path)) {
    e.respondWith(
      fetch(req).then(function (resp) {
        if (resp && resp.ok) {
          var copy = resp.clone();
          caches.open(DATA_CACHE).then(function (c) { c.put(req, copy); });
        }
        return resp;
      }).catch(function () {
        return caches.match(req, { ignoreSearch: true }).then(function (hit) {
          if (hit) return hit;
          return new Response(JSON.stringify({ ok: false, offline: true }), {
            status: 200, headers: { 'Content-Type': 'application/json; charset=utf-8' }
          });
        });
      })
    );
    return;
  }

  // ---- 其余 API（auth/widget/动作类）一律走网络，不缓存 ----
  if (path.indexOf('/api/') === 0) return;

  // ---- 页面导航：缓存优先（stale-while-revalidate）----
  // 命中缓存立即返回实现秒开与离线可用；后台静默更新；无缓存才等网络。
  if (req.mode === 'navigate') {
    e.respondWith(
      caches.match(req, { ignoreSearch: true }).then(function (cached) {
        var network = fetch(req).then(function (resp) {
          if (resp && resp.ok) {
            var copy = resp.clone();
            caches.open(CACHE).then(function (c) { c.put(req, copy); });
          }
          return resp;
        }).catch(function () { return null; });

        if (cached) {
          // 后台更新缓存（不阻塞首屏）
          network;
          return cached;
        }
        // 无缓存：等网络；失败则回退首页缓存，再回退任意已缓存页面，最后才离线页
        return network.then(function (resp) {
          if (resp && resp.ok) return resp;
          return caches.match('/' + SW_SEARCH, { ignoreSearch: true })
            .then(function (hit) { return hit || caches.match('/', { ignoreSearch: true }); })
            .then(function (hit) { return hit || caches.match('/login' + SW_SEARCH, { ignoreSearch: true }); })
            .then(function (hit) { return hit || caches.match(OFFLINE); });
        });
      })
    );
    return;
  }

  // ---- 静态资源（含离线页/共享库）：缓存优先 + 后台更新 ----
  if (path.indexOf('/static/') === 0 || path === OFFLINE || path === '/pwa-offline-lib.js') {
    e.respondWith(
      caches.match(req).then(function (hit) {
        var net = fetch(req).then(function (resp) {
          if (resp && resp.ok) {
            var copy = resp.clone();
            caches.open(CACHE).then(function (c) { c.put(req, copy); });
          }
          return resp;
        }).catch(function () { return hit; });
        return hit || net;
      })
    );
  }
});
