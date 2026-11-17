# 理财日历 Web 版 —— 单镜像，SQLite 单文件存储
FROM node:20-slim

ENV TZ=Asia/Shanghai \
    NODE_ENV=production \
    PORT=8080 \
    DB_PATH=/data/finance.db

WORKDIR /app

# 依赖单独一层，代码变更不会重复装依赖；用 lock 文件保证版本可复现
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force

COPY server ./server
COPY web ./web

# 数据目录由 compose 挂载；先建好并交给 node 用户
RUN mkdir -p /data && chown -R node:node /data /app
USER node

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:8080/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server/index.js"]
