# JacarandaParse 前端

React + TypeScript + Vite + Ant Design。只通过 `/api/v1` 调用后端，不直连数据库或对象存储。

## 运行

先在项目根目录启动后端：`uv run jacara api --reload`（上传后要处理文件，还要开 `jacara serve` 和 `jacara worker`）。
然后在这个文件夹里：

```bash
npm install        # 第一次，或者 package.json 变了之后
npm run dev        # 开发服务器：http://127.0.0.1:5173（/api 自动转给 127.0.0.1:8000）
```

团队模式（`.env` 里 `JACARA_MODE=team`）会先显示登录页；个人模式直接进入。

> **npm 命令都在 `web/` 里运行。** 在根目录运行 `npm install` 会在根目录多出一套 `package.json` 和 `node_modules`，
> 网页里就会有两份 React，打开是白屏（控制台报 `Invalid hook call`）。删掉根目录那三样，回 `web/` 重装即可。
> 装了新包后页面异常，可以用 `npm run dev -- --force` 清掉 Vite 的缓存。

## 检查

```bash
npm run lint && npm run build
```

`build` 会先做 TypeScript 类型检查，再把网页打包到 `dist/`。

## 目录

| 目录 | 内容 |
| --- | --- |
| `src/main.tsx` | 入口：中文、主题色、TanStack Query |
| `src/App.tsx` | 路由：哪个网址显示哪个页面 |
| `src/api/` | 调用后端的唯一地方：`client.ts` 发请求、自动带 token、统一错误；一个功能一个文件（`auth.ts`、`documents.ts`）；`types.ts` 和后端 schemas 对应 |
| `src/auth/` | 登录：`AuthProvider`（全站共用"现在是谁"）、`useAuth()`、守门员 `RequireAuth`、登录页 |
| `src/components/` | 共用组件：`AppLayout`（左边菜单、顶上用户名和退出） |
| `src/features/` | 按功能分的页面：`documents/`（列表、上传、状态）、`review/`（审核）、`admin/` |
| `src/utils/` | 小工具：文件大小、时间格式 |

## 页面

| 网址 | 页面 | 谁能看 |
| --- | --- | --- |
| `/login` | 登录 | 没登录的人 |
| `/documents` | 文档列表：上传（要"上传文件"权限）、状态自动刷新、重试（要"重新处理"权限） | 登录了就能看 |
| `/documents/:id` | 审核工作台：左边原图和出处框，右边字段；修改、确认（要"修改字段"权限）、修改历史、完成审核（要"完成审核"权限）；↑↓ 切换字段，Enter 确认 | 登录了就能看 |
| `/admin/users` | 用户管理 | 管理员 |
| `/admin/audit-logs` | 审计日志 | 管理员 |

## 取数据：TanStack Query

读数据用 `useQuery`（`queryKey` 起名字、`queryFn` 调 `src/api/` 里的函数），改数据用 `useMutation`，
改完调用 `queryClient.invalidateQueries({ queryKey: [...] })`，相关列表会自动重新取。
文档列表有"排队中/处理中"的文件时每 3 秒刷新一次，全部处理完就停。
