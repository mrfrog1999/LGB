/* 离线写队列共享库：页面 <script> 与 Service Worker importScripts 共用同一套逻辑。
 * 队列存于 IndexedDB（同源，页面与 SW 共享），保证数据不因离线或页面关闭而丢失。
 * 对外暴露 global.LGBOffline。 */
(function (global) {
  'use strict';
  var DB_NAME = 'lgb-offline';
  var DB_VER = 1;
  var STORE = 'outbox';

  function openDB() {
    return new Promise(function (resolve, reject) {
      var req = indexedDB.open(DB_NAME, DB_VER);
      req.onupgradeneeded = function () {
        var db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          db.createObjectStore(STORE, { keyPath: 'id', autoIncrement: true });
        }
      };
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error); };
    });
  }

  function run(mode, fn) {
    return openDB().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(STORE, mode);
        var store = tx.objectStore(STORE);
        var result;
        var req = fn(store);
        if (req) req.onsuccess = function () { result = req.result; };
        tx.oncomplete = function () { try { db.close(); } catch (e) {} resolve(result); };
        tx.onerror = function () { try { db.close(); } catch (e) {} reject(tx.error); };
        tx.onabort = function () { try { db.close(); } catch (e) {} reject(tx.error); };
      });
    });
  }

  function outboxAdd(item) {
    item.createdAt = Date.now();
    return run('readwrite', function (s) { return s.add(item); });
  }

  function outboxAll() {
    return run('readonly', function (s) { return s.getAll(); });
  }

  function outboxCount() {
    return run('readonly', function (s) { return s.count(); });
  }

  function outboxDelete(id) {
    return run('readwrite', function (s) { return s.delete(id); });
  }

  function outboxClear() {
    return run('readwrite', function (s) { return s.clear(); });
  }

  // 按 id 顺序重放队列；成功出队，失败保留待下次重试。
  // fetcher 可注入（测试用），默认用全局 fetch。
  function flushOutbox(fetcher) {
    var doFetch = fetcher || global.fetch.bind(global);
    return outboxAll().then(function (items) {
      items.sort(function (a, b) { return (a.id || 0) - (b.id || 0); });
      var synced = 0, failed = 0;
      var chain = Promise.resolve();
      items.forEach(function (it) {
        chain = chain.then(function () {
          var init = { method: it.method || 'POST', headers: it.headers || { 'Content-Type': 'application/json' } };
          if (it.body != null) init.body = it.body;
          return doFetch(it.url, init).then(function (r) {
            var ok = !!r.ok;
            if (ok && r.headers && (r.headers.get('content-type') || '').indexOf('json') >= 0) {
              return r.clone().json().then(function (d) {
                if (d && d.ok === false) ok = false;
                if (ok) { return outboxDelete(it.id).then(function () { synced++; }); }
                failed++;
              }).catch(function () {
                if (ok) { return outboxDelete(it.id).then(function () { synced++; }); }
                failed++;
              });
            }
            if (ok) return outboxDelete(it.id).then(function () { synced++; });
            failed++;
          }).catch(function () { failed++; });
        });
      });
      return chain.then(function () { return { synced: synced, failed: failed }; });
    });
  }

  global.LGBOffline = {
    openDB: openDB,
    outboxAdd: outboxAdd,
    outboxAll: outboxAll,
    outboxCount: outboxCount,
    outboxDelete: outboxDelete,
    outboxClear: outboxClear,
    flushOutbox: flushOutbox
  };
})(typeof self !== 'undefined' ? self : window);
