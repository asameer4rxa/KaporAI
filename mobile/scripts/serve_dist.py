"""Serve the built web app (mobile/dist) on this PC to try it before publishing.

    python mobile\\scripts\\serve_dist.py          ->  http://localhost:8090

Any path that is not a file falls back to index.html, like a real static host does.
"""
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DIST = Path(__file__).resolve().parent.parent / "dist"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST), **kwargs)

    def send_head(self):
        target = DIST / self.path.split("?", 1)[0].lstrip("/")
        if not target.is_file():
            self.path = "/index.html"
        return super().send_head()


if __name__ == "__main__":
    if not (DIST / "index.html").is_file():
        sys.exit("No build found. Run:  cd mobile; npm run build:web")
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    print(f"Serving {DIST} on http://localhost:{port}  (Ctrl+C stops it)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
