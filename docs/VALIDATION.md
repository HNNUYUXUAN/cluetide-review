# 本版验证 / Release validation

验证日期 / Checked: **2026-10-08 (Asia/Shanghai)**.

Platform / 平台: **Windows**. The Linux/macOS command recipe has not been executed on those platforms. / Linux 与 macOS 命令尚待对应平台验证。

This record describes checks performed from the GCC review checkout. Source identifiers and preserved-byte mappings are in [SOURCE.md](../SOURCE.md). Model-quality claims require their own evaluation evidence.

本记录按 GCC 审阅目录的实际检查填写；来源及原件映射见来源说明。工程通过结果按具体命令和运行范围解释。

| Check / 检查 | Result / 结果 | Scope / 范围 |
| --- | --- | --- |
| Frontend dependency installation / 前端安装 | Passed / 通过 | `npm ci`, project lockfile |
| Production build / 生产构建 | Passed / 通过 | TypeScript + Vite, 59 transformed modules |
| Frontend tests / 前端测试 | 28 passed, 0 failed, 0 skipped / 28 项通过 | Node runner and two Chrome component suites; new checkout's Python runtime |
| Python dependency installation / Python 安装 | Passed / 通过 | New isolated environment, locked requirements, `pip check` |
| Critical backend regressions / 关键后端回归 | 244 passed / 244 项通过 | Eight modules, 26.43 seconds |
| Full backend suite / 全量后端测试 | 880 passed / 880 项通过 | New isolated environment and state; 59.79 seconds |
| Import and HTTP smoke / 导入与服务冒烟 | Passed / 通过 | Own source import; health, case catalog, production page and two replay ZIPs |
| Selected source digests / 来源摘要 | 461 verified / 461 文件通过 | Original-to-release mapping and current bytes |
| Full-package selection / 完整包清单 | Passed / 通过 | 487 files selected, public content and nested evidence validation |

The initial backend selection was `test_agent_assessments.py`, `test_gcc_quality_grounding.py`, `test_product_packaging.py`, `test_general_rpc_api.py`, `test_publication_atomicity.py`, `test_assessment_corrections.py`, `test_citation_validation.py` and `test_registry.py`. It covers the extracted historical report fixtures, public case flow, exact citations and amounts, atomic publication, version review and the curated package. The subsequent full suite passed all 880 tests, including image-metadata and CLI dependencies.

本轮先针对来源提取与交付结构选择八个模块，随后执行完整测试套件，880 项全部通过。两次数量分别描述各自命令的范围。测试使用独立本地状态，模型与链读取由离线或模拟输入提供。

Maintained Markdown links were checked locally. The preserved pywin32 `NOTICE.md` contains an upstream relative `License.txt` reference tied to its distribution layout; its bytes remain original, and the corresponding preserved pywin32 license files are indexed under `data/attribution/licenses/python/pywin32`.

当前维护文档本地链接已检查。pywin32 原始 NOTICE 中的相对许可链接对应其发行包目录；原文保持字节，所保留许可文件由本版许可目录与来源清单定位。

Visuals were read back from the approved product UI captures listed in `SOURCE.md`. The hosted demo deployment and audience access are verified separately at publication time.

截图已按来源清单逐图核看。在线 Demo 的部署与访问状态在发布阶段另行回读。
