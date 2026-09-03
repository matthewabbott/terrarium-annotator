"""CLI entry point: `terrarium-annotator verify ...`.

The `run` command needs model credentials (plan G1); verify is
model-free and works on any annotator DB + corpus pair.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

from terrarium_annotator.corpus import CorpusReader
from terrarium_annotator.verify import verify


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="terrarium-annotator")
    sub = parser.add_subparsers(dest="command", required=True)
    verify_parser = sub.add_parser(
        "verify", help="Check annotator DB invariants against the corpus"
    )
    verify_parser.add_argument("--corpus-db", required=True)
    verify_parser.add_argument("--annotator-db", required=True)
    args = parser.parse_args(argv)

    if args.command == "verify":
        conn = sqlite3.connect(f"file:{args.annotator_db}?mode=ro", uri=True)
        with CorpusReader(args.corpus_db) as corpus:
            violations = verify(conn, corpus)
        if not violations:
            print("verify: all invariants hold")
            return 0
        print(f"verify: {len(violations)} violation(s)")
        for v in violations:
            print(f"  [{v.check}] {v.detail}")
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
