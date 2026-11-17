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

# 3) 打开 http://服务器IP:6868
```

首次启动会自动创建账号（默认 `admin` / `admin12345`，可用 `.env` 里的
`BOOTSTRAP_USER` / `BOOTSTRAP_PASSWORD` 改）。**登录后请立刻在「我的」页改掉密码。**

数据落在 `./data/finance.db`（宿主机），删容器不丢。备份直接复制这个目录即可：

```bash
tar czf finance-calendar-$(date +%F).tar.gz data/
```

## 两种部署方式

镜像由 GitHub Actions 自动构建，发布在 **GitHub 自己的镜像仓库**（GHCR，不经过 Docker Hub）：

```
ghcr.io/mrfrog1999/fpc:latest
```

支持 `linux/amd64` 与 `linux/arm64` 双架构，标签有 `latest`、`v1.2.3`（打 tag 时）、`sha-xxxxxxx`、`20261117`。

### 方式 A：服务器本地构建（用 `docker-compose.yml`）

适合想自己掌控代码、或改动了源码的情况：

```bash
git clone git@github.com:mrfrog1999/FPC.git finance-calendar
cd finance-calendar
cp .env.example .env        # 改 SESSION_SECRET
docker compose up -d --build
```

`docker compose` 会用目录里的 `Dockerfile` 现场构建，并把镜像打成 `ghcr.io/mrfrog1999/fpc:latest`，与线上标签保持一致。

### 方式 B：直接拉现成镜像（用 `docker-compose.deploy.yml`）

服务器上不需要源码、不需要构建，只要这个 compose 文件：

```bash
mkdir finance-calendar && cd finance-calendar
curl -O https://raw.githubusercontent.com/mrfrog1999/FPC/main/docker-compose.deploy.yml
curl -O https://raw.githubusercontent.com/mrfrog1999/FPC/main/.env.example
cp .env.example .env        # 改 SESSION_SECRET

docker compose -f docker-compose.deploy.yml up -d
```

更新版本：

```bash
docker compose -f docker-compose.deploy.yml pull
docker compose -f docker-compose.deploy.yml up -d
```

数据库始终在 `./data/finance.db`，换镜像不动数据。

> **私有镜像要先登录**：GitHub Packages 的容器默认继承仓库可见性，私有仓库 → 私有镜像。
> 服务器上执行一次：
>
> ```bash
> echo "<你的 GitHub PAT，勾选 read:packages>" | docker login ghcr.io -u mrfrog1999 --password-stdin
> ```
>
> 不想每次登录，就在 GitHub → 你的头像 → Your packages → `fpc` → Package settings 里把可见性改成 **Public**（里面只有程序，没有存单数据）。

## GitHub Actions 怎么构建

`.github/workflows/docker-image.yml` 已经配好，**不需要任何 secret**——用 Actions 自动注入的 `GITHUB_TOKEN` 登录 GHCR：

| 项 | 设置 |
|---|---|
| 触发 | push 到 `main`、打 `v*` tag、手动 Run workflow |
| 架构 | `linux/amd64` + `linux/arm64` |
| 推送目标 | `ghcr.io/mrfrog1999/fpc` |
| 缓存 | `type=gha`，第二次起快很多 |

首次构建约 8-12 分钟（arm64 走 QEMU 模拟偏慢）。只想在 amd64 上跑、想省一半时间，把 workflow 里
`platforms: linux/amd64,linux/arm64` 改成 `platforms: linux/amd64`。

如果要改镜像仓库名，改两处：workflow 里的 `IMAGE_NAME`、compose 里的 `image:`。

## 配置项（.env）

| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` | 6868 | 宿主机端口（容器内固定 8080）。**必须是 1-65535**，超过会报 `invalid hostPort` |
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

## 端口注意

宿主机端口**必须是 1-65535** 之间的整数。写成 `88888` 这类五位数以上会直接报：

```
docker-compose config failed, invalid hostPort: 88888
```

不是面板不兼容，是端口号本身越界。想用一个不常见的号，选 `6868`、`18888`、`28888` 这类都行。

## Oracle Cloud（OCI）部署要点

Oracle 的机器有两道墙，**只开一道是不够的**：OCI 控制台的安全列表 + 实例内部的 iptables。

### 1. 控制台放行 6868

计算 → 实例 → 点进实例 → 所属的 **Virtual Cloud Network** → **Security List**（或你用的 NSG）→
Add Ingress Rule：

| 字段 | 值 |
|---|---|
| Source CIDR | `0.0.0.0/0`（想只给自己访问就填你的家宽 IP/32） |
| IP Protocol | `TCP` |
| Destination Port Range | `6868` |

### 2. 实例内部再放行（Ubuntu 镜像默认会拦）

Oracle 的 Ubuntu 镜像自带一条末尾 `REJECT all` 的 iptables 规则，官方文档要求手动插一条 ACCEPT：

```bash
sudo iptables -L INPUT --line-numbers -n      # 先看规则顺序，找到 REJECT 所在行号
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 6868 -j ACCEPT
sudo netfilter-persistent save                # 持久化，重启不丢
```

Oracle Linux / CentOS 系用 firewalld：

```bash
sudo firewall-cmd --permanent --add-port=6868/tcp && sudo firewall-cmd --reload
```

### 3. 装 Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker   # 免 sudo
docker compose version
```

### 4. 拉镜像 + 起服务

```bash
mkdir -p ~/finance-calendar && cd ~/finance-calendar

# 私有镜像需先登录一次（PAT 勾 read:packages）
echo "<你的PAT>" | docker login ghcr.io -u mrfrog1999 --password-stdin

curl -O https://raw.githubusercontent.com/mrfrog1999/FPC/main/docker-compose.deploy.yml
curl -O https://raw.githubusercontent.com/mrfrog1999/FPC/main/.env.example
cp .env.example .env
sed -i "s|SESSION_SECRET=.*|SESSION_SECRET=$(openssl rand -hex 32)|" .env

docker compose -f docker-compose.deploy.yml up -d
curl http://127.0.0.1:6868/api/health    # 期望 {"ok":true,...}
```

### 5. 自检不通时的排查顺序

```bash
docker compose -f docker-compose.deploy.yml ps      # 容器是否 Up / healthy
docker compose -f docker-compose.deploy.yml logs --tail=50
ss -tlnp | grep 6868                               # 宿主机端口是否在监听
sudo iptables -L INPUT -n | grep 6868              # 本机规则是否放行
```

外网仍打不开 → 99% 是 OCI 安全列表那条入站规则没加，或加在了别的 VCN / NSG 上。

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
