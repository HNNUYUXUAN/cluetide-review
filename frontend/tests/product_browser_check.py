"""Browser acceptance for the English BOT shell against its local dev preview."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "local-only" / "mainnet-product-qa"
OUT.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:5173/bot.html"
STORY = json.loads((ROOT / "frontend/src/bot-mainnet-story-data.json").read_text(encoding="utf-8"))
checks = []

with sync_playwright() as browser_api:
    browser = browser_api.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1536, "height": 1024}, device_scale_factor=1)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(BASE)
    page.get_by_role("heading", name="Every claim. A traceable history.").wait_for()
    assert "BOT Mainnet 677" in page.locator(".ct-proof-strip").inner_text()
    assert page.get_by_role("link", name="View contract", exact=True).get_attribute("href") == STORY["contract_url"]
    page.locator(".ct-hero-media img").evaluate("img => img.decode()")
    page.screenshot(path=str(OUT / "home-desktop.png"))
    page.get_by_role("link", name="Explore the UNI case").click()
    page.get_by_role("heading", name="One event. Three accountable steps.").wait_for()
    page.get_by_role("link", name="03 Corrected report").click()
    assert "step=v2" in page.url
    page.get_by_role("heading", name="What changed in v2").wait_for()
    page.reload()
    page.get_by_role("heading", name="What changed in v2").wait_for()
    page.screenshot(path=str(OUT / "case-desktop.png"))
    page.go_back()
    page.get_by_role("heading", name="Start with the evidence.").wait_for()
    page.go_forward()
    page.get_by_role("heading", name="What changed in v2").wait_for()
    checks.append("exact version route, reload, back and forward")
    for step in STORY["steps"]:
        page.goto(BASE + "#/cases/uniswap93?step=" + step["key"])
        assert "BOT Mainnet 677" in page.locator(".ct-chain-rows").inner_text()
        assert page.locator(".ct-explorer-link").get_attribute("href") == step["tx_url"]
        assert "BOT Mainnet 677" in page.locator(".ct-footer").inner_text()
    checks.append("three case routes preserve verified mainnet transaction identity")
    page.goto(BASE + "#/developers")
    assert "BOT Mainnet" in page.locator(".ct-network").inner_text()
    assert page.get_by_role("link", name="View verified deployment").get_attribute("href") == STORY["deployment"]["tx_url"]
    assert page.get_by_role("link", name="Archived testnet contract · 968").get_attribute("href").startswith("https://scan.bohr.life/address/")
    checks.append("mainnet deployment link and separate testnet archive remain accessible")
    page.locator('.ct-header nav').get_by_role("link", name="Verify", exact=True).click()
    assert "BOT Mainnet 677" in page.locator(".ct-sample-bundles").inner_text()
    for index, version in enumerate(["v1", "v2"]):
        page.locator(".ct-bundle-row").nth(index).get_by_role("button", name="Verify", exact=True).click()
        page.get_by_role("heading", name="File integrity verified").wait_for()
        assert f"Matches the recorded UNI {version}" in page.locator(".ct-result-statement").inner_text()
        assert "2 archived citation links still require review" in page.locator(".ct-citation-result").inner_text()
        checks.append(f"original {version} ZIP verified; citation gaps remain visible")
    page.screenshot(path=str(OUT / "verify-desktop.png"), full_page=True)
    page.locator('input[type="file"]').set_input_files({"name": "damaged.zip", "mimeType": "application/zip", "buffer": b"invalid archive"})
    page.get_by_role("heading", name="Verification could not complete.").wait_for()
    assert page.get_by_role("heading", name="File integrity verified").count() == 0
    checks.append("invalid uploaded ZIP clears earlier success")
    page.route("**/api/investigations/gcc-uniswap93-adaptive-20261007", lambda route: route.fulfill(status=404, content_type="application/json", body='{"detail":"Case not found"}'))
    page.goto(BASE + "#/cases/uniswap93?step=review")
    page.get_by_text("Original archived case required for this readback.", exact=False).wait_for()
    assert page.get_by_role("button", name="Verify this transaction").count() == 0
    checks.append("fresh local store gates archived transaction readback")
    page.unroute("**/api/investigations/gcc-uniswap93-adaptive-20261007")
    page.goto(BASE + "?investigation=gcc-uniswap93-adaptive-20261007")
    page.get_by_role("heading", name="Your investigation workspace.").wait_for()
    page.locator(".ct-workspace .app-shell").wait_for()
    page.screenshot(path=str(OUT / "workspace-desktop.png"))
    checks.append("legacy investigation query mounts local workspace")
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(BASE)
    page.locator(".ct-hero-media img").evaluate("img => img.decode()")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(OUT / "home-mobile.png"), full_page=True)
    page.get_by_role("button", name="Open navigation").click()
    page.locator('.ct-header nav').get_by_role("link", name="Case explorer").click()
    page.get_by_role("heading", name="One event. Three accountable steps.").wait_for()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(OUT / "case-mobile.png"), full_page=True)
    checks.append("390px mobile navigation and no horizontal overflow")
    assert not errors, errors
    browser.close()

(OUT / "checks.json").write_text(json.dumps({"checks": checks, "page_errors": errors}, indent=2), encoding="utf-8")
print(json.dumps({"passed": len(checks), "page_errors": errors, "screenshots": str(OUT)}, indent=2))
