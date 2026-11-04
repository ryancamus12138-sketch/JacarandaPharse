# 0010 开发环境基础设施：Docker Compose + .env

- 状态：已采纳
- 日期：2026-11-04

## 背景

后端需要 PostgreSQL（数据）、Valkey（任务队列）、SeaweedFS（上传的原始文件）。
每个开发者都应该能用同样的方式、一条命令启动它们，密码不能进入 Git。

## 决策

**`compose.yaml`（项目根目录）启动三个服务：**

| 服务 | 镜像 | 本机端口 |
| --- | --- | --- |
| PostgreSQL | postgres:18-alpine | 5432（可在 .env 改） |
| Valkey | valkey/valkey:9.0-alpine | 6379 |
| SeaweedFS（server -s3，单容器） | chrislusf/seaweedfs:4.52 | 8333（只开放 S3） |

- 镜像版本写死，不用 latest。
- 端口只绑定 127.0.0.1，同一网络的其他设备连不进来。
- 数据放在 Docker 数据卷里，删掉容器不丢数据（`docker compose down -v` 才会清空）。
- PostgreSQL 和 Valkey 配了健康检查。
- 必填的密码用 `${变量:?说明}`：没设置就启动失败，不会用默认密码悄悄运行。

**配置：** `.env`（不进 Git）+ `.env.example`（进 Git，只有说明和假值）。
docker compose 和代码读同一个 `.env`。代码用 pydantic-settings 的 `Settings`（`core/config.py`）读取：
环境变量 > `.env` > 默认值；自动转换和检查类型；密码用 `SecretStr`，打印时只显示星号。

**健康检查：** `jacara doctor` 检查 PostgreSQL、Valkey、SeaweedFS、llama-server，
失败时给出原因和解决办法。只读不写。

**新依赖：** pydantic-settings（MIT）、psycopg（LGPL-3.0）、redis（MIT）、boto3（Apache 2.0）。
psycopg 是 LGPL：只作为库使用、不修改其源代码，不要求本项目开源，与 Apache 2.0 兼容。

## 开发中遇到的情况

- 本机已有 PostgreSQL 14（官方安装包，占用 5432 并监听所有网络接口），
  在 `.env` 里改 `POSTGRES_PORT=5433` 即可，compose 和代码都跟着变。

## 后果

- 新开发者：装 Docker Desktop → `cp .env.example .env` 并填密码 → `docker compose up -d` → `jacara doctor`。
- 这份 compose 只用于开发。正式部署（多机、HTTPS、SeaweedFS 拆分、备份）以后在 `deploy/` 另写。
