"""
Render all .mmd files under this folder to PNG via the mermaid.ink service.

Usage:
    python3 render_diagrams.py              # only render missing PNGs
    python3 render_diagrams.py --force      # re-render everything

Requires: internet access to https://mermaid.ink
"""

from __future__ import annotations

import argparse
import base64
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ASSETS_DIR = Path(__file__).parent
MERMAID_INK = "https://mermaid.ink/img"
RENDER_PARAMS = "type=png&bgColor=white"
MAX_RETRIES = 4
BACKOFF_SECONDS = 3
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)


def encode_diagram(source: str) -> str:
    data = source.strip().encode("utf-8")
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def render(mmd_path: Path, force: bool) -> bool:
    out = mmd_path.with_suffix(".png")
    if out.exists() and not force:
        print(f"SKIP {out.relative_to(ASSETS_DIR)} (already exists)")
        return True

    source = mmd_path.read_text(encoding="utf-8")
    url = f"{MERMAID_INK}/{encode_diagram(source)}?{RENDER_PARAMS}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "image/png,image/*;q=0.8,*/*;q=0.5",
        },
    )

    last_error: str | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                out.write_bytes(resp.read())
            print(f"OK   {out.relative_to(ASSETS_DIR)}")
            return True
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if exc.code in (502, 503, 504, 429) and attempt < MAX_RETRIES:
                time.sleep(BACKOFF_SECONDS * attempt)
                continue
            break
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            if attempt < MAX_RETRIES:
                time.sleep(BACKOFF_SECONDS * attempt)
                continue
            break

    print(f"FAIL {mmd_path.relative_to(ASSETS_DIR)}: {last_error}")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-render diagrams even if PNG already exists.",
    )
    args = parser.parse_args()

    mmd_files = sorted(ASSETS_DIR.rglob("*.mmd"))
    if not mmd_files:
        print("No .mmd files found.")
        return 1

    print(f"Rendering {len(mmd_files)} diagram(s)...")
    failed = [m for m in mmd_files if not render(m, force=args.force)]
    if failed:
        print(f"\n{len(failed)} diagram(s) failed. Re-run the script to retry.")
        return 2
    print("\nAll diagrams rendered successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
