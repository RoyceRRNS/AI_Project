"""Run the API (and the built UI, if present).

    python scripts/serve.py                         # production profile: Docling, BGE-M3, reranker, Ollama Qwen
    python scripts/serve.py --profile test --storage-dir ./storage-test --port 8765   # offline stand-ins
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", choices=["production", "test"], default=None)
    parser.add_argument("--storage-dir", default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--fresh", action="store_true", help="delete the storage dir first (test profile only)")
    args = parser.parse_args()

    if args.profile:
        os.environ["CNR_PROFILE"] = args.profile
    if args.storage_dir:
        os.environ["CNR_STORAGE_DIR"] = str(Path(args.storage_dir).resolve())
    if args.fresh:
        if os.environ.get("CNR_PROFILE") != "test" or not args.storage_dir:
            parser.error("--fresh needs --profile test and an explicit --storage-dir")
        import shutil

        shutil.rmtree(os.environ["CNR_STORAGE_DIR"], ignore_errors=True)

    sys.path.insert(0, str(ROOT))
    import uvicorn

    uvicorn.run("course_notes_rag.api.server:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
