"""Actual localhost-only Chrome cache/guard regression; no provider/payment proof."""

import asyncio
import json
import threading
import sys
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT / "integrations/stagehand"))
sys.path.insert(0, str(ROOT / "src"))
from local_session import chrome_executable  # noqa: E402
from native_cache import configure  # noqa: E402

configuration = configure() if "--patched-driver" in sys.argv else None
from playwright.async_api import async_playwright  # noqa: E402

counts = {"asset": 0, "post": 0, "nocache": 0, "blocked": 0}
route_counts = {"asset": 0, "post": 0, "nocache": 0, "blocked": 0}
responses = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/blocked"):
            counts["blocked"] += 1
        if self.path.startswith("/nocache"):
            counts["nocache"] += 1
            body = b"{}"
            content = "application/json"
            cache = "no-store"
        elif self.path.startswith("/cacheable."):
            counts["asset"] += 1
            body = b"window.cacheProbeLoaded=true;"
            content = "application/javascript"
            cache = "public,max-age=86400,immutable"
        elif self.path.startswith("/cookie"):
            body = json.dumps({"cookie": self.headers.get("Cookie", "")}).encode()
            content = "application/json"
            cache = "no-store"
        else:
            body = b'<html><body><script src="/cacheable.012345abcdef.js"></script><button>Local probe</button></body></html>'
            content = "text/html"
            cache = "no-store"
        self.send_response(200)
        self.send_header("Content-Type", content)
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        counts["post"] += 1
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(body)


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
origin = f"http://127.0.0.1:{server.server_port}"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=chrome_executable(), headless=True)
        context = await browser.new_context(service_workers="block")

        async def route(r):
            if "/blocked" in r.request.url:
                await r.abort()
                return
            if r.request.method == "POST":
                route_counts["post"] += 1
            elif "/cacheable." in r.request.url:
                route_counts["asset"] += 1
            await r.continue_()

        await context.route("**/*", route)
        page = await context.new_page()
        session = await context.new_cdp_session(page)

        async def response(event):
            responses.append(event.get("responseStatusCode"))
            await session.send("Fetch.continueResponse", {"requestId": event["requestId"]})

        session.on("Fetch.requestPaused", response)
        await session.send("Network.enable")
        await session.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Response"}]})
        if "--native-cache" in sys.argv:
            await session.send("Network.setCacheDisabled", {"cacheDisabled": False})
        for n in (1, 2):
            await page.goto(origin + f"/page?round={n}")
            await page.evaluate(
                "async () => { await fetch('/nocache'); await fetch('/post',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}); }"
            )
        blocked = await page.evaluate(
            "async () => { try { await fetch('/blocked'); return false; } catch { return true; } }"
        )
        await context.add_cookies([{"name": "isolated", "value": "one", "url": origin}])
        first = await page.evaluate("async () => (await (await fetch('/cookie')).json()).cookie")
        second_context = await browser.new_context(service_workers="block")
        await second_context.route("**/*", route)
        await second_context.add_cookies([{"name": "isolated", "value": "two", "url": origin}])
        second_page = await second_context.new_page()
        await second_page.goto(origin + "/cookie")
        second = json.loads(await second_page.locator("body").inner_text())["cookie"]
        cookies_isolated = first == "isolated=one" and second == "isolated=two"
        await browser.close()
    print(
        json.dumps(
            {
                "source": "localhost_only_actual_Chrome_cache_probe",
                "patched_driver_configuration": configuration,
                "separate_CDP_cache_enabled": "--native-cache" in sys.argv,
                "actual_HTTP_counts": counts,
                "request_guard_counts": route_counts,
                "response_guard_observations": len(responses),
                "unapproved_request_blocked": blocked,
                "cookie_sessions_isolated": cookies_isolated,
            }
        )
    )
    assert counts["asset"] == 1, "Immutable asset must use native cache on second navigation"
    assert counts["blocked"] == 0 and blocked
    assert counts["nocache"] == 2
    assert cookies_isolated
    assert counts["post"] == 2 and route_counts["post"] == 2, "POSTs must stay real and request guarded"


try:
    asyncio.run(main())
finally:
    server.shutdown()
    server.server_close()
