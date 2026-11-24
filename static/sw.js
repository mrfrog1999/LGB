/* Service Worker：离线写队列 + 后台同步 + 页面/资源离线缓存。
 * 与页面共用 static/pwa-offline.js（IndexedDB outbox）。 */
importScripts('/pwa-offline-lib.js');

var CACHE = 'lgb-pwa-v3';
var OFFLINE = '/offline.html';
var SYNC_TAG = 'lgb-outbox-sync';

self.addEventListener('install', function (e) {
  e.waitUntil(
    caches.open(CACHE).then(function (c) {
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
      return Promise.all(keys.filter(function (k) { return k !== CACHE; })
                             .map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); })
  );
});

// 通知所有页面客户端同步结果
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
    return notifyClients({ type: 'LGB_SYNC_RESULT', synced: res.synced, failed: res.failed }).then(function(){ return res; });
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

// 页面主动请求冲洗（iOS 无 Background Sync 时由页面触发）
self.addEventListener('message', function (e) {
  if (e.data && e.data.type === 'LGB_FLUSH') {
    e.waitUntil(replayOutbox());
  }
});

self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') return; // 写请求由页面 api() 入队处理，不在此缓存
  var url;
  try { url = new URL(req.url); } catch (err) { return; }
  if (url.origin !== self.location.origin) return;
  var path = url.pathname;

  if (path.indexOf('/api/') === 0) return; // API 一律走网络

  if (req.mode === 'navigate') {
    e.respondWith(
      fetch(req).then(function (resp) {
        if (resp && resp.ok) {
          var copy = resp.clone();
          caches.open(CACHE).then(function (c) { c.put(req, copy); });
          return resp;
        }
        // 网关返回 403/404/5xx 等异常状态时，若有缓存则回退，避免看到错误页
        return caches.match(req, { ignoreSearch: true })
          .then(function (hit) { return hit || caches.match('/', { ignoreSearch: true }); })
          .then(function (hit) { return hit || resp; });
      }).catch(function () {
        // 回退缓存：忽略 query 参数（sharecode 可能变化），优先命中任意已缓存页面
        return caches.match(req, { ignoreSearch: true })
          .then(function (hit) { return hit || caches.match('/', { ignoreSearch: true }); })
          .then(function (hit) { return hit || caches.match(OFFLINE); });
      })
    );
    return;
  }

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
