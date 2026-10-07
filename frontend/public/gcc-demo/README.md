# GCC 公开历史回放素材

本目录承载浏览器本地回放与复验使用的公开素材。报告、引导步骤和复核意见属于合成示例；模型请求、交易签名和广播均为 0。

## 来源与版本

- `uniswap93-synthetic-v1.zip`、`uniswap93-synthetic-v2.zip`：ClueTide 公开 UNI 回放包，原始字节按来源清单保留。链上观察取得于 2026-10-06 UTC；窗口为 Ethereum 24106368–24106388。来源提交与对应文件见 [GCC 来源说明](https://github.com/HNNUYUXUAN/cluetide-review/blob/GCC/SOURCE.md)。
- UNI 两个版本均保存相邻两块的 totalSupply 观察。v2 的金额主张明确 Transfer 本身不能证明 totalSupply 变化；v2 的 `parent_manifest_hash` 精确绑定 v1。
- `euler-20230313-synthetic-v1.zip`、`euler-20230313-synthetic-v2.zip`：由 `frontend/scripts/rebuild-euler.py` 从本仓库 `data/cases/euler-20230313/rpc.json`、`normalized-transfers.json`、`sources.json` 生成。链上观察取得于 2026-10-07 UTC；范围仅为 Ethereum 16817995–16817997 的 Euler 主体 DAI 三条 Transfer。v2 在合成文本中明确净 Transfer 流的范围，并保留 v1 的父摘要。
- `fixtures.json`：四个 ZIP 的 SHA-256 与内部 manifest SHA-256，供发布文件一致性核对。全部报告带合成标识与源文件摘要。
- `bundle.js`：原样复用既有公开应用的五文件 ZIP_STORED 读取器；版权与 MIT 许可见 `LICENSE.txt`。

公开来源在包内保留发布者、原始链接、定位、项目摘要和实际核读范围。Euler 固定源码的指针保留 GPL-2.0-or-later 说明；本站未打包该源码原件。

## 本地复验范围

读取器检查 16 MiB 上限、固定五文件目录、ZIP 路径和边界、CRC32、文件大小与 SHA-256、规范 JSON、地址与金额格式以及包内报告引用。导入内容只在浏览器中读取，不上传。

文件摘要校验针对字节一致性；它不能证明事实真实性、来源真实性、查询完整性或复核者独立身份。页面将既有公开观察、合成报告和本地复验结果分别标识。

## 重建 Euler 公开示例

在前端项目执行 `python scripts/rebuild-euler.py <GCC仓库绝对路径>`。脚本使用固定 ZIP 时间戳，重复运行可取得相同字节，并更新四包摘要清单。UNI 原始公开包保持不变。
