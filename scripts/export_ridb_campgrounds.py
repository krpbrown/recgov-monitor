from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
from datetime import date
from urllib import parse, request
from urllib.error import HTTPError, URLError


RIDB_FACILITIES_URL = "https://ridb.recreation.gov/api/v1/facilities"
RIDB_FACILITY_URL = "https://ridb.recreation.gov/api/v1/facilities/{facility_id}"
RIDB_RECAREA_URL = "https://ridb.recreation.gov/api/v1/recareas/{rec_area_id}"
RECGOV_AVAILABILITY_URL = (
    "https://www.recreation.gov/api/camps/availability/campground/{campground_id}/month"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch all recreation.gov campgrounds from RIDB, validate campground IDs "
            "against recreation.gov, and export JSON."
        )
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("RIDB_API_KEY"),
        help="RIDB API key. Defaults to RIDB_API_KEY environment variable.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Page size for RIDB facilities endpoint. Defaults to 200.",
    )
    parser.add_argument(
        "--output",
        default="campgrounds.json",
        help="Output JSON file path. Defaults to campgrounds.json.",
    )
    parser.add_argument(
        "--skip-validation",
        "-S",
        action="store_true",
        help="Skip live recreation.gov validation of campground IDs.",
    )
    parser.add_argument(
        "--validate-delay-seconds",
        type=float,
        default=0.25,
        help="Delay between validation requests. Defaults to 0.25.",
    )
    parser.add_argument(
        "--validate-timeout-seconds",
        type=int,
        default=20,
        help="Timeout for each validation request in seconds. Defaults to 20.",
    )
    parser.add_argument(
        "--test-limit",
        type=int,
        default=0,
        help="Temporary testing mode: stop after collecting N campgrounds (0 disables).",
    )
    parser.add_argument(
        "--test-include-id",
        type=int,
        action="append",
        default=[],
        help="Campground ID to force-include in test mode. Repeatable.",
    )
    return parser


def fetch_facilities_page(api_key: str, offset: int, limit: int) -> dict:
    query = parse.urlencode({"offset": offset, "limit": limit})
    url = f"{RIDB_FACILITIES_URL}?{query}"
    req = request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "apikey": api_key,
            "User-Agent": "recgov-monitor-ridb-export/1.0",
        },
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"RIDB request failed: {exc.code} {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"RIDB request failed: {exc.reason}") from exc


def is_campground(record: dict) -> bool:
    reservation_url = str(record.get("FacilityReservationURL") or "").lower()
    facility_type = str(record.get("FacilityTypeDescription") or "").lower()
    facility_name = str(record.get("FacilityName") or "").lower()
    return (
        "/camping/campgrounds/" in reservation_url
        or "campground" in facility_type
        or "campground" in facility_name
    )


def extract_park_name(record: dict) -> str:
    for key in ("RecAreaName", "ParentRecAreaName", "OrganizationName"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def fetch_rec_area_name(
    api_key: str,
    rec_area_id: int,
    timeout_seconds: int = 20,
) -> str:
    url = RIDB_RECAREA_URL.format(rec_area_id=rec_area_id)
    req = request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "apikey": api_key,
            "User-Agent": "recgov-monitor-ridb-export/1.0",
        },
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError):
        return ""

    if isinstance(payload, dict):
        name = payload.get("RecAreaName")
        if isinstance(name, str) and name.strip():
            return name.strip()

        records = payload.get("RECDATA")
        if isinstance(records, list):
            for record in records:
                if not isinstance(record, dict):
                    continue
                rec_name = record.get("RecAreaName")
                if isinstance(rec_name, str) and rec_name.strip():
                    return rec_name.strip()
    return ""


def fetch_facility_details(
    api_key: str,
    facility_id: int,
    timeout_seconds: int = 20,
) -> dict:
    url = RIDB_FACILITY_URL.format(facility_id=facility_id)
    req = request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "apikey": api_key,
            "User-Agent": "recgov-monitor-ridb-export/1.0",
        },
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, json.JSONDecodeError):
        return {}

    if isinstance(payload, dict):
        if "RECDATA" in payload and isinstance(payload.get("RECDATA"), list):
            records = payload.get("RECDATA")
            for record in records:
                if isinstance(record, dict):
                    return record
        return payload
    return {}


