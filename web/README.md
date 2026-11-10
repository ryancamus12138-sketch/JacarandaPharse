# JacarandaParse 前端

React + TypeScript + Vite + Ant Design。只通过 `/api/v1` 调用后端，不直连数据库或对象存储。

## 运行

先在项目根目录启动后端：`uv run jacara api --reload`。然后在这个文件夹里：

```bash
npm install        # 第一次，或者 package.json 变了之后
npm run dev        # 开发服务器：http://127.0.0.1:5173（/api 自动转给 127.0.0.1:8000）
```

团队模式（`.env` 里 `JACARA_MODE=team`）会先显示登录页；个人模式直接进入。

## 检查

```bash
npm run lint && npm run build
```

`build` 会先做 TypeScript 类型检查，再把网页打包到 `dist/`。

## 目录

| 目录 | 内容 |
| --- | --- |
| `src/main.tsx` | 入口：中文、主题色 |
| `src/App.tsx` | 路由：哪个网址显示哪个页面 |
| `src/api/` | 调用后端的唯一地方：`client.ts` 发请求、自动带 token、统一错误；一个功能一个文件（`auth.ts`……）；`types.ts` 和后端 schemas 对应 |
| `src/auth/` | 登录：`AuthProvider`（全站共用"现在是谁"）、`useAuth()`、守门员 `RequireAuth`、登录页 |
| `src/components/` | 共用组件：`AppLayout`（左边菜单、顶上用户名和退出） |
| `src/features/` | 按功能分的页面：`documents/`、`admin/` |

## 页面

| 网址 | 页面 | 谁能看 |
| --- | --- | --- |
| `/login` | 登录 | 没登录的人 |
| `/documents` | 文档 | 登录了就能看 |
| `/admin/users` | 用户管理 | 管理员 |
| `/admin/audit-logs` | 审计日志 | 管理员 |
