# GCC frontend / 产品前端

React + TypeScript provides the product story, UNI/Euler replay, local ZIP verification and investigation workbench. Build with `npm ci && npm run build`; run the tests with `npm test`. The production FastAPI service serves `dist` and `/api` together at port 5186.

React + TypeScript 实现故事、双案回放、本地 ZIP 复验和调查工作台。生产构建由 5186 端口 FastAPI 同源提供；开发端口 5187、构建预览端口 4187 代理到同一本地 API。

Hash routes support static hosting under `/gcc/`. `src/routing` controls case/version identity, while `src/state/case-resource.ts` isolates late responses. Replay assets in `public/gcc-demo` retain synthetic-report labels and source digests; `public/licenses` carries runtime MIT notices.

组件测试默认选择 `.venv/Scripts/python.exe`；其他环境设置 `CLUETIDE_TEST_PYTHON`，并安装 Chrome 与 Python Playwright。运行、状态及验证范围见 [开发说明](../docs/DEVELOPMENT.md)。