def fetch_park_from_recreation_url(url: str, timeout_seconds: int = 20) -> str:
    req = request.Request(
        url,
        method="GET",
        headers={
            "Accept": "text/html",
            "User-Agent": "recgov-monitor-ridb-export/1.0",
        },
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            text = response.read().decode("utf-8", errors="ignore")
    except (HTTPError, URLError):
        return ""

    match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""

    title = html.unescape(match.group(1)).strip()
    title = " ".join(title.split())
    # Typical shape: "<Campground>, <Park Name> - Recreation.gov"
    park_match = re.search(
        r",\s*(.*?)\s*-\s*Recreation\.gov",
        title,
        flags=re.IGNORECASE,
    )
    if park_match:
        park = park_match.group(1).strip()
        if park and park.lower() != "recreation.gov":
            return park
    return ""


def fetch_all_campgrounds(
    api_key: str,
    limit: int,
    max_campgrounds: int | None = None,
    include_ids: set[int] | None = None,
) -> list[dict[str, object]]:
    offset = 0
    page = 0
    campgrounds_by_id: dict[str, dict[str, object]] = {}
    rec_area_cache: dict[int, str] = {}
    facility_cache: dict[int, dict] = {}
    page_park_cache: dict[str, str] = {}
    required_ids = include_ids or set()

    while True:
        page += 1
        print(
            f"[ridb] Fetching page {page} (offset={offset}, limit={limit})...",
            file=sys.stderr,
        )
        payload = fetch_facilities_page(api_key=api_key, offset=offset, limit=limit)
        records = payload.get("RECDATA", [])
        if not isinstance(records, list):
            raise RuntimeError("Unexpected RIDB response: RECDATA was not a list.")

        for record in records:
            if not isinstance(record, dict) or not is_campground(record):
                continue

            facility_id = record.get("FacilityID")
            facility_name = (record.get("FacilityName") or "").strip()
            if facility_id is None or not facility_name:
                continue
            try:
                facility_id_int = int(facility_id)
            except (TypeError, ValueError):
                continue
            facility_id_str = str(facility_id_int)
            if (
                max_campgrounds
                and facility_id_str not in campgrounds_by_id
                and len(campgrounds_by_id) >= max_campgrounds
                and facility_id_int not in required_ids
            ):
                continue
            park = ""
            rec_area_id_raw = record.get("RecAreaID")
            rec_area_id_int: int | None = None
            if rec_area_id_raw is not None:
                try:
                    rec_area_id_int = int(rec_area_id_raw)
                except (TypeError, ValueError):
                    rec_area_id_int = None

            # Temporarily disabled per request:
            # 1) facility field park extraction (RecAreaName/ParentRecAreaName/OrganizationName)
            # 2) RIDB rec-area / facility-detail lookup for park enrichment
            #
            # if not park and rec_area_id_int is None:
            #     if facility_id_int in facility_cache:
            #         facility_detail = facility_cache[facility_id_int]
            #     else:
            #         facility_detail = fetch_facility_details(
            #             api_key=api_key,
            #             facility_id=facility_id_int,
            #         )
            #         facility_cache[facility_id_int] = facility_detail
            #
            #     park = extract_park_name(facility_detail)
            #     detail_rec_area_id = facility_detail.get("RecAreaID")
            #     if detail_rec_area_id is not None:
            #         try:
            #             rec_area_id_int = int(detail_rec_area_id)
            #         except (TypeError, ValueError):
            #             rec_area_id_int = None
            #
            # if not park and rec_area_id_int is not None:
            #     if rec_area_id_int in rec_area_cache:
            #         park = rec_area_cache[rec_area_id_int]
            #     else:
            #         park = fetch_rec_area_name(api_key=api_key, rec_area_id=rec_area_id_int)
            #         rec_area_cache[rec_area_id_int] = park

            campgrounds_by_id[facility_id_str] = {
                "name": facility_name,
                "id": facility_id_int,
                "url": f"https://www.recreation.gov/camping/campgrounds/{facility_id_int}",
                "park": park,
            }

            if (
                not park
                and campgrounds_by_id[facility_id_str]["url"]
            ):
                campground_url = str(campgrounds_by_id[facility_id_str]["url"])
                if campground_url in page_park_cache:
                    park = page_park_cache[campground_url]
                else:
                    park = fetch_park_from_recreation_url(campground_url)
                    page_park_cache[campground_url] = park
                if park:
                    campgrounds_by_id[facility_id_str]["park"] = park

        metadata = payload.get("METADATA", {})
        results = metadata.get("RESULTS", {}) if isinstance(metadata, dict) else {}
        total_count = results.get("TOTAL_COUNT") if isinstance(results, dict) else None

        next_offset = offset + len(records)
        if isinstance(total_count, int) and total_count > 0:
            percent = (next_offset / total_count) * 100
            print(
                (
                    f"[ridb] Page {page} complete: {len(records)} records, "
                    f"{next_offset}/{total_count} scanned ({percent:.1f}%), "
                    f"{len(campgrounds_by_id)} campgrounds collected."
                ),
                file=sys.stderr,
            )
        else:
            print(
                (
                    f"[ridb] Page {page} complete: {len(records)} records, "
                    f"{next_offset} scanned, {len(campgrounds_by_id)} campgrounds collected."
                ),
                file=sys.stderr,
            )

        offset += len(records)
        if not records:
            print("[ridb] No more records returned; stopping.", file=sys.stderr)
            break
        if isinstance(total_count, int) and offset >= total_count:
            print("[ridb] Reached TOTAL_COUNT; stopping.", file=sys.stderr)
            break
        if max_campgrounds and len(campgrounds_by_id) >= max_campgrounds:
            print(
                (
                    "[ridb] Test limit reached; "
                    f"stopping at {len(campgrounds_by_id)} campgrounds "
                    "and backfilling required IDs if needed."
                ),
                file=sys.stderr,
            )
            break

    if required_ids:
        present_ids = {int(value["id"]) for value in campgrounds_by_id.values()}
        missing_ids = sorted(required_ids - present_ids)
        for facility_id_int in missing_ids:
            print(f"[ridb] Fetching required test ID {facility_id_int}...", file=sys.stderr)
            detail = fetch_facility_details(api_key=api_key, facility_id=facility_id_int)
            if not detail:
                print(f"[ridb]   could not fetch facility {facility_id_int}", file=sys.stderr)
                continue

            facility_name_raw = detail.get("FacilityName")
            facility_name = (
                facility_name_raw.strip()
                if isinstance(facility_name_raw, str) and facility_name_raw.strip()
                else f"campground {facility_id_int}"
            )
            park = ""

            rec_area_id_raw = detail.get("RecAreaID")
            rec_area_id_int: int | None = None
            if rec_area_id_raw is not None:
                try:
                    rec_area_id_int = int(rec_area_id_raw)
                except (TypeError, ValueError):
                    rec_area_id_int = None
            # Temporarily disabled per request:
            # if not park and rec_area_id_int is not None:
            #     if rec_area_id_int in rec_area_cache:
            #         park = rec_area_cache[rec_area_id_int]
            #     else:
            #         park = fetch_rec_area_name(api_key=api_key, rec_area_id=rec_area_id_int)
            #         rec_area_cache[rec_area_id_int] = park

            url = f"https://www.recreation.gov/camping/campgrounds/{facility_id_int}"
            if not park:
                if url in page_park_cache:
                    park = page_park_cache[url]
                else:
                    park = fetch_park_from_recreation_url(url)
                    page_park_cache[url] = park

            campgrounds_by_id[str(facility_id_int)] = {
                "name": facility_name,
                "id": facility_id_int,
                "url": url,
                "park": park,
            }

    sorted_campgrounds = sorted(
        campgrounds_by_id.values(),
        key=lambda item: (str(item["name"]).lower(), int(item["id"])),
    )
    if max_campgrounds:
        if not required_ids:
            return sorted_campgrounds[:max_campgrounds]

        selected: list[dict[str, object]] = []
        selected_ids: set[int] = set()
        required_records = [
            item for item in sorted_campgrounds if int(item["id"]) in required_ids
        ]
        for item in required_records:
            selected.append(item)
            selected_ids.add(int(item["id"]))

        for item in sorted_campgrounds:
            item_id = int(item["id"])
            if item_id in selected_ids:
                continue
            if len(selected) >= max_campgrounds:
                break
            selected.append(item)
            selected_ids.add(item_id)

        return selected[:max_campgrounds]
    return sorted_campgrounds


def validate_campground_record(
    campground: dict[str, object],
    timeout_seconds: int,
) -> tuple[bool, str]:
    campground_id = int(campground["id"])
    month_start = date.today().replace(day=1)
    params = parse.urlencode({"start_date": f"{month_start.isoformat()}T00:00:00.000Z"})
    url = RECGOV_AVAILABILITY_URL.format(campground_id=campground_id)
    req = request.Request(
        f"{url}?{params}",
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "recgov-monitor-ridb-export/1.0",
        },
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict) and ("campsites" in payload or "campground_id" in payload):
                return True, "ok"
            return True, "ok (unexpected payload shape)"
    except HTTPError as exc:
        if exc.code in {400, 404}:
            return False, f"HTTP {exc.code} Not Found/Invalid"
        body = exc.read().decode("utf-8", errors="replace")
        return True, f"HTTP {exc.code} (kept, transient/unknown): {body[:120]}"
    except URLError as exc:
        return True, f"Network error (kept, transient/unknown): {exc.reason}"


