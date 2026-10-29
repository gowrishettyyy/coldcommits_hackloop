"""Render docs/project-overview.html to docs/CampusPulse_Project_Overview.pdf.

    python tools/make_project_pdf.py
"""
import functools
import pathlib
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "project-overview.html"
TARGET = ROOT / "docs" / "CampusPulse_Project_Overview.pdf"

FONT_CSS = (
    "body, .page, h1, h2, h3, p, td, th, li, div, span, b, .kicker, .foot, .lede, .small "
    "{ font-family: 'NunitoDoc', 'Nunito', sans-serif !important; }"
)


def main():
    if not SOURCE.is_file():
        sys.exit(f"missing source: {SOURCE}")

    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(ROOT))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/docs/project-overview.html"

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
