"""Write docs/demo/qr.svg and docs/demo/qr.png for the public demo URL.

Usage: .venv/bin/python scripts/make_qr.py [URL]   (default: coord/DEMO_URL.txt)
Re-run whenever the tunnel URL changes.
"""
import pathlib
import sys

import segno

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else (ROOT / "coord/DEMO_URL.txt").read_text().strip()
    if not url.startswith(("http://", "https://")):
        sys.exit(f"not a URL: {url!r}")
    out = ROOT / "docs/demo"
    out.mkdir(parents=True, exist_ok=True)
    qr = segno.make(url, error="h", micro=False)
    # scale so the PNG is at least 600 px including the 4-module quiet zone
    scale = max(10, -(-600 // (qr.symbol_size(scale=1, border=4)[0])))
    qr.save(out / "qr.svg", scale=10, border=4, dark="#000000", light="#ffffff")
    qr.save(out / "qr.png", scale=scale, border=4, dark="#000000", light="#ffffff")
    w, _ = qr.symbol_size(scale=scale, border=4)
    print(f"{url} -> docs/demo/qr.svg, docs/demo/qr.png ({w}x{w} px, version {qr.version}, error H)")


if __name__ == "__main__":
    main()
