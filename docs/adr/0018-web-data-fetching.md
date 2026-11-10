# 0018 前端取数据：TanStack Query

- 状态：已采纳
- 日期：2026-11-10

## 背景

文档列表要：加载中和出错的显示、翻页、上传或重试后自动刷新，
以及有文件在排队或处理时定时刷新、处理完就停。用 `useState` + `useEffect` 自己写，
每个页面都要重复一套 loading、error、取消、定时器的代码，容易出错（比如离开页面后定时器还在跑）。

## 决策

**用 TanStack Query（`@tanstack/react-query`，MIT）管理从后端取的数据。**

- 读：`useQuery({ queryKey, queryFn })`。`queryFn` 只调 `src/api/` 里的函数（规则 8 不变）。
- 改：`useMutation`，成功或失败后 `invalidateQueries`，相关列表自动重新取。
- 定时刷新：`refetchInterval` 根据数据决定。文档列表里有 uploaded/processing 时 3 秒一次，否则不刷新。
  比 WebSocket 简单，本机使用、几秒延迟完全够用。
- 重试：4xx（没权限、找不到、格式错）不重试；连不上或 5xx 最多再试 2 次。
- 翻页用 `keepPreviousData`，新一页数据到了再换，表格不闪。

"现在是谁"（`AuthProvider`）仍然自己管：它要在 401 时被 `client.ts` 通知，逻辑和普通列表不同，量也小。

**上传** 用 Ant Design 的 `Upload.Dragger` 选文件，但不用它自带的上传，交给 `useMutation` 调 `src/api/documents.ts`，
这样 token、错误处理和其他请求一样。前端也检查 50 MB 和文件类型，只为省时间；真正的限制在后端。

**重试** 失败的文档，以及排队超过 1 分钟的文档（多半是 worker 没开）都显示"重试"。
后端两种状态都接受，worker 按状态认领，重复放进队列也不会处理两次。

## 后果

- 新页面取数据一律用 `useQuery`，不在组件里手写 `fetch` 和定时器。
- 状态的中文名和颜色集中在 `features/documents/StatusTag.tsx`，以后审核页也用它。

## 依赖与授权

| 包 | 授权 |
| --- | --- |
| @tanstack/react-query | MIT |