def validate_campgrounds(
    campgrounds: list[dict[str, object]],
    delay_seconds: float,
    timeout_seconds: int,
) -> list[dict[str, object]]:
    kept: list[dict[str, object]] = []
    total = len(campgrounds)
    for index, campground in enumerate(campgrounds, start=1):
        campground_id = int(campground["id"])
        campground_name = str(campground["name"])
        print(
            f"[ridb] Validating {index}/{total}: {campground_name} ({campground_id})...",
            file=sys.stderr,
        )
        is_valid, reason = validate_campground_record(campground, timeout_seconds=timeout_seconds)
        if is_valid:
            kept.append(campground)
            print(f"[ridb]   keep   {campground_id} ({reason})", file=sys.stderr)
        else:
            print(f"[ridb]   drop   {campground_id} ({reason})", file=sys.stderr)

        if delay_seconds > 0 and index < total:
            time.sleep(delay_seconds)

    print(
        f"[ridb] Validation complete: kept {len(kept)} of {total} campgrounds.",
        file=sys.stderr,
    )
    return kept


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    started_at = time.perf_counter()

    if not args.api_key:
        parser.error("RIDB API key is required via --api-key or RIDB_API_KEY env var.")
    if args.limit <= 0:
        parser.error("--limit must be a positive integer.")
    if args.test_limit < 0:
        parser.error("--test-limit must be >= 0.")
    if args.validate_delay_seconds < 0:
        parser.error("--validate-delay-seconds must be >= 0.")
    if args.validate_timeout_seconds <= 0:
        parser.error("--validate-timeout-seconds must be > 0.")

    include_ids = set(args.test_include_id)
    if args.test_limit > 0 and not include_ids:
        include_ids.add(232492)

    print("[ridb] Starting campground export...", file=sys.stderr)
    try:
        campgrounds = fetch_all_campgrounds(
            api_key=args.api_key,
            limit=args.limit,
            max_campgrounds=(args.test_limit if args.test_limit > 0 else None),
            include_ids=include_ids,
        )
        if args.skip_validation:
            print("[ridb] Skipping live campground validation.", file=sys.stderr)
        else:
            print(
                (
                    "[ridb] Starting live campground validation "
                    f"({len(campgrounds)} records)..."
                ),
                file=sys.stderr,
            )
            campgrounds = validate_campgrounds(
                campgrounds,
                delay_seconds=args.validate_delay_seconds,
                timeout_seconds=args.validate_timeout_seconds,
            )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    with open(args.output, "w", encoding="utf-8") as output_file:
        json.dump(campgrounds, output_file, indent=2)
        output_file.write("\n")

    elapsed_seconds = int(time.perf_counter() - started_at)
    hours, remainder = divmod(elapsed_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    elapsed_display = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    print(f"[ridb] Finished export: {len(campgrounds)} campgrounds.", file=sys.stderr)
    print(f"Wrote {len(campgrounds)} campgrounds to {args.output}")
    print(f"time elapsed: {elapsed_display}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

