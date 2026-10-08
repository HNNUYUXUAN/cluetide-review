# 来源与证据边界 / Provenance and evidence scope

## 产品来源 / Product lineage

| Baseline / 基线 | Commit / 提交 | Role / 用途 |
| --- | --- | --- |
| GCC maintained product / GCC 维护源码 | `1b2fdd5d152f51e99d28563e10db39c94d015e3f` | Selected runtime, UI, tests, public evidence and attribution / 本版选取来源 |
| Shared research baseline / 共同研究起点 | `cf19b51af64fa289de19ccd70ab26425c09584d3` | Original GCC frontend and common investigation core / 前端与共同调查核心 |
| Independently packaged GCC / 独立打包来源 | `f21485d7078912c5894f4d599be26314a8b24782` | Historical independently reproduced product / 历史独立复现版本 |

The selected source originated in `HNNUYUXUAN/cluetide`. This repository starts its own review history. [source-manifest.json](source-manifest.json) maps the selected source paths and original digests to current release digests. The release adapts packaging, documentation, the UI source link and the regression-fixture layout. Core investigation behavior follows the maintained GCC baseline.

本版从原仓库 `HNNUYUXUAN/cluetide` 的 GCC 维护提交选取源码，并建立独立审阅历史。来源清单分别记录原路径、来源提交、原摘要和本版摘要。打包、说明、页面源码入口与回归 fixture 路径按本版结构维护，调查核心行为沿用该 GCC 基线。

## 公开证据 / Public evidence

`data/cases/uniswap93` and `data/cases/euler-20230313` retain public RPC captures, source excerpts, acquisition metadata, reproduction scripts and fixture manifests. See each case README for exact addresses, transactions, block ranges, source dates and observed scope. Original evidence and third-party license bytes are preserved.

两个案卷保留公开 RPC 捕获、短摘录、取得记录、重现脚本与 fixture 清单。地址、交易、窗口、来源日期和核读范围见各案 README。原始证据及第三方许可保持字节。

`frontend/public/gcc-demo` contains four synthetic-report replay ZIPs: UNI v1/v2 and Euler v1/v2. Their manifests identify the public observations and synthetic report text. The two `data/demo/bundles/real-acceptance-v*.zip` files are public evidence fixtures used by import, citation, publication and revision regression tests. They are evidence bundles, with fixed validated members.

`tests/fixtures/legacy-conclusion.json` extracts the historical public report at `data/demo/gateway-acceptance.json#/report`. `tests/fixtures/quality-regression.json` extracts the report and evidence from UNI run 1 (zero-based) and the report from Euler run 2 of the historical evaluation sources identified in the manifest. These selected fields preserve deterministic schema and semantic regression coverage. They do not summarize model accuracy.

前端四份回放 ZIP 明确标注合成报告。两份 real-acceptance 公开证据包供版本、引用、导入与原子发布测试使用。两个 JSON 回归 fixture 从来源报告中按清单所列字段选取，服务于确定性兼容与语义风险检查。

## 图像与许可 / Images and licenses

`docs/images/gcc-product.jpg` is a 2026-10-08 JPEG capture (1265 × 1565) of the maintained GCC production story page. `story.png` and `review.png` are accepted PNG UI captures (1536 × 1024) from the GCC frontend source baseline, dated 2026-10-08. They illustrate the product and labelled replay state; they are not model-evaluation results.

ClueTide code uses the existing project [MIT license](LICENSE), copied with its original bytes. Third-party license originals under `data/attribution/licenses` and `frontend/public/licenses` retain authorship and terms. Attribution metadata describes its own capture date and lockfile scope. See [attribution](data/attribution/README.md).

产品图像记录实际页面与回放状态。ClueTide 自有代码沿用项目 MIT 许可，第三方原件保留各自署名和条款。历史许可索引按其日期和对应锁文件解释。

## 核验方法 / Verification

Run `python scripts/verify_sources.py` to check the indexed current bytes. SHA-256 proves byte consistency with the index, while provenance, provider trust, source interpretation and reviewer independence require their own evidence. The RPC finalized anchor is provider-reported. The bounded observations do not establish complete transaction causality or incident-wide loss.

执行 `python scripts/verify_sources.py` 核对来源清单。字节一致性、来源真实性、提供者信任、解释与复核身份分别核验；有限窗口的观察支持明确范围内的结论。

## Bilingual product documentation · 2026-10-08

The current README offers separate Chinese and English product narratives with language links, generated concept artwork, editable diagrams and actual product screenshots. [Visual provenance](docs/readme-assets.md) and its [asset manifest](docs/readme-assets.json) record the production briefs, image origins and exact bytes. These documentation assets are release additions; the source baseline and original evidence artifacts above retain their recorded identities. The release integrity manifest includes the final documentation and image files.
