# 龙钱包 · 现金理财监控

一个自托管的现金理财监控系统：自动抓取银行理财产品公告，记录持仓、支出与收益，提供可视化总览、每日定时邮件日报、年化预警、多账号权限与全量数据备份恢复。

## 功能特性

- **多数据源抓取**：定时抓取建设银行、邮储银行、浦发银行的理财产品七日年化与每万份收益
- **收益计算**：按每万份收益逐日计息，支持新买入、已持有导入、加仓、编辑、删除与支出记录
- **可视化总览**：三家银行年化卡片、收益走势、持仓明细，适配桌面端与移动端
- **邮件日报**：HTML 格式报告，兼容移动端邮箱
- **定时任务**：每日推送数据报告；每日定时补充抓取（仅更新数据，不发邮件）
- **年化预警**：产品七日年化跌破阈值时自动邮件提醒
- **权限管理**：管理员（读写）/ 普通成员（只读），密码哈希存储
- **数据安全**：全量 JSON 导出 / 导入（含设置与账号），SQLite 本地持久化

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11 · Flask · SQLite · APScheduler |
| 前端 | 原生 HTML / CSS / JavaScript · SVG 图标 |
| 抓取 | curl 子进程 + 银行站点 TLS 兼容配置 |
| 部署 | Docker · Docker Compose · GitHub Actions (GHCR) |

## 项目结构

```text
.
├── app.py                  # Web 路由、鉴权、定时任务调度
├── service.py              # 业务逻辑：数据聚合、收益计算、更新流程
├── db.py                   # SQLite 数据访问与备份导入导出
├── calculator.py           # 收益计算
├── config.py               # 配置项（均可用环境变量覆盖）
├── ccb.py / psbc.py / spdb.py  # 三家银行的数据源抓取
├── mailer.py               # 邮件报告生成与 SMTP 发送
├── openssl_legacy.cnf      # 银行站点 TLS 重协商兼容配置
├── templates/              # 页面模板：总览、设置、登录
├── static/                 # 静态资源
├── Dockerfile              # 镜像构建
├── docker-compose.yml      # 通用部署编排（GHCR 镜像版）
├── docker-compose.build.yml # 通用部署编排（源码本地构建版）
└── .github/workflows/      # 自动构建镜像并推送到 GHCR
```

## 快速开始

项目提供两份通用 Compose 文件，任选其一：

| 文件 | 说明 | 适用 |
|---|---|---|
| `docker-compose.yml` | 从 GHCR 拉取镜像（**推荐**） | 1Panel、飞牛 NAS、命令行 |
| `docker-compose.build.yml` | 用源码在本地构建（无需镜像仓库） | 已上传源码到服务器的场景 |

部署前请把文件里的 `SECRET_KEY` 改为随机强字符串：

```bash
openssl rand -hex 32
```

部署完成后访问 `http://<服务器地址>:6868/`，用默认管理员账号登录并**立即修改密码**。

### 方式一：1Panel 部署

1. 进入 **容器 → 编排 → 新建编排**
2. 粘贴 `docker-compose.yml` 的内容（镜像版），或 `docker-compose.build.yml`（源码构建版）
3. 修改 `SECRET_KEY`，端口按需要调整（默认 `6868:6868`）
4. 点击**部署**，等待镜像拉取与容器启动
5. 浏览器访问 `http://<服务器IP>:6868/`

> 若使用源码构建版，需先把整个项目目录上传到服务器，并在该目录新建编排。

### 方式二：飞牛 NAS（FnOS）部署

1. 打开 **Docker → 项目 → 新增项目**（Compose 方式）
2. 粘贴 Compose 文件内容，设置项目名称与存放路径
3. 修改 `SECRET_KEY`，确认端口 `6868`
4. 点击**部署 / 启动**
5. 浏览器访问 `http://<飞牛IP>:6868/`

> 飞牛上如需绑定挂载到特定目录，把 `./data` 改成绝对路径（如 `/volume1/docker/longwallet/data`）。

