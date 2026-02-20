from __future__ import annotations

import argparse
import base64
import calendar
import html
import io
import json
import math
from datetime import date, datetime
from pathlib import Path
import re
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any
from urllib import request
from urllib.error import HTTPError, URLError
import webbrowser
try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None  # type: ignore[assignment]
    ImageTk = None  # type: ignore[assignment]


DISPLAY_DATE_FORMAT = "%m-%d-%Y"
STORAGE_DATE_FORMAT = "%Y-%m-%d"
RIDB_MEDIA_URL = "https://ridb.recreation.gov/api/v1/facilities/{facility_id}/media"
PREVIEW_MAX_WIDTH = 360
PREVIEW_MAX_HEIGHT = 220
LISTS_SECTION_HEIGHT = 280
TRIP_GROUPS_SECTION_HEIGHT = 160
PREVIEW_SECTION_HEIGHT = 620


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GUI editor for monitor.json using campgrounds.json catalog."
    )
    parser.add_argument(
        "--campgrounds-file",
        default="campgrounds.json",
        help="Path to campgrounds catalog JSON. Defaults to campgrounds.json.",
    )
    parser.add_argument(
        "--monitor-file",
        default="monitor.json",
        help="Path to monitor config JSON. Defaults to monitor.json.",
    )
    parser.add_argument(
        "--ridb-api-key",
        default="",
        help="RIDB API key for campground image lookup. Defaults to RIDB_API_KEY env var.",
    )
    return parser.parse_args()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def storage_to_display_date(raw: str) -> str:
    try:
        parsed = datetime.strptime(raw, STORAGE_DATE_FORMAT).date()
    except ValueError:
        return raw
    return parsed.strftime(DISPLAY_DATE_FORMAT)


def display_to_storage_date(raw: str) -> str:
    parsed = datetime.strptime(raw, DISPLAY_DATE_FORMAT).date()
    return parsed.strftime(STORAGE_DATE_FORMAT)


def load_campgrounds(path: Path) -> list[dict[str, Any]]:
    raw = load_json(path)
    if not isinstance(raw, list):
        raise ValueError(f"{path} must contain a JSON array.")

    campgrounds: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        campground_id = item.get("id")
        url = item.get("url")
        park = item.get("park")
        if not isinstance(name, str) or not isinstance(campground_id, int):
            continue
        campgrounds.append(
            {
                "name": name.strip(),
                "id": campground_id,
                "url": url if isinstance(url, str) else "",
                "park": park.strip() if isinstance(park, str) else "",
            }
        )

    if not campgrounds:
        raise ValueError(f"{path} has no valid campground records.")
    return sorted(campgrounds, key=lambda c: (str(c["name"]).lower(), int(c["id"])))


