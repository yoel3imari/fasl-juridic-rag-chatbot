"""Resumable bulk-ingest CLI: ``python -m app.library.bulk <subcommand>``.

Extension-friendly dispatcher (plan todos 12/16 add ``extract``/``run`` ...
here): each subcommand lives in its own ``app.library.bulk_*`` module and
registers ONE entry below. Keep this file to parsing + dispatch only - no
stage logic, so parallel todos never edit the same function body.

Available subcommands: ``catalog`` (todo 10).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

Handler = Callable[[argparse.Namespace], Awaitable[dict[str, Any]]]


async def _run_catalog(args: argparse.Namespace) -> dict[str, Any]:
    from app.library.bulk_catalog import run_catalog_from_args

    return await run_catalog_from_args(args)


async def _run_extract(args: argparse.Namespace) -> dict[str, Any]:
    from app.library.bulk_extract import run_extract_from_args

    return await run_extract_from_args(args)


def _register(sub: Any) -> dict[str, Handler]:
    """Register one subparser per stage module. Later todos add lines here."""
    from app.library.bulk_catalog import add_catalog_parser
    from app.library.bulk_extract import add_extract_parser

    handlers: dict[str, Handler] = {}
    add_catalog_parser(sub)
    handlers["catalog"] = _run_catalog
    add_extract_parser(sub)
    handlers["extract"] = _run_extract
    # todo 16: add_run_parser(sub); handlers["run"] = _run_run
    return handlers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bulk", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    _register(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bulk", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    handlers = _register(sub)
    args = parser.parse_args(argv)
    report = asyncio.run(handlers[args.command](args))
    print(
        f"{args.command}: "
        + " ".join(
            f"{k}={report[k]}"
            for k in ("discovered", "duplicates", "catalogued")
            if k in report
        )
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
