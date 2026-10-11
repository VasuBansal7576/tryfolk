"""Real Stagehand normal tabs in fresh owned profiles, with network-only guards."""
import asyncio
import os
import re
from pathlib import Path
import shutil
import socket
import sys
import tempfile
from uuid import uuid4

from playwright.async_api import async_playwright
from stagehand import Stagehand, local_browser

from tryfolk.browser_network import ResourceGuard
from tryfolk.response_boundary import ResponseBoundary
from tryfolk.native_card_observation import observer_script


def chrome_executable():
    # Match the installed Stagehand launcher's browser selection. Never select
    # an existing Chrome instance or a personal profile; only an executable.
    configured = os.environ.get("CHROME_PATH")
    if configured and Path(configured).is_file():
        return configured
    if sys.platform == "darwin":
        candidates = (
            "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        )
        for candidate in candidates:
            if Path(candidate).is_file():
                return candidate
    else:
        for name in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser"):
            if path := shutil.which(name):
                return path
    raise RuntimeError("Install a supported local Chrome executable; no hosted session is substituted.")


async def open_routed_browser(stack, *, model, logging, downloads):
    playwright = await async_playwright().start()
    stack.push_async_callback(playwright.stop)
    profile = Path(tempfile.mkdtemp(prefix="stagehand-profile-", dir=downloads.parent))
    stack.callback(shutil.rmtree, profile, ignore_errors=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    # The persistent context is a fresh normal Chrome profile, so the actual
    # Stagehand extension can inject its helpers without incognito grants.
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=profile, executable_path=chrome_executable(),
        headless=True, chromium_sandbox=True, service_workers="block",
        locale="en-US", viewport={"width":1280, "height":800}, has_touch=False,
        accept_downloads=True, downloads_path=downloads,
        ignore_default_args=["--disable-extensions"],
        args=[f"--remote-debugging-port={port}", "--enable-unsafe-extension-debugging",
              "--remote-allow-origins=*"],
    )
    stack.push_async_callback(context.close)
    await context.add_init_script(observer_script())
    guard = ResourceGuard()
    # The SDK's own chrome-extension://.../blank.html is an internal runtime
    # document, not an HTTP resource. Keep all actual HTTP(S) traffic gated.
    await context.route(re.compile(r"^https?://"), guard.route)
    await context.route_web_socket("**/*", lambda ws: ws.close(code=1008, reason="Unapproved socket"))
    browser = await local_browser.connect(cdp_url=f"http://127.0.0.1:{port}")
    stack.push_async_callback(browser.close)
    stagehand = await Stagehand.create(browser=browser, model=model, self_heal=False, logging=logging)
    stack.push_async_callback(stagehand.close)
    page = await browser.context.new_page()
    marker = "about:blank#tryfolk-" + uuid4().hex
    await page.goto(marker)
    deadline = asyncio.get_running_loop().time() + 5
    while True:
        candidates = [p for p in context.pages if p.url == marker]
        if len(candidates) == 1:
            network_page = candidates[0]
            break
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError("The actual Stagehand normal tab was not found in its owned routed context.")
        await asyncio.sleep(0.05)
    guard.primary_page = network_page
    boundary = ResponseBoundary(context, guard)
    await boundary.cover(network_page)
    guard.response_guard = boundary.cover
    return browser, stagehand, page, context, network_page, guard
