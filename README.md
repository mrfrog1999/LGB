# 龙钱包 · 现金理财监控

抓取建行 / 邮储 / 浦发三家银行理财产品的七日年化与万份收益，记录持仓、支出与收益，支持每日定时邮件推送、年化预警、多账号权限与数据备份恢复。

## 功能

- 每日自动抓取三家银行官网公告，按每万份收益逐日复利计算收益
- 持仓管理：新买入、已持有导入、编辑、加仓、删除、支出记录
- 总览：三家银行年化卡片、收益曲线、持仓明细
- 邮件报告 + 每日定时推送 + 七日年化跌破阈值预警
- 登录鉴权：管理员（可修改）/ 普通成员（只读）
- 全量数据导出 / 导入（含设置与账号）

## 默认账号

| 角色 | 用户名 | 密码 |
|---|---|---|
| 管理员 | `admin` | `admin123` |

> 登录后请立即在「设置 → 账号与权限 → 修改我的密码」中更改默认密码。

## 一键部署（Docker Compose）

### 方式一：从源码构建（推荐）

```bash
git clone https://github.com/mrfrog1999/FPC.git
cd FPC
# 可选：设置会话密钥
export SECRET_KEY="换成一段随机强字符串"
docker compose up -d --build
```

### 方式二：使用 GitHub Actions 构建的镜像

每次 push 到 main 分支，GitHub Actions 会自动构建镜像并推送到 GHCR。使用时把 `docker-compose.yml` 中的 `build: .` 注释掉、并启用 `image: ghcr.io/mrfrog1999/fpc:latest` 即可：

```yaml
services:
  longwallet:
    # build: .
    image: ghcr.io/mrfrog1999/fpc:latest
    ...
```

然后：

```bash
docker compose up -d
```

部署完成后访问：

```
http://服务器IP:6868/?sharecode=57BQDYVY3U
```

## 端口与数据持久化

- **运行端口：6868**（`docker-compose.yml` 中 `6868:6868`，容器内 `PORT=6868`）
- **数据持久化**：SQLite 数据库位于 `/app/data/longwallet.db`，通过命名卷 `longwallet-data` 挂载。容器停止、删除、重建后数据均不丢失。

常用命令：

```bash
# 查看日志
docker compose logs -f

# 停止（数据保留在卷中）
docker compose down

# 完全删除数据卷（危险：会清空所有持仓与设置）
docker compose down -v

# 备份数据库（复制到宿主机当前目录）
docker cp longwallet:/app/data/longwallet.db ./longwallet-backup.db
```

> 也可在系统「设置 → 数据备份与恢复」中导出/导入完整 JSON 备份（含设置与账号）。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PORT` | `6868` | Web 监听端口 |
| `DATA_DIR` | `/app/data` | 数据库目录（应指向持久化卷） |
| `OPENSSL_CONF` | `/app/openssl_legacy.cnf` | 建行 TLS 兼容配置 |
| `SECRET_KEY` | `longwallet-monitor-secret-key` | 会话签名密钥，生产建议修改 |
| `TZ` | `Asia/Shanghai` | 时区（影响定时任务） |

## 本地开发运行

```bash
pip install -r requirements.txt
OPENSSL_CONF=$(pwd)/openssl_legacy.cnf python3 app.py
```

默认监听 `8866`（本地未设置 `PORT` 时）。
