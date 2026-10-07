"""打包本分支已构建的 GCC 静态产品故事与双案 Demo。"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def inventory(files: dict[str, bytes]) -> list[dict]:
    return [{"path": name, "bytes": len(data), "sha256": digest(data)} for name, data in sorted(files.items())]


def package(output: Path, package_date: datetime) -> dict:
    dist = ROOT / "frontend/dist"
    if not (dist / "index.html").is_file():
        raise ValueError("请先在本分支 frontend 执行 npm ci 和 npm run build。")
    files = {}
    for path in dist.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise ValueError("静态构建目录应仅包含普通文件和目录。")
        if path.is_file():
            files[path.relative_to(dist).as_posix()] = path.read_bytes()
    for path in (ROOT / "frontend/public").rglob("*"):
        if path.is_file() and files.get(path.relative_to(ROOT / "frontend/public").as_posix()) != path.read_bytes():
            raise ValueError("请重新构建，使生产素材与本分支 public 原件一致。")
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    files["README.txt"] = (
        "ClueTide GCC 产品故事与双案静态 Demo\n\n"
        "解压后在本目录执行：python -m http.server 8080 --bind 127.0.0.1\n"
        "浏览器打开 http://127.0.0.1:8080/ 。页面资源通过 HTTP 读取。\n"
        "故事、UNI/Euler 回放、准确版本下载和浏览器本地 ZIP 复验使用随包素材。\n"
        "回放采用公开历史观察与明确标注的合成报告，来源和许可随案卷保留。\n"
        "实际调查使用完整 GCC 产品包，在 http://127.0.0.1:5186 同源运行 UI 和 API。\n"
        "开发服务 5187 和构建预览 4187 将 /api 代理至本地 5186。\n"
        "文件摘要校验针对字节，事实解释依据案卷来源。\n"
        "gcc-demo/LICENSE.txt 与 licenses/ 保存适用的原始许可。\n"
    ).encode("utf-8")
    manifest = {"format": "cluetide-gcc-static/v2", "source_head_commit": commit,
                "source_path": "frontend/dist", "source_scope": "本工作树生产构建逐文件字节索引",
                "kind": "gcc_static_demo", "files": inventory(files)}
    files["gcc-demo-manifest.json"] = encoded(manifest)
    output = output.resolve()
    if not output.is_relative_to((ROOT / "local-only/deliverables").resolve()) or output.suffix != ".zip":
        raise ValueError("输出须为本工作树 local-only/deliverables 内的 ZIP。")
    for path in (output, *output.parents):
        if path.is_symlink() or path.is_junction():
            raise ValueError("输出路径须位于普通目录。")
        if path == ROOT:
            break
    if output.exists():
        raise ValueError("输出已存在；请使用新的发布文件名。")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for name, payload in sorted(files.items()):
            item = zipfile.ZipInfo(name, date_time=(package_date.year, package_date.month, package_date.day, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100644 << 16
            bundle.writestr(item, payload)
    with zipfile.ZipFile(output) as bundle:
        if set(bundle.namelist()) != set(files) or any(bundle.read(name) != payload for name, payload in files.items()) or bundle.testzip() is not None:
            raise ValueError("静态发布包回读失败。")
    result = {**manifest, "archive": {"path": output.relative_to(ROOT).as_posix(), "bytes": output.stat().st_size,
              "sha256": digest(output.read_bytes()), "members": len(files)}, "readback": "passed"}
    output.with_suffix(".index.json").write_bytes(encoded(result))
    return result["archive"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="YYYYMMDD")
    parser.add_argument("--output", type=Path, default=ROOT / "local-only/deliverables/cluetide-gcc-static.zip")
    args = parser.parse_args()
    try:
        result = package(args.output, datetime.strptime(args.date, "%Y%m%d"))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"status": "created", **result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
