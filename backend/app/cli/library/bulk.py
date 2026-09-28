"""Resumable bulk-ingest CLI: ``python -m app.cli.library.bulk <subcommand>``.

Extension-friendly dispatcher (plan todos 12/16 add ``extract``/``run`` ...
here): each subcommand lives in its own ``app.cli.library.bulk_*`` module and
registers ONE entry below. Keep this file to parsing + dispatch only - no
stage logic, so parallel todos never edit the same function body.

Available subcommands: ``catalog`` (todo 10), ``extract`` (todo 12),
``migrate`` (todo 8), ``reembed-matter`` (todo 9), ``embed``/``index``/``run``
(todo 16).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

Handler = Callable[[argparse.Namespace], Awaitable[dict[str, Any]]]


async def _run_catalog(args: argparse.Namespace) -> dict[str, Any]:
    from app.cli.library.bulk_catalog import run_catalog_from_args

    return await run_catalog_from_args(args)


async def _run_extract(args: argparse.Namespace) -> dict[str, Any]:
    from app.cli.library.bulk_extract import run_extract_from_args

    return await run_extract_from_args(args)


async def _run_embed(args: argparse.Namespace) -> dict[str, Any]:
    from app.cli.library.bulk_run import run_embed_from_args

    return await run_embed_from_args(args)


async def _run_index(args: argparse.Namespace) -> dict[str, Any]:
    from app.cli.library.bulk_run import run_index_from_args

    return await run_index_from_args(args)


async def _run_run(args: argparse.Namespace) -> dict[str, Any]:
    from app.cli.library.bulk_run import run_run_from_args

    return await run_run_from_args(args)


async def _run_reembed(args: argparse.Namespace) -> dict[str, Any]:
    from app.cli.library.bulk_reembed import run_reembed_from_args

    return await run_reembed_from_args(args)


async def _run_migrate(args: argparse.Namespace) -> dict[str, Any]:
    from app.cli.library.bulk_migrate import run_migrate_from_args

    return await run_migrate_from_args(args)


def _register(sub: Any) -> dict[str, Handler]:
    """Register one subparser per stage module. Later todos add lines here."""
    from app.cli.library.bulk_catalog import add_catalog_parser
    from app.cli.library.bulk_extract import add_extract_parser
    from app.cli.library.bulk_migrate import add_migrate_parser
    from app.cli.library.bulk_reembed import add_reembed_parser
    from app.cli.library.bulk_run import add_embed_parser, add_index_parser, add_run_parser

    handlers: dict[str, Handler] = {}
    add_catalog_parser(sub)
    handlers["catalog"] = _run_catalog
    add_extract_parser(sub)
    handlers["extract"] = _run_extract
    add_migrate_parser(sub)
    handlers["migrate"] = _run_migrate
    add_reembed_parser(sub)
    handlers["reembed-matter"] = _run_reembed
    add_embed_parser(sub)
    handlers["embed"] = _run_embed
    add_index_parser(sub)
    handlers["index"] = _run_index
    add_run_parser(sub)
    handlers["run"] = _run_run
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
