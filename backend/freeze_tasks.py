"""
freeze_tasks.py
───────────────
Freeze the 25 in-code BENCHMARK_TASKS into versioned, immutable spec files
under tasks/ so the ablation study runs against a fixed, citable task set
(not a moving in-code list). One JSON per task plus a manifest with a content
hash for integrity.

    python freeze_tasks.py            # write tasks/*.json + tasks/INDEX.json
    python freeze_tasks.py --check    # verify on-disk specs match the code (CI)

The frozen specs are the artifact of record for the paper; regenerate only with
an explicit version bump.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_TASKS_DIR = os.path.join(_HERE, "tasks")
SPEC_VERSION = "1.0"


def _task_to_dict(t) -> dict:
    return dataclasses.asdict(t)


def _serialise() -> "tuple[list, str]":
    from benchmark_tasks import BENCHMARK_TASKS
    records = [_task_to_dict(t) for t in BENCHMARK_TASKS]
    # Deterministic content hash over the canonical JSON of all tasks.
    blob = json.dumps(records, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    return records, digest


def write() -> str:
    records, digest = _serialise()
    os.makedirs(_TASKS_DIR, exist_ok=True)
    for r in records:
        path = os.path.join(_TASKS_DIR, f"{r['task_id']}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(r, f, indent=2, ensure_ascii=False)
    index = {
        "spec_version": SPEC_VERSION,
        "content_hash": digest,
        "n_tasks": len(records),
        "task_ids": [r["task_id"] for r in records],
        "by_complexity": {
            str(c): sum(1 for r in records if r["complexity"] == c)
            for c in sorted({r["complexity"] for r in records})
        },
        "categories": sorted({r["category"] for r in records}),
    }
    with open(os.path.join(_TASKS_DIR, "INDEX.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    return digest


def check() -> int:
    """Return 0 if the on-disk INDEX hash matches the current code, else 1."""
    _, digest = _serialise()
    idx_path = os.path.join(_TASKS_DIR, "INDEX.json")
    if not os.path.exists(idx_path):
        print(f"[freeze] no frozen specs at {idx_path}; run `python freeze_tasks.py`")
        return 1
    with open(idx_path, "r", encoding="utf-8") as f:
        idx = json.load(f)
    on_disk = idx.get("content_hash")
    if on_disk == digest:
        print(f"[freeze] OK — {idx.get('n_tasks')} tasks, hash {digest}")
        return 0
    print(f"[freeze] MISMATCH — code hash {digest} != frozen {on_disk}. "
          f"The in-code BENCHMARK_TASKS changed; bump SPEC_VERSION and re-freeze "
          f"deliberately if this is intended.")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Freeze/verify benchmark task specs")
    ap.add_argument("--check", action="store_true",
                    help="verify on-disk specs match the code (no write)")
    args = ap.parse_args()
    sys.path.insert(0, _HERE)
    if args.check:
        return check()
    digest = write()
    print(f"[freeze] wrote {SPEC_VERSION} specs to {_TASKS_DIR} (hash {digest})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
