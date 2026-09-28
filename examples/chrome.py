#!/usr/bin/env python3
"""Record a Chromium window. The driver is CDP. The pixels are XGetImage."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("DISPLAY", ":0.0")

from record.session import RecordSession

ROOT = Path(__file__).resolve().parent.parent
PAGE = Path(__file__).resolve().parent / "page.html"
OUT = Path("/tmp/demo-chrome-example.mp4")


def main() -> None:
    url = PAGE.resolve().as_uri()
    with RecordSession(url=url, out=OUT, title="demo-chrome-example", fps=12, max_sec=20) as rec:
        rec.hold(1.2)
        rec.click_text("Take the challenge")
        rec.hold(2)
    print(OUT, OUT.stat().st_size if OUT.exists() else 0)


if __name__ == "__main__":
    main()
