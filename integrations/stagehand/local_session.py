"""Real Stagehand normal tabs in fresh owned profiles, with network-only guards."""
import asyncio
import json
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
from stagehand.extension_assets import extension_directory

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


async def open_routed_browser(stack, *, model, logging, downloads, session_stack=None, pooled=False):
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
    # The host's normal profile is owned and never used for customer traffic.
    await context.route(re.compile(r"^https?://"), lambda route: route.abort())
    connection = None
    if pooled:
        connection = context.browser
        if connection is None:
            raise RuntimeError("Owned persistent Chrome host does not expose context creation.")
        cdp = await connection.new_browser_cdp_session()
        # Only the genuine SDK extension in this disposable profile receives
        # incognito access. No personal profile or installed extension is touched.
        loaded = await cdp.send("Extensions.loadUnpacked", {
            "path": str(extension_directory()), "enableInIncognito": True,
        })
        if not loaded.get("id"):
            raise RuntimeError("Owned Stagehand extension lacks incognito registration.")
        await cdp.detach()
    browser = await local_browser.connect(cdp_url=f"http://127.0.0.1:{port}")
    stack.push_async_callback(browser.close)
    stagehand = await Stagehand.create(browser=browser, model=model, self_heal=False, logging=logging)
    stack.push_async_callback(stagehand.close)

    async def new_session(owned_stack, owned_downloads):
        isolated = await connection.new_context(
            locale="en-US", viewport={"width":1280, "height":800}, has_touch=False,
            accept_downloads=True, service_workers="block",
        )
        owned_stack.push_async_callback(isolated.close)
        return await routed_context(browser, isolated, pooled=True)

    if pooled:
        session = await new_session(session_stack, downloads)
    else:
        await context.unroute(re.compile(r"^https?://"))
        session = await routed_context(browser, context, pooled=False)
    if pooled:
        return browser, stagehand, *session, new_session
    return browser, stagehand, *session


async def routed_context(browser, context, *, pooled):
    await context.add_init_script(observer_script())
    guard = ResourceGuard()
    await context.route(re.compile(r"^https?://"), guard.route)
    await context.route_web_socket("**/*", lambda ws: ws.close(code=1008, reason="Unapproved socket"))
    marker = "about:blank#tryfolk-" + uuid4().hex
    if pooled:
        network_page = await context.new_page()
        await network_page.goto(marker)
        deadline = asyncio.get_running_loop().time() + 10
        while True:
            candidates = [p for p in await browser.context.pages() if await p.url() == marker]
            if len(candidates) == 1:
                page = candidates[0]
                # Require genuine SDK extension-world helpers before any fixture URL.
                await page.snapshot(include_iframes=True, timeout=5000)
                break
            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError("Fresh isolated context is unavailable to actual Stagehand.")
            await asyncio.sleep(.05)
    else:
        page = await browser.context.new_page()
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
    async def owned_session_reader():
        # This isolated browser context's cookie jar authenticates the app read.
        # Never send its session cookie through the model or callback pipe.
        url = guard.policy.origin + "/api/target/return"
        response = await context.request.get(url, max_redirects=0, timeout=3000)
        try:
            raw = await response.body()
            bounded = len(raw) <= 64_000
            valid = response.status == 200 and response.url == url and not response.headers.get("location")
            valid = valid and response.headers.get("content-type", "").split(";", 1)[0] == "application/json" and bounded
            body = json.loads(raw) if valid else {}
            return {"http_status": response.status, "same_origin_no_redirect": valid,
                    "body": body if isinstance(body, dict) else {}}
        finally:
            await response.dispose()
    guard.owned_session_reader = owned_session_reader
    boundary = ResponseBoundary(context, guard)
    await boundary.cover(network_page)
    guard.response_guard = boundary.cover
    return page, context, network_page, guard
