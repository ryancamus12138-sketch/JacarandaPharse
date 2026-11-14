# 0064 发布个人版：GitHub Releases 下载，安装脚本和 jacara setup

状态：已采纳（2026-11）

## 决定

- **个人版放在 GitHub Releases 让人直接下载，不注册、不登录。** 项目是 Apache 2.0 开源的，装在用户自己的电脑上、
  数据只在本机，没有中心服务器，注册账号没有意义。用手机号、邮箱注册要公网服务器、短信资质（腾讯云短信要先提交签名
  归属方的资质和实名信息）、邮件服务器，都要联网，违反规则 1，也和 ADR 0021 冲突，所以不做。
- **团队版以后照自部署软件的做法**（Immich：第一个注册的人是管理员，由管理员添加别人；Gitea：装好后关掉自由注册），
  用"第一次打开时建管理员 + 邀请链接"，不用短信和邮件。不在这一版做。
- **发布包** = git 里提交了的源码（`git archive`，`.gitattributes` 里 `export-ignore` 掉 `.github`、`CLAUDE.md` 这些开发文件）
  + 打包好的网页（`web/dist`，用户不用装 Node.js）+ 双击启动的 `JacarandaParse.command`。模型文件不放进去：
  太大，而且各有自己的许可，由用户按说明从 Hugging Face 下载。
- **打包**：`scripts/build_release.sh`（要求没有未提交的改动），产出 `release/JacarandaParse-版本.zip` 和它的 SHA-256，
  发布说明取 CHANGELOG 里这个版本的那一节。推送 `v*` 标签时 `.github/workflows/release.yml` 自动打包，
  用 GitHub 官方的 `gh release create` 上传，不用第三方 Action。
- **安装**：`scripts/install.sh` 检查 uv、Docker，`uv sync --frozen --no-dev`（只装运行要的，不装 pytest 这些），
  再运行 `jacara setup`。
- **`jacara setup`**（`ops/setup.py`）：一步一步做、每步说结果和没做成怎么办，可以重复运行：
  1. `.env` 没有就照 `.env.example` 生成：三个必填密钥换成随机值（`secrets.token_hex`），`JACARA_MODE=personal`、
     `JACARA_API_DOCS=false`，文件权限 600。已有的 `.env` 不动，只提醒还是 `change-me` 的。
  2. `docker compose up -d --wait`：等数据库等服务起来。
  3. 建表（`alembic upgrade head`，升级新版本时也是这一步）。
  4. 检查打包好的网页、`llama-server`、大模型和视觉模型文件，缺了给出下载地址和放哪里。
- **日常使用**：双击 `JacarandaParse.command`，或 `uv run --frozen --no-dev jacara start`。
- **联网**：第一次安装要联网（Python 依赖、数据库镜像、模型）；装好以后运行时不联网（规则 1）。
  完全断网安装的离线包和断网验证一起做，在下一步。

## 版本

这是 1.0.0：第一个正式版本（照语义化版本，1.0.0 起对外的用法才算稳定）。之前内部开发的三个版本改叫 0.1.0、0.2.0、0.3.0
（0.y.z 是初始开发阶段），它们的标签改名、GitHub 上的发布改成草稿（只有有推送权限的人看得到）并标成预发布，
不再对外。以后的测试版用带 `-` 的版本号（例如 `v1.1.0-beta.1`），发布时自动标成预发布，不会顶掉"最新版"。