class MonitorEditorApp:
    def __init__(
        self,
        root: tk.Tk,
        campgrounds: list[dict[str, Any]],
        monitor_path: Path,
        ridb_api_key: str,
    ) -> None:
        self.root = root
        self.campgrounds = campgrounds
        self.monitor_path = monitor_path
        self.ridb_api_key = ridb_api_key
        self.campground_by_id: dict[int, dict[str, Any]] = {
            int(item["id"]): item for item in campgrounds
        }

        self.search_var = tk.StringVar()
        self.check_in_var = tk.StringVar()
        self.check_out_var = tk.StringVar()
        self.webhook_var = tk.StringVar()
        self.poll_var = tk.StringVar(value="60")
        self.status_var = tk.StringVar(value="Ready.")

        self.filtered: list[dict[str, Any]] = []
        self.selected_ids: list[int] = []
        self.trip_groups: list[dict[str, Any]] = []
        self.active_trip_group_index: int | None = None
        self.image_cache: dict[int, str] = {}
        self.preview_image: Any = None
        self.current_image_url: str = ""
        self.current_campground_url: str = ""
        self.current_preview_request_id = 0

        self._build_ui()
        self._refresh_search_results()
        self._load_existing_monitor_file()

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.configure("App.TLabelframe.Label", font=("Segoe UI", 11, "bold"))
        style.configure("App.Header.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("App.SubHeader.TLabel", font=("Segoe UI", 10, "bold"))

        self.root.title("recgov-monitor monitor.json editor")
        self.root.geometry("1450x1100")
        self.root.minsize(1200, 980)

        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        notebook = ttk.Notebook(frame)
        notebook.grid(row=0, column=0, sticky="nsew")

        monitor_tab = ttk.Frame(notebook, padding=8)
        settings_tab = ttk.Frame(notebook, padding=8)
        notebook.add(monitor_tab, text="Monitor")
        notebook.add(settings_tab, text="Settings")

        monitor_tab.columnconfigure(0, weight=1)
        monitor_tab.columnconfigure(1, weight=1)
        monitor_tab.rowconfigure(1, weight=0, minsize=LISTS_SECTION_HEIGHT)
        monitor_tab.rowconfigure(4, weight=0, minsize=TRIP_GROUPS_SECTION_HEIGHT)
        monitor_tab.rowconfigure(5, weight=0, minsize=PREVIEW_SECTION_HEIGHT)

        search_label = ttk.Label(
            monitor_tab,
            text="Search campgrounds or parks",
            style="App.Header.TLabel",
        )
        search_label.grid(row=0, column=0, sticky="w")
        self.search_var.trace_add("write", lambda *_: self._refresh_search_results())
        search_entry = ttk.Entry(monitor_tab, textvariable=self.search_var)
        search_entry.grid(row=0, column=0, sticky="ew", padx=(0, 140))

        selected_label = ttk.Label(
            monitor_tab,
            text="Current trip campgrounds",
            style="App.Header.TLabel",
        )
        selected_label.grid(row=0, column=1, sticky="w")

        left_panel = ttk.Frame(monitor_tab)
        left_panel.grid(row=1, column=0, sticky="nsew", padx=(0, 8), pady=(8, 8))
        left_panel.configure(height=LISTS_SECTION_HEIGHT)
        left_panel.grid_propagate(False)
        left_panel.rowconfigure(0, weight=1)
        left_panel.columnconfigure(0, weight=1)

        self.search_listbox = tk.Listbox(
            left_panel,
            selectmode=tk.EXTENDED,
            exportselection=False,
        )
        self.search_listbox.grid(row=0, column=0, sticky="nsew")
        search_scroll = ttk.Scrollbar(
            left_panel, orient=tk.VERTICAL, command=self.search_listbox.yview
        )
        search_scroll.grid(row=0, column=1, sticky="ns")
        self.search_listbox.configure(yscrollcommand=search_scroll.set)

        add_button = ttk.Button(
            monitor_tab,
            text="Add Selected ->",
            command=self._add_selected_from_search,
        )
        add_button.grid(row=2, column=0, sticky="e", pady=(0, 8))

        right_panel = ttk.Frame(monitor_tab)
        right_panel.grid(row=1, column=1, sticky="nsew", padx=(8, 0), pady=(8, 8))
        right_panel.configure(height=LISTS_SECTION_HEIGHT)
        right_panel.grid_propagate(False)
        right_panel.rowconfigure(0, weight=1)
        right_panel.columnconfigure(0, weight=1)

        self.selected_listbox = tk.Listbox(right_panel, selectmode=tk.EXTENDED, exportselection=False)
        self.selected_listbox.grid(row=0, column=0, sticky="nsew")
        selected_scroll = ttk.Scrollbar(
            right_panel, orient=tk.VERTICAL, command=self.selected_listbox.yview
        )
        selected_scroll.grid(row=0, column=1, sticky="ns")
        self.selected_listbox.configure(yscrollcommand=selected_scroll.set)

        remove_button = ttk.Button(
            monitor_tab,
            text="<- Remove Selected",
            command=self._remove_selected,
        )
        remove_button.grid(row=2, column=1, sticky="w", pady=(0, 8))

        options = ttk.LabelFrame(
            monitor_tab,
            text="Trip dates",
            style="App.TLabelframe",
            padding=10,
        )
        options.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 8))
        options.columnconfigure(4, weight=1)

        ttk.Label(options, text="Check-in (MM-DD-YYYY)").grid(row=0, column=0, sticky="w")
        check_in_entry = ttk.Entry(options, textvariable=self.check_in_var, width=14)
        check_in_entry.grid(row=0, column=1, sticky="w", padx=(8, 10))
        check_in_entry.bind(
            "<Button-1>",
            lambda _event: self._open_date_range_picker(),
        )

        ttk.Label(options, text="Check-out (MM-DD-YYYY)").grid(row=0, column=2, sticky="w")
        check_out_entry = ttk.Entry(options, textvariable=self.check_out_var, width=14)
        check_out_entry.grid(row=0, column=3, sticky="w", padx=(8, 0))
        check_out_entry.bind(
            "<Button-1>",
            lambda _event: self._open_date_range_picker(),
        )
        ttk.Button(
            options,
            text="New Trip Group",
            command=self._new_trip_group,
        ).grid(row=0, column=4, sticky="e", padx=(16, 8))
        ttk.Button(
            options,
            text="Add/Update Trip Group",
            command=self._add_or_update_trip_group,
        ).grid(row=0, column=5, sticky="e", padx=(0, 8))
        ttk.Button(
            options,
            text="Clear Current",
            command=self._clear_current_trip_selection,
        ).grid(row=0, column=6, sticky="e")

        groups = ttk.LabelFrame(
            monitor_tab,
            text="Trip groups",
            style="App.TLabelframe",
            padding=10,
        )
        groups.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=(8, 8))
        groups.configure(height=TRIP_GROUPS_SECTION_HEIGHT)
        groups.grid_propagate(False)
        groups.columnconfigure(0, weight=1)
        groups.rowconfigure(0, weight=1)

        self.trip_groups_listbox = tk.Listbox(groups, selectmode=tk.SINGLE, exportselection=False)
        self.trip_groups_listbox.grid(row=0, column=0, sticky="nsew")
        groups_scroll = ttk.Scrollbar(
            groups,
            orient=tk.VERTICAL,
            command=self.trip_groups_listbox.yview,
        )
        groups_scroll.grid(row=0, column=1, sticky="ns")
        self.trip_groups_listbox.configure(yscrollcommand=groups_scroll.set)
        self.trip_groups_listbox.bind("<<ListboxSelect>>", self._on_trip_group_selected)

        group_buttons = ttk.Frame(groups)
        group_buttons.grid(row=0, column=2, sticky="ns", padx=(8, 0))
        ttk.Button(
            group_buttons,
            text="Load Group",
            command=self._load_selected_trip_group,
        ).grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(
            group_buttons,
            text="Remove Group",
            command=self._remove_selected_trip_group,
        ).grid(row=1, column=0, sticky="ew")

        preview = ttk.LabelFrame(
            monitor_tab,
            text="Campground preview",
            style="App.TLabelframe",
            padding=10,
        )
        preview.grid(row=5, column=0, columnspan=2, sticky="nsew", pady=(8, 8))
        preview.configure(height=PREVIEW_SECTION_HEIGHT)
        preview.grid_propagate(False)
        preview.columnconfigure(1, weight=1)

        self.preview_title_var = tk.StringVar(value="Select a campground to preview.")
        self.preview_subtitle_var = tk.StringVar(value="")
        self.preview_url_var = tk.StringVar(value="")
        self.preview_status_var = tk.StringVar(value="")

        ttk.Label(
            preview,
            textvariable=self.preview_title_var,
            style="App.SubHeader.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            preview,
            textvariable=self.preview_subtitle_var,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 6))
        preview_link = tk.Label(
            preview,
            textvariable=self.preview_url_var,
            fg="#1565C0",
            cursor="hand2",
            anchor="w",
        )
        preview_link.grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 8))
        preview_link.bind("<Button-1>", lambda _event: self._open_current_campground_url())

        preview_image_frame = ttk.Frame(
            preview,
            width=PREVIEW_MAX_WIDTH,
            height=PREVIEW_MAX_HEIGHT,
        )
        preview_image_frame.grid(row=3, column=0, columnspan=2, sticky="ew")
        preview_image_frame.grid_propagate(False)
        preview_image_frame.columnconfigure(0, weight=1)
        preview_image_frame.rowconfigure(0, weight=1)

        self.preview_image_label = ttk.Label(preview_image_frame, text="No image loaded.", anchor="center")
        self.preview_image_label.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        ttk.Label(preview, textvariable=self.preview_status_var).grid(row=4, column=0, sticky="w", pady=(8, 0))

        settings = ttk.LabelFrame(
            settings_tab,
            text="General settings",
            style="App.TLabelframe",
            padding=10,
        )
        settings.grid(row=0, column=0, sticky="nsew")
        settings_tab.columnconfigure(0, weight=1)
        settings_tab.rowconfigure(1, weight=1)
        settings.columnconfigure(1, weight=1)

        ttk.Label(settings, text="Discord webhook URL").grid(row=0, column=0, sticky="w")
        ttk.Entry(settings, textvariable=self.webhook_var).grid(
            row=0, column=1, sticky="ew", padx=(8, 0)
        )

        ttk.Label(settings, text="Poll seconds").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(settings, textvariable=self.poll_var, width=12).grid(
            row=1, column=1, sticky="w", padx=(8, 0), pady=(10, 0)
        )

        actions = ttk.Frame(frame)
        actions.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        actions.columnconfigure(0, weight=1)

        ttk.Label(actions, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Button(actions, text="Save monitor.json", command=self._save_monitor_file).grid(
            row=0, column=1, sticky="e"
        )

        self.search_listbox.bind("<<ListboxSelect>>", self._on_search_selection)
        self.selected_listbox.bind("<<ListboxSelect>>", self._on_selected_selection)

    def _render_label(self, campground: dict[str, Any]) -> str:
        park = str(campground.get("park") or "").strip()
        if park:
            return f"{campground['name']} ({campground['id']}) - {park}"
        return f"{campground['name']} ({campground['id']})"

    def _refresh_search_results(self) -> None:
        query = self.search_var.get().strip().lower()
        if query:
            self.filtered = [
                item
                for item in self.campgrounds
                if (
                    query in str(item["name"]).lower()
                    or query in str(item["id"])
                    or query in str(item.get("park") or "").lower()
                )
            ]
        else:
            self.filtered = self.campgrounds

        self.search_listbox.delete(0, tk.END)
        for item in self.filtered:
            self.search_listbox.insert(tk.END, self._render_label(item))

        self.status_var.set(f"Loaded {len(self.filtered)} matching campgrounds.")

    def _refresh_selected_list(self) -> None:
        self.selected_listbox.delete(0, tk.END)
        for campground_id in self.selected_ids:
            campground = self.campground_by_id.get(campground_id)
            if campground is None:
                continue
            self.selected_listbox.insert(tk.END, self._render_label(campground))

    def _refresh_trip_groups_list(self) -> None:
        self.trip_groups_listbox.delete(0, tk.END)
        for idx, group in enumerate(self.trip_groups, start=1):
            campground_ids = [int(v) for v in group.get("campground_ids", [])]
            names: list[str] = []
            for campground_id in campground_ids[:3]:
                campground = self.campground_by_id.get(campground_id)
                if campground is None:
                    names.append(str(campground_id))
                else:
                    names.append(str(campground["name"]))
            suffix = ""
            if len(campground_ids) > 3:
                suffix = f", +{len(campground_ids) - 3} more"
            summary = ", ".join(names) + suffix if names else "(no campgrounds)"
            self.trip_groups_listbox.insert(
                tk.END,
                (
                    f"Trip {idx}: {group.get('check_in', '')} to {group.get('check_out', '')} "
                    f"| {summary}"
                ),
            )

    def _validate_current_trip_inputs(self) -> tuple[str, str]:
        check_in = self.check_in_var.get().strip()
        check_out = self.check_out_var.get().strip()
        if not self.selected_ids:
            raise ValueError("Select at least one campground for this trip group.")
        self._validate_dates(check_in, check_out)
        return check_in, check_out

    def _add_or_update_trip_group(self) -> None:
        try:
            check_in, check_out = self._validate_current_trip_inputs()
        except ValueError as exc:
            messagebox.showerror("Invalid trip group", str(exc))
            return

        group = {
            "campground_ids": list(self.selected_ids),
            "check_in": check_in,
            "check_out": check_out,
        }

        if self.active_trip_group_index is None:
            self.trip_groups.append(group)
            self.active_trip_group_index = len(self.trip_groups) - 1
            self.status_var.set(f"Added trip group #{len(self.trip_groups)}.")
        else:
            self.trip_groups[self.active_trip_group_index] = group
            self.status_var.set(f"Updated trip group #{self.active_trip_group_index + 1}.")

        self._refresh_trip_groups_list()
        if self.active_trip_group_index is not None:
            self.trip_groups_listbox.selection_clear(0, tk.END)
            self.trip_groups_listbox.selection_set(self.active_trip_group_index)
            self.trip_groups_listbox.activate(self.active_trip_group_index)

    def _clear_current_trip_selection(self) -> None:
        self.selected_ids = []
        self.active_trip_group_index = None
        self.check_in_var.set("")
        self.check_out_var.set("")
        self._refresh_selected_list()
        self.trip_groups_listbox.selection_clear(0, tk.END)
        self.status_var.set("Cleared current trip selection.")

    def _new_trip_group(self) -> None:
        self._clear_current_trip_selection()
        self.status_var.set("Ready to create a new trip group.")

    def _on_trip_group_selected(self, _event: Any) -> None:
        indexes = self.trip_groups_listbox.curselection()
        if not indexes:
            return
        index = indexes[0]
        if index < 0 or index >= len(self.trip_groups):
            return
        self.active_trip_group_index = index

    def _load_selected_trip_group(self) -> None:
        indexes = self.trip_groups_listbox.curselection()
        if not indexes:
            return
        index = indexes[0]
        if index < 0 or index >= len(self.trip_groups):
            return
        group = self.trip_groups[index]
        self.active_trip_group_index = index
        selected_ids: list[int] = []
        for value in group.get("campground_ids", []):
            try:
                campground_id = int(value)
            except (TypeError, ValueError):
                continue
            if campground_id in self.campground_by_id:
                selected_ids.append(campground_id)
        self.selected_ids = selected_ids
        self.check_in_var.set(str(group.get("check_in", "")))
        self.check_out_var.set(str(group.get("check_out", "")))
        self._refresh_selected_list()
        self.status_var.set(f"Loaded trip group #{index + 1} into current selection.")

    def _remove_selected_trip_group(self) -> None:
        indexes = self.trip_groups_listbox.curselection()
        if not indexes:
            return
        index = indexes[0]
        if index < 0 or index >= len(self.trip_groups):
            return
        self.trip_groups.pop(index)
        if self.active_trip_group_index == index:
            self.active_trip_group_index = None
        elif self.active_trip_group_index is not None and self.active_trip_group_index > index:
            self.active_trip_group_index -= 1
        self._refresh_trip_groups_list()
        self.status_var.set(f"Removed trip group #{index + 1}.")

    def _add_selected_from_search(self) -> None:
        indexes = self.search_listbox.curselection()
        if not indexes:
            return
        for index in indexes:
            campground = self.filtered[index]
            campground_id = int(campground["id"])
            if campground_id not in self.selected_ids:
                self.selected_ids.append(campground_id)
        self._refresh_selected_list()
        self.status_var.set(f"Selected {len(self.selected_ids)} campground(s).")

    def _on_search_selection(self, _event: Any) -> None:
        indexes = self.search_listbox.curselection()
        if not indexes:
            return
        campground = self.filtered[indexes[0]]
        self._start_preview_load(campground)

    def _on_selected_selection(self, _event: Any) -> None:
        indexes = self.selected_listbox.curselection()
        if not indexes:
            return
        campground_id = self.selected_ids[indexes[0]]
        campground = self.campground_by_id.get(campground_id)
        if campground is None:
            return
        self._start_preview_load(campground)

    def _start_preview_load(self, campground: dict[str, Any]) -> None:
        campground_id = int(campground["id"])
        self.current_preview_request_id += 1
        request_id = self.current_preview_request_id
        name = str(campground["name"])
        park = str(campground.get("park") or "").strip()
        campground_url = str(campground.get("url") or "").strip()
        self.preview_title_var.set(f"{name} ({campground_id})")
        self.preview_subtitle_var.set(park)
        self.preview_url_var.set(campground_url)
        self.current_campground_url = campground_url
        self.preview_status_var.set("Loading image...")
        self.preview_image_label.configure(image="", text="Loading image...")
        self.preview_image = None

        thread = threading.Thread(
            target=self._load_preview_worker,
            args=(request_id, campground),
            daemon=True,
        )
        thread.start()

    def _load_preview_worker(self, request_id: int, campground: dict[str, Any]) -> None:
        campground_id = int(campground["id"])
        image_url = self.image_cache.get(campground_id, "")
        if not image_url:
            image_url = self._find_image_url_for_campground(campground)
            if image_url:
                self.image_cache[campground_id] = image_url

        if not image_url:
            self.root.after(
                0,
                lambda: self._apply_preview_failure(request_id, "No image found for this campground."),
            )
            return

        image_result = self._download_image_photo(image_url)
        if not isinstance(image_result, str):
            self.root.after(0, lambda: self._apply_preview_success(request_id, image_result, image_url))
            return
        self.root.after(
            0,
            lambda: self._apply_preview_failure(
                request_id,
                image_result,
                image_url=image_url,
            ),
        )

    def _find_image_url_for_campground(self, campground: dict[str, Any]) -> str:
        existing = str(campground.get("image_url") or "").strip()
        if existing:
            return existing
        campground_url = str(campground.get("url") or "").strip()

        # First try RIDB media when key is available.
        if self.ridb_api_key:
            campground_id = int(campground["id"])
            url = RIDB_MEDIA_URL.format(facility_id=campground_id)
            req = request.Request(
                url,
                method="GET",
                headers={
                    "Accept": "application/json",
                    "apikey": self.ridb_api_key,
                    "User-Agent": "recgov-monitor-gui/1.0",
                },
            )
            try:
                with request.urlopen(req, timeout=20) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except (HTTPError, URLError, json.JSONDecodeError):
                payload = {}

            records = payload.get("RECDATA", []) if isinstance(payload, dict) else []
            if isinstance(records, list):
                for item in records:
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("MediaType") or "").lower() != "image":
                        continue
                    for key in ("URL", "EntityMediaURL", "MediaURL"):
                        media_url = item.get(key)
                        if isinstance(media_url, str) and media_url.startswith("http"):
                            return media_url

        # Fallback: scrape Recreation.gov page metadata (og:image / twitter:image).
        if campground_url:
            fallback = self._find_image_url_from_recreation_page(campground_url)
            if fallback:
                return fallback
        return ""

    def _find_image_url_from_recreation_page(self, campground_url: str) -> str:
        req = request.Request(
            campground_url,
            method="GET",
            headers={
                "Accept": "text/html",
                "User-Agent": "recgov-monitor-gui/1.0",
            },
        )
        try:
            with request.urlopen(req, timeout=20) as response:
                page = response.read().decode("utf-8", errors="ignore")
        except (HTTPError, URLError):
            return ""

        patterns = [
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, page, flags=re.IGNORECASE)
            if not match:
                continue
            image_url = html.unescape(match.group(1)).strip()
            if image_url.startswith("http"):
                return image_url
        return ""

    def _download_image_photo(self, image_url: str) -> Any:
        try:
            with request.urlopen(image_url, timeout=20) as response:
                image_bytes = response.read()
        except (HTTPError, URLError) as exc:
            return f"Image download failed: {exc}"

        try:
            encoded = base64.b64encode(image_bytes).decode("ascii")
            photo = tk.PhotoImage(data=encoded)
            return self._fit_tk_photo(photo)
        except tk.TclError:
            if Image is None or ImageTk is None:
                return (
                    "Unable to render image in Tk (likely unsupported format). "
                    "Install Pillow to render WebP/JPEG in the GUI."
                )
            try:
                pil_image = Image.open(io.BytesIO(image_bytes))
                pil_image.thumbnail((PREVIEW_MAX_WIDTH, PREVIEW_MAX_HEIGHT))
                return ImageTk.PhotoImage(pil_image)
            except Exception:
                return "Unable to render image in Tk or Pillow."

    def _fit_tk_photo(self, photo: tk.PhotoImage) -> tk.PhotoImage:
        width = photo.width()
        height = photo.height()
        if width <= 0 or height <= 0:
            return photo
        scale = max(width / PREVIEW_MAX_WIDTH, height / PREVIEW_MAX_HEIGHT)
        if scale <= 1:
            return photo
        factor = max(1, math.ceil(scale))
        return photo.subsample(factor, factor)

    def _apply_preview_success(self, request_id: int, image: Any, image_url: str) -> None:
        if request_id != self.current_preview_request_id:
            return
        self.preview_image = image
        self.current_image_url = image_url
        self.preview_image_label.configure(image=self.preview_image, text="")
        self.preview_status_var.set("Image loaded.")

    def _apply_preview_failure(
        self,
        request_id: int,
        message: str,
        image_url: str = "",
    ) -> None:
        if request_id != self.current_preview_request_id:
            return
        self.preview_image = None
        self.current_image_url = image_url
        self.preview_image_label.configure(image="", text="No image loaded.")
        if "Unable to render image in Tk" in message and image_url:
            self.preview_status_var.set("Image format not supported by Tk. Use 'Open image URL'.")
            return
        if not self.ridb_api_key:
            self.preview_status_var.set(f"{message} (RIDB key not set; used page fallback only)")
            return
        self.preview_status_var.set(message)

    def _open_current_image_url(self) -> None:
        if not self.current_image_url:
            messagebox.showinfo("No image URL", "No image URL is loaded for this campground yet.")
            return
        webbrowser.open(self.current_image_url)

    def _open_current_campground_url(self) -> None:
        if not self.current_campground_url:
            messagebox.showinfo("No URL", "No campground URL is available for this campground.")
            return
        webbrowser.open(self.current_campground_url)

    def _remove_selected(self) -> None:
        indexes = list(self.selected_listbox.curselection())
        if not indexes:
            return
        for index in reversed(indexes):
            self.selected_ids.pop(index)
        self._refresh_selected_list()
        self.status_var.set(f"Selected {len(self.selected_ids)} campground(s).")

    def _load_existing_monitor_file(self) -> None:
        if not self.monitor_path.exists():
            self.status_var.set(f"{self.monitor_path} not found. Fill fields and save to create it.")
            return

        try:
            raw = load_json(self.monitor_path)
        except Exception as exc:  # noqa: BLE001
            self.status_var.set(f"Could not read {self.monitor_path}: {exc}")
            return

        if not isinstance(raw, dict):
            self.status_var.set(f"{self.monitor_path} is not a JSON object.")
            return

        webhook = raw.get("discord_webhook_url")
        if isinstance(webhook, str):
            self.webhook_var.set(webhook)

        poll = raw.get("poll_seconds")
        if isinstance(poll, int):
            self.poll_var.set(str(poll))

        monitors = raw.get("monitors")
        if isinstance(monitors, list) and monitors:
            loaded_groups: list[dict[str, Any]] = []
            for item in monitors:
                if not isinstance(item, dict):
                    continue
                check_in = item.get("check_in")
                check_out = item.get("check_out")
                campground_ids_raw = item.get("campground_ids")
                if (
                    not isinstance(check_in, str)
                    or not isinstance(check_out, str)
                    or not isinstance(campground_ids_raw, list)
                ):
                    continue

                campground_ids: list[int] = []
                for value in campground_ids_raw:
                    try:
                        campground_id = int(value)
                    except (TypeError, ValueError):
                        continue
                    if campground_id in self.campground_by_id:
                        campground_ids.append(campground_id)

                if not campground_ids:
                    continue

                loaded_groups.append(
                    {
                        "campground_ids": campground_ids,
                        "check_in": storage_to_display_date(check_in),
                        "check_out": storage_to_display_date(check_out),
                    }
                )

            self.trip_groups = loaded_groups
            self._refresh_trip_groups_list()

            if self.trip_groups:
                self.trip_groups_listbox.selection_set(0)
                self.trip_groups_listbox.activate(0)
                self._load_selected_trip_group()

        self.status_var.set(f"Loaded settings from {self.monitor_path}.")

    def _validate_dates(self, check_in: str, check_out: str) -> None:
        try:
            in_date = datetime.strptime(check_in, DISPLAY_DATE_FORMAT).date()
            out_date = datetime.strptime(check_out, DISPLAY_DATE_FORMAT).date()
        except ValueError as exc:
            raise ValueError("Dates must be in MM-DD-YYYY format.") from exc
        if out_date <= in_date:
            raise ValueError("Check-out must be after check-in.")

    def _open_date_range_picker(self) -> None:
        check_in_raw = self.check_in_var.get().strip()
        check_out_raw = self.check_out_var.get().strip()
        today = datetime.now().date()
        initial_check_in = today
        initial_check_out = today

        try:
            initial_check_in = datetime.strptime(check_in_raw, DISPLAY_DATE_FORMAT).date()
        except ValueError:
            initial_check_in = today

        try:
            initial_check_out = datetime.strptime(check_out_raw, DISPLAY_DATE_FORMAT).date()
        except ValueError:
            initial_check_out = initial_check_in

        def _apply_range(start: date, end: date) -> None:
            self.check_in_var.set(start.strftime(DISPLAY_DATE_FORMAT))
            self.check_out_var.set(end.strftime(DISPLAY_DATE_FORMAT))

        DateRangePickerDialog(
            self.root,
            initial_check_in=initial_check_in,
            initial_check_out=initial_check_out,
            on_select=_apply_range,
        )

    def _save_monitor_file(self) -> None:
        webhook = self.webhook_var.get().strip()
        poll_raw = self.poll_var.get().strip()
        groups_to_save: list[dict[str, Any]] = []

        for group in self.trip_groups:
            try:
                check_in_storage = display_to_storage_date(str(group.get("check_in", "")))
                check_out_storage = display_to_storage_date(str(group.get("check_out", "")))
            except ValueError:
                messagebox.showerror(
                    "Invalid trip group",
                    "One of the saved trip groups has invalid dates. Re-open and update that group.",
                )
                return
            campground_ids: list[int] = []
            for value in group.get("campground_ids", []):
                try:
                    campground_ids.append(int(value))
                except (TypeError, ValueError):
                    continue
            if not campground_ids:
                continue
            groups_to_save.append(
                {
                    "campground_ids": campground_ids,
                    "check_in": check_in_storage,
                    "check_out": check_out_storage,
                }
            )

        if not groups_to_save:
            # Backward-compatible fallback for a single current draft.
            check_in = self.check_in_var.get().strip()
            check_out = self.check_out_var.get().strip()
            if not self.selected_ids:
                messagebox.showerror(
                    "Missing trip groups",
                    "Add at least one trip group before saving.",
                )
                return
            try:
                self._validate_dates(check_in, check_out)
                check_in_storage = display_to_storage_date(check_in)
                check_out_storage = display_to_storage_date(check_out)
            except ValueError as exc:
                messagebox.showerror("Invalid dates", str(exc))
                return
            groups_to_save.append(
                {
                    "campground_ids": self.selected_ids,
                    "check_in": check_in_storage,
                    "check_out": check_out_storage,
                }
            )

        payload: dict[str, Any] = {"monitors": groups_to_save}

        if webhook:
            payload["discord_webhook_url"] = webhook

        if poll_raw:
            try:
                poll = int(poll_raw)
            except ValueError:
                messagebox.showerror("Invalid poll_seconds", "Poll seconds must be an integer.")
                return
            if poll < 0:
                messagebox.showerror("Invalid poll_seconds", "Poll seconds must be >= 0.")
                return
            payload["poll_seconds"] = poll

        self.monitor_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        self.status_var.set(
            f"Saved {self.monitor_path} with {len(groups_to_save)} trip group(s)."
        )
        messagebox.showinfo("Saved", f"Updated {self.monitor_path}")


class DateRangePickerDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Tk,
        initial_check_in: date,
        initial_check_out: date,
        on_select: Any,
    ) -> None:
        super().__init__(parent)
        base = initial_check_in
        self.cal = calendar.Calendar(firstweekday=calendar.SUNDAY)
        self.current_year = base.year
        self.current_month = base.month
        self.selected_start: date | None = initial_check_in
        self.selected_end: date | None = (
            initial_check_out if initial_check_out > initial_check_in else None
        )
        self.on_select = on_select

        self.title("Select check-in and check-out")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)

        outer = ttk.Frame(self, padding=8)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(1, weight=1)

        ttk.Button(outer, text="<", width=3, command=self._prev_month).grid(
            row=0, column=0, sticky="w"
        )
        self.header_var = tk.StringVar()
        ttk.Label(outer, textvariable=self.header_var, anchor="center").grid(
            row=0, column=1, sticky="ew"
        )
        ttk.Button(outer, text=">", width=3, command=self._next_month).grid(
            row=0, column=2, sticky="e"
        )

        self.grid_frame = ttk.Frame(outer)
        self.grid_frame.grid(row=1, column=0, columnspan=3, pady=(8, 0))

        self.selection_label_var = tk.StringVar()
        ttk.Label(outer, textvariable=self.selection_label_var).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(8, 0)
        )

        buttons = ttk.Frame(outer)
        buttons.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        buttons.columnconfigure(0, weight=1)
        ttk.Button(buttons, text="Cancel", command=self.destroy).grid(row=0, column=0, sticky="w")
        self.apply_button = ttk.Button(buttons, text="Apply", command=self._apply_selection)
        self.apply_button.grid(row=0, column=1, sticky="e")
        self._render()

    def _prev_month(self) -> None:
        if self.current_month == 1:
            self.current_month = 12
            self.current_year -= 1
        else:
            self.current_month -= 1
        self._render()

    def _next_month(self) -> None:
        if self.current_month == 12:
            self.current_month = 1
            self.current_year += 1
        else:
            self.current_month += 1
        self._render()

    def _render(self) -> None:
        for child in self.grid_frame.winfo_children():
            child.destroy()

        self.header_var.set(f"{calendar.month_name[self.current_month]} {self.current_year}")
        for col, name in enumerate(("Su", "Mo", "Tu", "We", "Th", "Fr", "Sa")):
            ttk.Label(self.grid_frame, text=name, width=4, anchor="center").grid(
                row=0, column=col, padx=1, pady=1
            )

        month_rows = self.cal.monthdayscalendar(self.current_year, self.current_month)
        for row_idx, week in enumerate(month_rows, start=1):
            for col_idx, day_num in enumerate(week):
                if day_num == 0:
                    ttk.Label(self.grid_frame, text="", width=4).grid(
                        row=row_idx, column=col_idx, padx=1, pady=1
                    )
                    continue
                day_date = date(self.current_year, self.current_month, day_num)
                bg = None
                fg = None
                if self.selected_start and day_date == self.selected_start:
                    bg = "#1565C0"
                    fg = "white"
                elif self.selected_end and day_date == self.selected_end:
                    bg = "#1565C0"
                    fg = "white"
                elif (
                    self.selected_start
                    and self.selected_end
                    and self.selected_start < day_date < self.selected_end
                ):
                    bg = "#D6E9FF"

                button = tk.Button(
                    self.grid_frame,
                    text=str(day_num),
                    width=4,
                    command=lambda d=day_num: self._pick_day(d),
                )
                if bg is not None:
                    button.configure(bg=bg)
                if fg is not None:
                    button.configure(fg=fg)
                button.grid(row=row_idx, column=col_idx, padx=1, pady=1)

        if self.selected_start and self.selected_end:
            self.selection_label_var.set(
                "Selected: "
                f"{self.selected_start.strftime(DISPLAY_DATE_FORMAT)} to "
                f"{self.selected_end.strftime(DISPLAY_DATE_FORMAT)}"
            )
            self.apply_button.state(["!disabled"])
        elif self.selected_start:
            self.selection_label_var.set(
                "Selected check-in: "
                f"{self.selected_start.strftime(DISPLAY_DATE_FORMAT)} "
                "(pick a check-out date)"
            )
            self.apply_button.state(["disabled"])
        else:
            self.selection_label_var.set("Select check-in and check-out dates.")
            self.apply_button.state(["disabled"])

    def _pick_day(self, day_num: int) -> None:
        picked = date(self.current_year, self.current_month, day_num)
        if self.selected_start is None or (self.selected_start and self.selected_end):
            self.selected_start = picked
            self.selected_end = None
        elif picked <= self.selected_start:
            self.selected_start = picked
            self.selected_end = None
        else:
            self.selected_end = picked
        self._render()

    def _apply_selection(self) -> None:
        if not self.selected_start or not self.selected_end:
            return
        self.on_select(self.selected_start, self.selected_end)
        self.destroy()


def main() -> None:
    args = parse_args()
    campgrounds_path = Path(args.campgrounds_file)
    monitor_path = Path(args.monitor_file)
    ridb_api_key = args.ridb_api_key.strip()
    if not ridb_api_key:
        import os

        ridb_api_key = os.getenv("RIDB_API_KEY", "").strip()

    try:
        campgrounds = load_campgrounds(campgrounds_path)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Failed to load campgrounds catalog: {exc}") from exc

    root = tk.Tk()
    app = MonitorEditorApp(
        root,
        campgrounds=campgrounds,
        monitor_path=monitor_path,
        ridb_api_key=ridb_api_key,
    )
    root.after(0, lambda: app.status_var.set(f"Catalog loaded from {campgrounds_path}."))
    root.mainloop()


if __name__ == "__main__":
    main()

