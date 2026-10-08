# 开发与验证 / Development and validation

## 环境 / Environment

Python **3.12**, Node.js **24**, npm. `requirements-lock.txt` installs the Python package in editable mode; `frontend/package-lock.json` fixes frontend dependencies. Installation and launch commands are in [README](../README.md).

Python 3.12 与 Node.js 24 为验证环境；Python 和前端分别采用锁文件。本轮验证平台为 Windows，POSIX 安装命令待对应平台执行。安装步骤及完整启动命令见根 README。

Verification for this release ran on Windows. The Linux/macOS instructions are a corresponding shell recipe whose installation and browser behavior still require execution on those platforms.

| Command / 命令 | Purpose / 用途 |
| --- | --- |
| `python -m pytest -q` | Backend regression suite / 后端回归 |
| `python -m pip check` | Dependency compatibility / 依赖兼容性 |
| `npm test` in `frontend` | Routing, state, import and Chrome component checks / 前端状态与浏览器组件 |
| `npm run build` in `frontend` | TypeScript check and Vite production build / 类型校验与生产构建 |
| `python scripts/verify_sources.py` | Verify provenance manifest / 核对来源摘要 |
| `python scripts/package_demo.py --dry-run` | Validate full product selection / 核对完整产品清单 |

## 测试状态 / Test state

Run backend tests with `CLUETIDE_DATA_DIR` set to a new directory under `local-only/`. Use the Python executable from this repository's environment. The browser component suites select `.venv/Scripts/python.exe` by default on Windows; set `CLUETIDE_TEST_PYTHON` explicitly on other platforms. They launch installed Chrome through Python Playwright. A skipped browser suite is a narrower validation scope and must be reported.

后端测试使用 `local-only/` 下的独立数据目录；浏览器测试通过 Python Playwright 启动已安装的 Chrome。请明确测试 Python 路径并检查 skipped 数量。运行参数 `CLUETIDE_PREVIEW_ONLY=1` 用于产品离线预览；执行测试套件前清除此变量，由各测试控制配置。

## 服务与状态 / Service and state

FastAPI serves both `frontend/dist` and `/api` on loopback port **5186**. Build before starting the service. The default launcher selects public-cache evidence and an offline model. Development **5187** and preview **4187** proxy `/api` to 5186; proxy guards validate loopback Host, Origin and Fetch Metadata.

`local-data/cases.sqlite3` stores cases and publication state in one transaction. The JSON registry mirror can be recovered from committed state. A state directory has one service lease. Evidence versions retain distinct ZIP bytes and parent digests. GET polling is read-only. Export remains available for saved partial, stopped or failed investigation results, with their actual status.

本地服务在 5186 同源提供页面与 API；启动前构建前端。SQLite 原子保存案件与登记状态，JSON 镜像从已提交状态恢复。同一数据目录由单一服务租约持有。版本证据保留原始 ZIP 和父摘要；GET 轮询只读。早停、部分完成和失败结果按实际状态保存与导出。

## 可重建交付 / Rebuildable delivery

```bash
python scripts/package_demo.py --dry-run
python scripts/package_demo.py --output local-only/deliverables/cluetide-gcc.zip
python scripts/package_gcc_frontend.py --date 20261008 --output local-only/deliverables/cluetide-gcc-static.zip
```

The full package includes runtime source, tests, lockfiles, built frontend, public case captures, replay fixtures, documentation and attribution. The static package includes built frontend assets, replay ZIPs and runtime notices. Each package has a file inventory and digests. Build outputs and release ZIPs are local products; a new freeze should use a new filename.

完整包包含可运行源码、测试、锁文件、生产前端、公开案卷、回放、说明与许可。静态包包含构建结果、回放 ZIP 和运行依赖许可。发布脚本记录实际成员摘要。交付前在新目录解包并重复安装、构建和启动，以确认文件清单可复现。

## 评估方法 / Evaluation method

`src/cluetide/evaluation.py` implements a strongly conditioned script, one-shot and adaptive strategies. The script preloads case-specific evidence; one-shot produces one report after prechecks; adaptive can choose bounded follow-up reads. Compare like-for-like evidence scope, configuration, requests, timing and report semantics.

`scripts/gcc_evaluate.py` and `scripts/probe_gateway.py` are explicit live-evaluation tools. Their admission logic checks configured sessions and budgets. For deterministic review, use the default offline launcher and the preserved public regression fixtures. This release makes no new model-accuracy claim.

三种策略的证据覆盖与请求方式不同；比较时同时报告运行条件、请求数量、耗时及语义核读。实时评估工具保留会话与预算准入逻辑。离线流程和公开回归 fixture 提供确定性验证入口。
