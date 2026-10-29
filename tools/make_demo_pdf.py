"""Render docs/demo-captions.html to the printable demo-caption PDF.

    python tools/make_demo_pdf.py

The HTML is served over a short-lived local HTTP server and printed with the
Chromium that Playwright installed. Serving it (rather than opening file://)
lets the bundled Nunito font load; on a bare Linux VM the font is also injected
through the FontFace API, because there are no system fonts to fall back on.

Playwright's Chromium may need shared libraries on a minimal Linux install
(libnss3, libasound2, libatk, …); on Windows/macOS `playwright install chromium`
is enough.
"""
import functools
import pathlib
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "demo-captions.html"
TARGET = ROOT / "docs" / "CampusPulse_Demo_Captions.pdf"

FONT_CSS = (
    "body, .page, h1, h2, p, td, li, div, span, b, .kicker, .sign "
    "{ font-family: 'NunitoDoc', 'Nunito', sans-serif !important; }"
)


def main():
    if not SOURCE.is_file():
        sys.exit(f"missing source: {SOURCE}")

    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(ROOT))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/docs/demo-captions.html"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1240, "height": 1754})
            page.goto(url, wait_until="networkidle")
            page.evaluate(
                """async (css) => {
                  const face = new FontFace('NunitoDoc',
                    "url('/web/fonts/nunito.woff2')", {weight: '100 900'});
                  await face.load();
                  document.fonts.add(face);
                  const style = document.createElement('style');
                  style.textContent = css;
                  document.head.appendChild(style);
                  await document.fonts.ready;
                }""",
                FONT_CSS,
            )
            page.wait_for_timeout(250)
            page.pdf(
                path=str(TARGET),
                format="A4",
                print_background=True,
                prefer_css_page_size=True,
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            )
            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()

    print(f"wrote {TARGET.relative_to(ROOT)} ({TARGET.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
