#!/usr/bin/env python3
"""Serve testbed/visual-heal/site/ on 127.0.0.1 only (default port 8765).

Variants are subdirectories: /base/, /v1/ ... /v6/ (signup) and /orders-base/, /v7/ ... /v9/ (orders). Responses are never cached.
"""
import argparse
import functools
import http.server
import os

HOST = "127.0.0.1"
SITE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site")


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, fmt, *args):
        if os.environ.get("QA_SERVE_QUIET") != "1":
            super().log_message(fmt, *args)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    handler = functools.partial(NoCacheHandler, directory=SITE)
    server = http.server.ThreadingHTTPServer((HOST, args.port), handler)
    print(f"serving {SITE} on http://{HOST}:{args.port}/ (base, orders-base, v1..v9)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
