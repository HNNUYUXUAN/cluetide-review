"""Product releases carry original third-party terms and the two public cases."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def packaging_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "package_demo.py"
    spec = spec_from_file_location("cluetide_product_packaging_test", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_product_selection_preserves_all_license_filename_variants(tmp_path):
    package = packaging_module()
    for name in package.REQUIRED_FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"product")
    originals = []
    for suffix in package.DIRECTORIES["data/attribution"]:
        name = "data/attribution/licenses/react/19_2_0/LICENSE" + suffix
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"original license\r\nCopyright\r\n")
        originals.append(name)
    euler = "data/cases/euler-20230313/raw/receipt.response.json"
    path = tmp_path / euler
    path.parent.mkdir(parents=True)
    path.write_bytes(b"{}\n")
    for excluded in (".env", "data/attribution/.env", "docs/project-manuscript.md"):
        path = tmp_path / excluded
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"private")
    selected, _ = package.select_files(tmp_path, include_demo=False, documents=[], demo_files=[])
    assert set(originals).issubset(selected)
    assert euler in selected
    assert not any(name.endswith(".env") or name == "docs/project-manuscript.md" for name in selected)
    assert all((tmp_path / name).read_bytes() == b"original license\r\nCopyright\r\n" for name in originals)


def test_actual_attribution_tree_is_fully_selected():
    package = packaging_module()
    root = Path(__file__).resolve().parents[1]
    selected = set(package.walk_allowlisted(root, "data/attribution", package.DIRECTORIES["data/attribution"]))
    originals = {path.relative_to(root).as_posix() for path in (root / "data/attribution").rglob("*") if path.is_file()}
    assert originals
    assert selected == originals


def test_product_selection_carries_rebuild_inputs_and_both_replay_copies(tmp_path):
    package = packaging_module()
    for name in package.REQUIRED_FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"product")
    expected = {
        "frontend/tests/routing.test.mjs", "frontend/tests/fixtures/case.json",
        "tests/fixtures/evidence.json", "frontend/scripts/rebuild-euler.py",
        "frontend/public/gcc-demo/fixtures.json", "frontend/dist/gcc-demo/fixtures.json",
        "frontend/public/gcc-demo/uniswap93-synthetic-v1.zip",
        "frontend/dist/gcc-demo/uniswap93-synthetic-v1.zip",
        "frontend/public/gcc-demo/LICENSE.txt", "frontend/dist/gcc-demo/LICENSE.txt",
        "frontend/public/licenses/react-LICENSE.txt", "frontend/dist/licenses/react-LICENSE.txt",
        "data/demo/bundles/real-acceptance-v1.zip",
    }
    for name in expected:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"public fixture")
    selected, _ = package.select_files(tmp_path, include_demo=False, documents=[], demo_files=[])
    assert expected <= set(selected)
    assert "frontend/preview-proxy.mjs" in selected
    assert "frontend/preview-proxy.d.mts" in selected


def test_actual_replay_and_runtime_license_files_are_selected():
    package = packaging_module()
    root = Path(__file__).resolve().parents[1]
    for relative in ("frontend/public/gcc-demo", "frontend/public/licenses"):
        selected = set(package.walk_allowlisted(root, relative, package.DIRECTORIES[relative]))
        originals = {path.relative_to(root).as_posix() for path in (root / relative).rglob("*") if path.is_file()}
        assert originals
        assert selected == originals
