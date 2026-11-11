# 0022：一个命令跑起来——API 直接提供网页

- 状态：已采纳
- 日期：2026-11-11

## 背景

到第 25 课为止，用一次要开四个终端：大模型、worker、API、`npm run dev`，打开的是 5173。
`npm run dev` 是开发服务器：改了代码马上刷新，但它慢、要装 Node、不适合给同事日常使用。
而且前端用的是浏览器路由（`/documents/123`），这种网址如果直接问后端，后端不认识就会 404。

## 决定

1. **API 顺便提供网页**：`npm run build` 把前端打包到 `web/dist/`，FastAPI 在所有接口之后加一条兜底路由（`core/web.py`）：
   - `/api/...` 永远是接口，不存在就是 404 JSON，不会变成网页；
   - `dist` 里真有的文件直接发；`assets/` 里的文件名带内容指纹，可以长期缓存（`immutable`）；
   - 其他网址都发 `index.html`（SPA 回退），由前端路由决定显示什么。`index.html` 设 `no-cache`，更新后马上生效；
   - 网址不能跳出 `dist`（`../../.env`），解析后的路径必须还在 `dist` 里面；
   - 还没打包时返回 503 和一句"怎么打包"，接口照常能用。
   网页和接口同一个地址（127.0.0.1:8000），所以不需要跨域设置，token 的用法也不变。
2. **`jacara start`**：一个命令启动 API 和 worker；加 `--model` 时同时启动大模型。
   worker 和大模型是子进程，API 在主进程；Ctrl + C 时先 `terminate`，10 秒不退再 `kill`，不留孤儿进程。
   不用 `--reload`：那是开发用的。
3. **启动前检查密钥**：`.env` 里 `POSTGRES_PASSWORD`、`S3_ACCESS_KEY`、`S3_SECRET_KEY` 还是空的或 `change-me` 时，
   `jacara start` 拒绝启动，`jacara doctor` 报错。只说是哪一项，不打印值。
4. **接口文档可以关**：`JACARA_API_DOCS=false` 时 `/docs`、`/openapi.json` 不存在。开发默认打开。
5. 开发方式不变：`npm run dev`（5173）+ `jacara api --reload`。

## 没有选的方案

- **Nginx 提供网页、反向代理 API**：多一个程序要装要配。等第 27 课 Docker Compose 再看需不需要。
- **把 `web/dist` 提交进 Git**：打包产物每次都变，会让提交记录很乱。离线安装包（第 28 课）在打包时生成。
- **用 Python 的 multiprocessing 同时跑 worker**：macOS 上 onnxruntime 在复制出来的子进程里容易崩溃（见 0013），
  用 subprocess 启动全新的进程更稳。

## 影响

- 日常使用只要 `docker compose up -d` 和 `uv run jacara start --model 模型.gguf`，打开 http://127.0.0.1:8000。
- 改了前端要重新 `npm run build`，8000 上才看得到；开发时用 5173 不受影响。
- 后端接口必须都在 `/api/` 下面，否则会和前端的网址冲突。
