from __future__ import annotations

import argparse
import json

from .analysis import write_trial


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze final-solid evidence without Tekla or drawing writes.")
    parser.add_argument("--input", required=True, help="teklaNativeEvidence.v1 JSON")
    parser.add_argument("--out", required=True, help="new output directory")
    args = parser.parse_args()
    report = write_trial(args.input, args.out)
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0 if report["summary"]["blocked"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
