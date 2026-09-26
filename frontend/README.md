# Fin Agent React 前端

这是 Fin Agent 的主 React + TypeScript + Vite 前端。开发时通过 Vite 连接现有 Flask API；生产构建存在时，Flask `/assistant` 会直接加载 React，旧 Jinja 页面保留在 `/assistant/legacy` 作为开发回退。

## 本地运行

后端默认运行在 `http://127.0.0.1:22053`：

```bash
npm install
npm run dev
```

打开 `http://127.0.0.1:22054/`。Vite 会把 `/api` 请求代理到现有后端。

渲染对象实验页：`http://127.0.0.1:22054/renderers`

如后端端口不同：

```bash
VITE_API_TARGET=http://127.0.0.1:PORT npm run dev
```

## 验证

```bash
npm run typecheck
npm test
npm run build
```

构建完成后重启 Flask，访问 `http://127.0.0.1:22053/assistant` 即可使用 React 版本；构建产物仍保持为本地生成文件，不进入 Git。

## 并行开发边界

- 后端负责线程、访客身份、上下文、顶层意图、自定义工具状态和 `surface_blocks` 协议。
- `src/api.ts`、`src/types.ts`、`src/surface.ts` 是前后端协议适配边界，改动这些文件时需要同步检查 Flask API。
- React 组件和样式只负责展示与交互，不重新推断金融业务，也不在浏览器执行模型生成的代码。
- 旧响应的 `items/workspace/task_state` 由 `surface.ts` 兼容归一化；新功能优先输出 Agent Surface 语义块。

核心流式状态在 `src/surface.ts` 中管理。当前同时兼容现有的 `run_started/block/done/error` 事件，并为 Agent Surface v1 事件名称保留适配入口。

渲染层位于 `src/rendering/`：

- `model.ts`：稳定的语义对象、金融数据、图结构和代码运行类型。
- `normalize.ts`：把旧 `kline/line/flow/chatflow/code` 与 Agent Surface v1 归一化。
- `registry.ts`：依据 `kind + shape + semantic + capabilities` 选择可信 Renderer。

`chatflow` 作为旧流程图别名兼容，不作为新的顶层业务类型。代码只负责展示后端运行状态，浏览器不会执行模型生成的代码。
