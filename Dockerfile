# 龙钱包现金理财监控 - Docker 镜像
# 基于 Python 3.11 slim，抓取逻辑依赖 curl 子进程 + OpenSSL legacy TLS 重协商
FROM python:3.11-slim

# 系统依赖：
#   curl          建行/邮储/浦发/网络诊断均通过 curl 子进程抓取
#   ca-certificates  HTTPS 证书
#   tzdata         定时任务按北京时间触发
# openssl_legacy.cnf 由项目提供，解决建行站点旧式 TLS 重协商握手失败
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        tzdata \
    && ln -fs /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && dpkg-reconfigure -f noninteractive tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=6868 \
    DATA_DIR=/app/data \
    OPENSSL_CONF=/app/openssl_legacy.cnf \
    TZ=Asia/Shanghai

WORKDIR /app

# 先装 Python 依赖，利用 Docker 层缓存
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝源码（data/ 与 __pycache__ 由 .dockerignore 排除，数据库由卷持久化）
COPY . .
RUN mkdir -p /app/data

EXPOSE 6868

# 直接以单进程启动：APScheduler 在 app.py 内以线程方式运行，
# 使用 gunicorn 多 worker 会造成定时任务重复执行，故不用多进程模型。
CMD ["python3", "app.py"]
