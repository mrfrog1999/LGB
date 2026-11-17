# 理财日历 · 自托管 Docker 版

微信小程序「理财日历」的完整 Web 复刻版：存单台账、日历标记、统计分析、多人共享、到期提醒。
一条 `docker compose up` 就能在自己的服务器上跑起来，数据存本地 SQLite 单文件，不依赖任何云服务。

## 功能对照

| 能力 | 小程序版 | 本 Docker 版 |
|---|---|---|
| 存单增删改 + 单利计算 | ✅ 云开发 | ✅ 本地 SQLite（同一套公式） |
| 日历双色标记（蓝=存入 / 红=到期） | ✅ | ✅ 点某天看当日存入 / 到期明细 |
| 台账筛选（全部 / 未到期 / 已到期 + 银行 / 年 / 月） | ✅ | ✅ 年月同时匹配存入日与到期日 |
| 批量修改「添加人」 | ✅ | ✅ 勾选多笔后一次改 |
| 统计（存入 / 到期，按年 / 月 / 日） | ✅ 独立页 | ✅ 独立页 |
| 多人共享同一账本 | ✅ 邀请码 | ✅ 注册即加入同一账本 |
| 存期标签按年期分色 | ✅ | ✅ |
| 内置 26 家银行 + 10 家官方 logo | ✅ | ✅（logo 已随仓库打包） |
| 到期提醒 | ✅ 微信订阅消息（一条授权一条额度） | ✅ Webhook / 邮件，**无条数限制** |
| 数据备份 | JSON 导入导出 | ✅ 同格式，可与小程序互换 |
| 部署 | 微信云开发（免费额度） | 自有服务器 / NAS / 树莓派 |

> 提醒是本版最大差异：不再受「一次订阅授权只能发一条」的限制。每天到点自动检查，
> 同一天到期的多笔存单会合并成一条消息，直接发到企业微信 / Bark / Server酱 / 邮箱。

## 快速开始

```bash
cd docker

# 1) 准备环境变量（务必改 SESSION_SECRET）
cp .env.example .env
# 生成随机密钥：openssl rand -hex 32   然后填到 .env 的 SESSION_SECRET

# 2) 构建并启动
docker compose up -d --build

# 3) 打开 http://服务器IP:8080
```

首次启动会自动创建账号（默认 `admin` / `admin12345`，可用 `.env` 里的
`BOOTSTRAP_USER` / `BOOTSTRAP_PASSWORD` 改）。**登录后请立刻在「我的」页改掉密码。**

数据落在 `./data/finance.db`（宿主机），删容器不丢。备份直接复制这个目录即可：

```bash
tar czf finance-calendar-$(date +%F).tar.gz data/
```

## 用现成镜像部署（推荐，服务器上不需要构建）

镜像由 GitHub Actions 自动构建推送到 Docker Hub：`mrfrog1989/fpc`（amd64 / arm64 双架构）。

```bash
mkdir finance-calendar && cd finance-calendar
curl -O https://raw.githubusercontent.com/<你的账号>/<仓库名>/main/docker-compose.deploy.yml
cp .env.example .env   # 或自己写四个必需变量
docker compose -f docker-compose.deploy.yml up -d
```

更新版本只需：

```bash
docker compose -f docker-compose.deploy.yml pull
docker compose -f docker-compose.deploy.yml up -d
```

数据库在 `./data/finance.db`，更新镜像不动数据。

## 推送到 GitHub 并自动构建镜像

1. 在 GitHub 建一个**私有**仓库（不要勾选 README/gitignore 初始化）。
2. 本目录已带好 `.github/workflows/docker-image.yml`，推到 `main` 分支即自动构建并推送。
3. 仓库 → Settings → Secrets and variables → Actions，加两个 secret：

   | Secret | 值 |
   |---|---|
   | `DOCKERHUB_USERNAME` | `mrfrog1989` |
   | `DOCKERHUB_TOKEN` | Docker Hub → Account settings → Personal access tokens 生成的 **Read & Write** token（不要用登录密码） |

4. 推送代码：

   ```bash
   git init
   git add .
   git commit -m "feat: 理财日历 Docker 版"
   git branch -M main
   git remote add origin https://github.com/<你的账号>/<仓库名>.git
   git push -u origin main
   ```

5. 打开仓库 Actions 页面看 `Build & Push Docker Image`，首次构建约 8-12 分钟（含 arm64 的 QEMU 模拟）。
   构建产物标签：`latest`（仅默认分支）、`v1.2.3`（打 tag 时）、`sha-xxxxxxx`、`20261117`（日期）。

镜像地址：`docker.io/mrfrog1989/fpc:latest`

> 只在 amd64 服务器上用、想省一半构建时间？把 workflow 里的
> `platforms: linux/amd64,linux/arm64` 改成 `platforms: linux/amd64` 即可。

## 配置项（.env）

| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` | 8080 | 宿主机端口（容器内固定 8080） |
| `SESSION_SECRET` | — | 会话签名密钥，**必须改成随机串** |
| `BOOTSTRAP_USER` | admin | 首次启动建的管理员账号 |
| `BOOTSTRAP_PASSWORD` | admin12345 | 同上，登录后请修改 |
| `COOKIE_SECURE` | 0 | 走 HTTPS 反代时设 `1` |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_SECURE` | — | 邮件通知（可留空） |
| `SMTP_USER` / `SMTP_PASS` / `SMTP_TO` | — | 邮箱账号、密码、收件地址 |

改完 `.env` 执行 `docker compose up -d` 重启即可生效。

## 到期提醒怎么配

在「我的」页 → 到期提醒：

1. **推送时间**：默认 09:00。服务内部每分钟检查一次，落在「设定时间 → +30 分钟」窗口时执行。
2. **提前一天 / 当天**：两个开关，可以只留前一天（少一半消息）。
3. **通知渠道**，任选其一：

| 渠道 | 填什么 |
|---|---|
| 企业微信群机器人 | 群设置 → 群机器人 → 添加 → 复制 Webhook 地址；类型选「企业微信群机器人」 |
| Bark（iOS） | `https://api.day.app/你的KEY`；类型选「Bark」 |
| Server酱 | `https://sctapi.ftqq.com/你的SendKey.send`（不填 key 时会带类型选择） |
| 通用 Webhook | 任意能收 POST JSON 的地址（飞书自定义机器人、n8n、自建脚本） |
| 邮件 | 只填 `.env` 里的 SMTP 这几行，页面上不用填 |

配好点「发一条测试」：如果今天 / 明天有到期存单，会立刻推一条（`测试模式` 不会写去重记录，可反复点）。

推送内容示例：

```
明日到期 · 共 4 笔（2029-11-04）
本息合计：94,725.00 元

- 北京银行  23,250.00 元（本金 22,000.00 + 利息 1,250.00）
- 北京银行  23,100.00 元（本金 22,000.00 + 利息 1,100.00）
...
```

## 反向代理（可选，建议配 HTTPS）

Nginx 示例：

```nginx
server {
    listen 443 ssl;
    server_name finance.example.com;

    ssl_certificate     /etc/letsencrypt/live/finance.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/finance.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

配上 HTTPS 后记得把 `.env` 的 `COOKIE_SECURE` 改成 `1` 再重启。

## 从微信小程序迁移数据

1. 小程序「我的」页 → 导出备份，得到 `finance-calendar-backup.json`
2. 本版「我的」页 → 数据 → 导入备份 → 选择该文件
3. 导入是**追加**，重复导入会翻倍；如需重来，先清空：`docker compose down && rm -rf data && docker compose up -d`

JSON 结构完全一致（`{ app, version, exportAt, items[] }`），两边可以互导。

## 常用运维

```bash
docker compose logs -f          # 看日志（含提醒调度）
docker compose restart          # 重启
docker compose down             # 停止
docker compose up -d --build    # 更新代码后重建
```

查看数据库：

```bash
docker compose exec finance-calendar node -e "const d=require('better-sqlite3')('/data/finance.db');console.log(d.prepare('select count(*) n from deposits').get())"
```

## 目录结构

```
docker/
├── Dockerfile            # node:20-slim，非 root 运行，带 healthcheck
├── docker-compose.yml    # 端口 / volume / 环境变量
├── .env.example          # 配置模板
├── package.json          # express + better-sqlite3 + node-cron + nodemailer
├── server/
│   ├── index.js          # REST 接口 + 静态服务
│   ├── db.js             # SQLite 建表、初始账号、设置项
│   ├── auth.js           # scrypt 密码 + HMAC 会话 Cookie
│   ├── calc.js           # 日期 / 存期 / 单利 / 展示字段（与小程序同口径）
│   ├── banks.js          # 26 家银行 + logo 映射
│   └── notify.js         # 到期提醒调度与多通渠道
└── web/                  # 纯静态前端（无构建步骤）
    ├── index.html
    ├── app.js
    ├── style.css
    └── images/banks/*.png
```

## 安全说明

- 密码用 `scrypt` 加盐哈希存储，不落明文；
- 会话是 HMAC 签名的 HttpOnly Cookie，默认 30 天；
- 所有接口都要登录，未登录返回 401；
- 容器以 `node` 非 root 用户运行；
- 建议只在内网 / 反代 HTTPS 后暴露，不要裸奔公网 8080。

## 常见问题

**端口被占用**：改 `.env` 的 `PORT`（例如 8088），`docker compose up -d`。

**登录后一直跳回登录页**：多半是 `COOKIE_SECURE=1` 但访问的是 http；改成 `0` 或配好 HTTPS。

**提醒没发**：看 `docker compose logs -f`；确认「开启提醒」是开的、Webhook 地址没写错、
容器时区为 `Asia/Shanghai`（compose 已设）。测试按钮能立刻验证一次。

**想让多人各自记账、互不可见**：当前是「一个部署 = 一个共享账本」，家人注册后共用同一份数据。
如果要多账本，需要扩展 `ledger` 概念，属于后续迭代。
