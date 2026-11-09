from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.job_sources import probe_sources


async def main() -> int:
    parser = argparse.ArgumentParser(description="Check live job portal adapters.")
    parser.add_argument("--query", default="software engineer")
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.limit <= 20:
        parser.error("--limit must be between 1 and 20")
    results = await probe_sources(args.query, args.limit)
    print(json.dumps(results, indent=2))
    return 0 if all(value.get("ok") for value in results.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
