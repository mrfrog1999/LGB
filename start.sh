#!/bin/sh
# 龙钱包启动脚本
# 关键点：建行/邮储等银行站点使用旧式 TLS 重协商，OpenSSL 3.0 默认禁用，
# 通过 OPENSSL_CONF 注入兼容配置，使 curl 子进程能正常抓取。
cd "$(dirname "$0")"
export OPENSSL_CONF="$(pwd)/openssl_legacy.cnf"
export PYTHONUNBUFFERED=1
exec python3 app.py
