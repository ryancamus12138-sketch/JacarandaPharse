# JacarandaParse 前端

React + TypeScript + Vite + Ant Design。只通过 `/api/v1` 调用后端，不直连数据库或对象存储。

## 两种运行方式

| | 开发 | 使用 |
| --- | --- | --- |
| 命令 | `npm run dev` | `npm run build`，然后在根目录 `uv run jacara start` |
| 网址 | <http://127.0.0.1:5173> | <http://127.0.0.1:8000> |
| 改了代码 | 保存后马上更新 | 要重新 `npm run build`，再重启 `jacara start` |
| 谁提供网页 | Vite 开发服务器（`/api` 转给 8000） | 后端直接提供 `dist/` 里的文件 |

### 开发

先在项目根目录启动后端：`uv run jacara api --reload`（上传后要处理文件，还要开 `jacara serve` 和 `jacara worker`）。
然后在这个文件夹里：

```bash
npm install        # 第一次，或者 package.json 变了之后
npm run dev        # 开发服务器：http://127.0.0.1:5173（/api 自动转给 127.0.0.1:8000）
```

### 打包给别人用

```bash
npm run build      # 先做 TypeScript 类型检查，再打包到 dist/
```

`dist/` 不进 Git，每次打包重新生成。后端（`backend/src/jacara/core/web.py`）这样提供它：

- `/api/...` 永远是接口；
- `dist/` 里真有的文件（`/assets/index-xxxx.js`）直接发，文件名带内容指纹，浏览器可以一直缓存；
- 其他网址（`/documents/123`）都发 `index.html`，由前端路由显示对应页面，所以刷新不会 404。

团队模式（`.env` 里 `JACARA_MODE=team`）会先显示登录页；个人模式直接进入。

> **npm 命令都在 `web/` 里运行。** 在根目录运行 `npm install` 会在根目录多出一套 `package.json` 和 `node_modules`，
> 网页里就会有两份 React，打开是白屏（控制台报 `Invalid hook call`）。删掉根目录那三样，回 `web/` 重装即可。
> 装了新包后页面异常，可以用 `npm run dev -- --force` 清掉 Vite 的缓存。

## 检查

```bash
npm run lint && npm run build
```

## 目录

| 目录 | 内容 |
| --- | --- |
| `src/main.tsx` | 入口：中文、主题色、TanStack Query |
| `src/App.tsx` | 路由：哪个网址显示哪个页面 |
| `src/api/` | 调用后端的唯一地方：`client.ts` 发请求、自动带 token、统一错误（`api()` 读 JSON、`apiBlob()` 读图片、`apiDownload()` 下载文件并读出文件名）；一个功能一个文件（`auth.ts`、`documents.ts`、`values.ts`、`users.ts`、`audit.ts`）；`types.ts` 和后端 schemas 对应 |
| `src/auth/` | 登录：`AuthProvider`（全站共用"现在是谁"）、`useAuth()`、守门员 `RequireAuth`、登录页、忘记密码页 |
| `src/components/` | 共用组件：`AppLayout`（左边菜单、顶上用户名、修改密码、退出）、`ChangePassword`、`NewPasswordFields` |
| `src/features/` | 按功能分的页面：`documents/`（列表、上传、状态、导出按钮）、`review/`（审核工作台）、`admin/`（用户管理、审计日志） |
| `src/utils/` | 小工具：文件大小、时间格式、`saveFile()`（把下载回来的文件交给浏览器保存） |

## 页面

| 网址 | 页面 | 谁能看 |
| --- | --- | --- |
| `/login` | 登录 | 没登录的人 |
| `/reset-password` | 忘记密码：用管理员给的重设码自己设新密码 | 不用登录 |
| `/documents` | 文档列表：上传（要"上传文件"权限）、状态自动刷新、重试（要"重新处理"权限）、勾选导出（zip 或汇总 Excel） | 登录了就能看 |
| `/documents/:id` | 审核工作台：左边原图和出处框（整页、缩放），右边字段；修改、确认（要"修改字段"权限）、修改历史、完成审核（要"完成审核"权限）、导出这一份；↑↓ 切换字段，Enter 确认 | 登录了就能看 |
| `/admin/users` | 用户管理：新建用户、勾选开通权限（马上生效）、改角色、停用、生成重设码 | 管理员 |
| `/admin/audit-logs` | 审计日志：谁、什么时候、做了什么；按操作筛选；只能看 | 管理员 |

团队模式下，右上角有"修改密码"（要输原密码）。

新加页面时网址不要用 `/api` 开头：那是留给后端接口的。

## 取数据：TanStack Query

读数据用 `useQuery`（`queryKey` 起名字、`queryFn` 调 `src/api/` 里的函数），改数据用 `useMutation`，
改完调用 `queryClient.invalidateQueries({ queryKey: [...] })`，相关列表会自动重新取。
文档列表有"排队中/处理中"的文件时每 3 秒刷新一次，全部处理完就停。

## 下载文件和显示图片

浏览器加载 `<img src>`、点 `<a href>` 下载时都**不会带登录 token**，团队模式下会 401。所以：

- 页面图片：`apiBlob()` 带 token 取回来，再用 `URL.createObjectURL` 变成 `blob:` 地址给 `<img>`；
- 导出 Excel / zip：`apiDownload()` 带 token 取回来，从 `Content-Disposition` 读出文件名（优先 `filename*`，中文不乱码），
  再用 `saveFile()` 交给浏览器保存。

多个文档默认每份一个 Excel、打包成 zip（浏览器没法直接下载文件夹，解压后就是文件夹）；
按钮旁边的下拉可以选"汇总到一个 Excel"，一行一张单据，方便对账。

## 规则和提示文字都在后端

前端不重写业务规则，后端出错时的 `{"detail": "..."}` 原样显示给人看。前端只做两种"提前检查"，为了少跑一趟：
表单必填、密码长度和两次输入一样、上传文件的类型和大小。真正挡住的都是后端。
