#!/usr/bin/env python3
"""A working ingester on the toolkit's ingest primitives -- edit this, do not start from zero.

  python3 src/ingest_example.py                 # every source in the manifest
  python3 src/ingest_example.py --refetch       # ignore cached snapshots
  python3 src/ingest_example.py --limit 3       # first three, while you are shaping it

WHAT IS THE TOOLKIT'S AND WHAT IS YOURS (corpus-toolkit ADR-0016). The toolkit owns the
mechanics every corpus performs identically: fetching honestly over HTTP/2 with per-host
politeness and refusals raised as exceptions (`Fetcher`), recording the snapshot and both
hashes and moving the drift baseline (`record_snapshot`), and writing a document whose
frontmatter is in the platform's order and validated BEFORE it touches disk
(`write_document`). This file owns the rest: which sources (the manifest), how bytes become
text (`to_text` below -- replace it when your sources need cleanup), and what the body says.

The failure modes this shape prevents were all measured on other corpora: a `Mozilla/5.0`
agent that hid a refusal, a stale `retrieved` that advanced on every cached run, platform
fields that "silently lacked" from generated documents, and a drift run that reported every
freshly ingested source as changed because nothing moved the baseline.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from corpus_toolkit import config as config_mod
from corpus_toolkit.documents import DocumentError, write_document
from corpus_toolkit.html_to_text import html_to_text
from corpus_toolkit.sources.fetch import UNSUPPORTED, FetchError, Fetcher, sniff
from corpus_toolkit.sources.snapshots import record_snapshot, retrieved_date

ROOT = Path(__file__).resolve().parent.parent
CONFIG = config_mod.load(ROOT / "_meta" / "corpus.yml")


def to_text(raw: bytes, fmt: str) -> str:
    """Bytes to the text that will be committed as `<id>.txt` and hashed. YOURS to refine:
    page furniture, headers repeated per page and boilerplate belong here, per source."""
    if fmt == "pdf":
        return subprocess.run(["pdftotext", "-layout", "-", "-"], input=raw,
                              capture_output=True, check=True).stdout.decode("utf-8", "replace")
    if fmt in ("html", "xml"):
        return html_to_text(raw)
    return raw.decode("utf-8", "replace")


def body_for(src: dict, text: str) -> str:
    """What the document says. The disclaimer marker from corpus.yml MUST appear here."""
    return (f"> **{CONFIG.disclaimer_marker}.** This is a convenience copy for machine reading. "
            f"The official text is published at the source URL above. Verify at source before "
            f"relying on it.\n\n# {src['title']}\n\n## Full text\n\n{text}\n")


def ingest(src: dict, fetcher: Fetcher, refetch: bool) -> Path:
    sid = src["id"]
    declared = src.get("format") or "html"
    snap_path = CONFIG.snapshot_dir / f"{sid}.{declared}"
    raw, fresh = fetcher.snapshot(src["url"], snap_path, refetch=refetch)
    fmt = sniff(raw, declared)                 # the bytes decide, the manifest is corrected
    if fmt in UNSUPPORTED:
        raise ValueError(f"{fmt} is not a format this corpus can read")
    if fmt != declared:
        # Reported, not rewritten: the manifest is curated data with comments, and a
        # whole-file yaml dump would flatten it. Fix the `format:` line by hand.
        print(f"  {sid}: manifest says {declared}, bytes say {fmt} -- correct the manifest")
    text = to_text(raw, fmt)
    snap = record_snapshot(CONFIG, sid, raw, fmt, text)      # files, hashes, baseline line

    doc_path = ROOT / CONFIG.content_roots[0].path / f"{sid}.md"
    frontmatter = {
        "id": sid,
        "title": src["title"],
        "doc_type": src.get("doc_type") or CONFIG.content_roots[0].doc_type,
        "citation": src.get("citation") or src["title"],
        "authority_level": src.get("authority_level", ""),
        "issuing_body": src.get("issuing_body", ""),
        "source_url": src["url"],
        "source_format": fmt,
        "retrieved": retrieved_date(fresh, doc_path, snap.raw_path),
        "source_sha256": snap.sha256,
        "snapshot_policy": "hash-only",
        "status": "current",
        "content_mode": "verbatim",
        "maintainer": src.get("maintainer", ""),
        "relationships": {"implements": [], "implemented_by": [],
                          "references_external": [], "related": [], "supersedes": []},
        "tags": [],
    }
    return write_document(CONFIG, doc_path, frontmatter, body_for(src, text))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refetch", action="store_true")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    groups = config_mod.load_source_manifest_group_files(CONFIG)
    ok = failed = 0
    with Fetcher(CONFIG) as fetcher:
        for path, group in groups:
            sources = [s for s in (group.get("sources") or []) if isinstance(s, dict)]
            for src in sources[:args.limit]:
                try:
                    out = ingest(src, fetcher, args.refetch)
                    print(f"ok  {out.relative_to(ROOT)}")
                    ok += 1
                except (FetchError, DocumentError, ValueError, subprocess.CalledProcessError) as e:
                    # Reported, never hidden: a refusal is a fact about our access, a
                    # DocumentError is a fact about this script -- both belong in the log.
                    print(f"FAILED {src.get('id')}: {type(e).__name__}: {e}", file=sys.stderr)
                    failed += 1
    print(f"{ok} ingested, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
