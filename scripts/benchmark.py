"""Ingest the sample course with the real stack and run the 10-question retrieval benchmark.

    python scripts/benchmark.py                 # Docling + BGE-M3 + bge-reranker-v2-m3
    python scripts/benchmark.py --profile test  # offline stand-ins (fast sanity check)
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from course_notes_rag.benchmark import format_report, run_benchmark, write_report  # noqa: E402
from course_notes_rag.config.settings import Settings  # noqa: E402
from course_notes_rag.samples import SAMPLE_COURSE, build_samples  # noqa: E402
from course_notes_rag.services import build_services  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=["production", "test"], default="production")
    parser.add_argument("--expansion", choices=["none", "multi_query", "hyde", "both"], default="none")
    args = parser.parse_args()

    storage = ROOT / f"storage-benchmark-{args.profile}"
    shutil.rmtree(storage, ignore_errors=True)
    services = build_services(Settings(profile=args.profile, storage_dir=storage))
    try:
        files = build_samples(storage / "samples")
        course = services.db.create_course(SAMPLE_COURSE["name"], SAMPLE_COURSE["code"])
        t0 = time.perf_counter()
        for name, path in files.items():
            doc = services.ingestion.submit_upload(course["id"], name, path.read_bytes())
            services.ingestion.wait(doc["id"])
            done = services.db.get_document(doc["id"])
            print(f"ingested {name}: {done['status']} via {done['engine']} "
                  f"({done['num_parents']} sections, {done['num_children']} chunks)")
        print(f"ingestion took {time.perf_counter() - t0:.1f}s\n")
        report = run_benchmark(services.retriever, course["id"], expansion=args.expansion)
        print(format_report(report))
        path = write_report(report, ROOT / "benchmarks" / "results", f"{args.profile}_{args.expansion}")
        print(f"\nreport written to {path}")
    finally:
        services.close()


if __name__ == "__main__":
    main()
