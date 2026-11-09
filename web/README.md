# JacarandaParse 前端

React + TypeScript + Vite + Ant Design。只通过 `/api/v1` 调用后端，不直连数据库或对象存储。

## 运行

先在项目根目录启动后端：`uv run jacara api --reload`。然后在这个文件夹里：

```bash
npm install        # 第一次，或者 package.json 变了之后
npm run dev        # 开发服务器：http://127.0.0.1:5173（/api 自动转给 127.0.0.1:8000）
```

## 检查

```bash
npm run lint && npm run build
```

`build` 会先做 TypeScript 类型检查，再把网页打包到 `dist/`。

## 目录

| 目录 | 内容 |
| --- | --- |
| `src/main.tsx` | 入口：中文、主题色 |
| `src/App.tsx` | 页面（以后是路由） |
| `src/api/` | 调用后端：`client.ts` 统一发请求和处理错误，`types.ts` 是后端返回的数据格式 |
| `src/features/` | 按功能分的页面（文档、审核、管理），以后几课加 |