### 方式三：命令行部署

```bash
# 镜像版
docker compose up -d

# 或源码构建版
docker compose -f docker-compose.build.yml up -d --build
```

### 镜像说明

推送到 `main` / `master` 分支会自动构建镜像并推送到 GHCR（`ghcr.io/<你的用户名>/lgb:latest`）。若镜像为私有，需先在服务器登录：

```bash
echo <TOKEN> | docker login ghcr.io -u <用户名> --password-stdin
```

### 方式四：本地开发运行

```bash
pip install -r requirements.txt
OPENSSL_CONF=$(pwd)/openssl_legacy.cnf python3 app.py
```

默认监听 `8866` 端口（未设置 `PORT` 时）。

## 端口与数据持久化

- **运行端口**：`6868`（Compose 中 `6868:6868`，容器内 `PORT=6868`）
- **数据持久化**：SQLite 数据库位于 `/app/data/longwallet.db`，通过命名卷 `longwallet-data` 挂载，容器停止、删除、重建后数据均不丢失

常用命令：

```bash
docker compose logs -f                       # 查看日志
docker compose down                          # 停止（数据保留在卷中）
docker compose down -v                       # 删除数据卷（危险：清空所有数据）
docker cp longwallet:/app/data/longwallet.db ./backup.db   # 备份数据库
```

也可以在系统「设置 → 数据备份与恢复」中导出 / 导入完整 JSON 备份。

## 配置

所有配置项均可通过环境变量覆盖：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PORT` | `6868`（Docker）/ `8866`（本地） | Web 监听端口 |
| `DATA_DIR` | `/app/data`（Docker） | 数据库目录，应指向持久化卷 |
| `OPENSSL_CONF` | `/app/openssl_legacy.cnf` | 银行站点 TLS 兼容配置 |
| `SECRET_KEY` | 见 `config.py` | 会话签名密钥，**生产环境务必修改** |
| `TZ` | `Asia/Shanghai` | 时区，影响定时任务 |
| `PUSH_HOUR` / `PUSH_MINUTE` | `9` / `0` | 每日数据推送时间 |
| `SECOND_FETCH_HOUR` / `SECOND_FETCH_MINUTE` | `14` / `0` | 每日补充抓取时间（不发邮件） |
| `ALERT_ANNUAL` | `1.0` | 年化预警阈值（%） |
| `SHARECODE` | 空 | 访问码兜底值，仅在需要 sharecode 访问的部署网关后使用；普通部署无需设置 |

推送时间、预警阈值、SMTP、收件人等也可在「设置」页中调整。

## 定时任务

| 任务 | 默认时间 | 行为 |
|---|---|---|
| 每日主任务 | 09:00 | 抓取最新净值、重算收益、发送邮件日报 |
| 补充抓取 | 14:00 | 仅抓取并更新数据，不发送邮件 |

任务时间可在「设置 → 每日推送」中修改，也可通过上表环境变量配置。

## 账号与权限

- 首次启动会创建一个默认管理员账号（用户名与初始密码见 `db.py` 初始化逻辑），**登录后请立即修改密码**
- 管理员：可读写全部数据与设置、管理账号、导出数据
- 普通成员：只读，可查看数据与预览报告、修改自己的密码
- 密码以哈希方式存储，不保存明文

## 安全说明

- 请勿将数据库文件（`*.db`）、`.env`、`data/` 目录提交到版本库，这些可能包含持仓、邮箱授权码、密码哈希等敏感信息（`.gitignore` 已默认排除）
- 生产环境务必修改 `SECRET_KEY` 与默认管理员密码
- 邮件授权码等凭据仅在服务端保存，前端导出 / 导入备份需注意保管

## 数据说明

产品列表中的日期为**净值归属日**（银行公告对应的净值日期），与程序实际抓取时间不同；银行通常在当日公布上一交易日的净值，两者可能相差一天。

## License

本项目仅供个人学习与自托管使用。数据来源为各银行公开公告，请以银行官方渠道为准。
