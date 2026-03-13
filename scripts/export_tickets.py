#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib import parse, request
from urllib.error import HTTPError, URLError
from datetime import datetime

SEARCH_URL = "https://www.recreation.gov/api/search"


def progress(message: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} [tickets] {message}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export ticketed Recreation.gov tours/events to tickets.json."
    )
    parser.add_argument(
        "--query",
        default="",
        help=(
            "Optional search query (examples: 'Great Basin', 'Arches'). "
            "Empty query fetches from the broader ticket index."
        ),
    )
    parser.add_argument(
        "--output",
        default="tickets.json",
        help="Output JSON path. Defaults to tickets.json.",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=100,
        help="Page size for Recreation.gov search API. Defaults to 100.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=50,
        help="Maximum number of pages to fetch. Defaults to 50.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=20,
        help="HTTP timeout in seconds. Defaults to 20.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-page progress.",
    )
    return parser


def fetch_search_page(
    query: str,
    *,
    start: int,
    size: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    params = {
        "inventory_type": "ticket",
        "start": str(start),
        "size": str(size),
    }
    if query.strip():
        params["q"] = query.strip()
    url = f"{SEARCH_URL}?{parse.urlencode(params)}"
    req = request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "recgov-monitor-ticket-export/1.0",
        },
    )
    with request.urlopen(req, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Unexpected search response payload type.")
    return payload


def extract_ticket_rows(results: list[Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for record in results:
        if not isinstance(record, dict):
            continue
        entity_type = str(record.get("entity_type") or "").lower()
        if entity_type not in {"tour", "timedentry_tour"}:
            continue
        ticket_id = str(record.get("entity_id") or "").strip()
        facility_id = str(record.get("parent_id") or "").strip()
        ticket_name = str(record.get("name") or "").strip()
        facility_name = str(record.get("parent_name") or "").strip()
        if not (ticket_id and facility_id and ticket_name and facility_name):
            continue
        rows.append(
            {
                "ticket_facility_id": facility_id,
                "ticket_id": ticket_id,
                "ticket_name": ticket_name,
                "ticket_facility_name": facility_name,
            }
        )
    return rows


def fetch_ticket_facility_park_name(
    facility_id: str,
    *,
    timeout_seconds: int,
) -> str:
    query = parse.urlencode({"fq": f"entity_id:{facility_id}", "size": "1"})
    url = f"{SEARCH_URL}?{query}"
    req = request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "recgov-monitor-ticket-export/1.0",
        },
    )
    with request.urlopen(req, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        return ""
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return ""
    first = results[0]
    if not isinstance(first, dict):
        return ""
    parent_name = first.get("parent_name")
    if isinstance(parent_name, str) and parent_name.strip():
        return parent_name.strip()
    return ""


def export_tickets(
    query: str,
    *,
    size: int,
    max_pages: int,
    timeout_seconds: int,
    verbose: bool,
) -> list[dict[str, str]]:
    ticket_by_key: dict[str, dict[str, str]] = {}
    start = 0
    page = 0
    progress(
        f"Starting export: query={'*' if not query.strip() else repr(query.strip())}, "
        f"size={size}, max_pages={max_pages}"
    )
    while page < max_pages:
        progress(f"Fetching search page {page + 1} (start={start}, size={size})...")
        payload = fetch_search_page(
            query=query,
            start=start,
            size=size,
            timeout_seconds=timeout_seconds,
        )
        results = payload.get("results")
        if not isinstance(results, list) or not results:
            break

        rows = extract_ticket_rows(results)
        for row in rows:
            key = f"{row['ticket_facility_id']}:{row['ticket_id']}"
            ticket_by_key[key] = row

        page += 1
        progress(
            f"Page {page} complete: search-results={len(results)} "
            f"ticket-rows={len(rows)} unique={len(ticket_by_key)}"
        )
        if verbose and rows:
            preview = rows[0]
            progress(
                "Sample row: "
                f"{preview['ticket_name']} ({preview['ticket_id']}) @ "
                f"{preview['ticket_facility_name']} ({preview['ticket_facility_id']})"
            )

        if len(results) < size:
            progress("Last page reached (results < page size).")
            break
        start += size

    facility_ids = sorted({row["ticket_facility_id"] for row in ticket_by_key.values()})
    progress(f"Resolving park names for {len(facility_ids)} ticket facilities...")
    park_by_facility: dict[str, str] = {}
    for index, facility_id in enumerate(facility_ids, start=1):
        try:
            park_name = fetch_ticket_facility_park_name(
                facility_id,
                timeout_seconds=timeout_seconds,
            )
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
            park_name = ""
        park_by_facility[facility_id] = park_name
        if index == 1 or index % 25 == 0 or index == len(facility_ids):
            progress(f"Resolved park names: {index}/{len(facility_ids)}")

    enriched: list[dict[str, str]] = []
    for row in ticket_by_key.values():
        park_name = park_by_facility.get(row["ticket_facility_id"], "")
        if park_name:
            enriched.append({**row, "park_name": park_name})
        else:
            enriched.append(row)

    return sorted(
        enriched,
        key=lambda row: (
            row.get("park_name", "").lower(),
            row["ticket_facility_name"].lower(),
            row["ticket_name"].lower(),
            row["ticket_facility_id"],
            row["ticket_id"],
        ),
    )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.size <= 0:
        parser.error("--size must be > 0")
    if args.max_pages <= 0:
        parser.error("--max-pages must be > 0")
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be > 0")

    try:
        tickets = export_tickets(
            query=args.query,
            size=args.size,
            max_pages=args.max_pages,
            timeout_seconds=args.timeout_seconds,
            verbose=args.verbose,
        )
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        progress(f"Ticket export failed: {exc}")
        return 1

    output_path = Path(args.output)
    output_path.write_text(
        f"{json.dumps(tickets, indent=2, ensure_ascii=False)}\n",
        encoding="utf-8",
    )
    progress(
        f"Wrote {len(tickets)} ticket entries to {output_path} "
        f"(query={'*' if not args.query.strip() else args.query!r})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
