#!/usr/bin/env python3
"""
Peloton class metadata scraper using saved Playwright session state.

Preferred workflow:
1. Run peloton_login_save_session.py once
2. Reuse peloton_state.json for future scrapes

Usage:
  python peloton_class_scrape_stateful.py --url 'https://members.onepeloton.com/classes/...'
  python peloton_class_scrape_stateful.py 'https://members.onepeloton.com/classes/...'

Outputs JSON with:
- class_id
- class_detail_url
- ride_title
- instructor
- discipline
- duration_minutes
- class_timestamp
- description
- segments
- zone_allocations
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import Locator, sync_playwright

BASE_CLASS_URL = "https://members.onepeloton.com/classes/cycling?modal=classDetailsModal&classId={class_id}"

DISCIPLINES = {
    "cycling", "running", "walking", "strength", "yoga",
    "stretching", "meditation", "bootcamp", "rowing",
    "tread", "bike", "cardio"
}

TIMESTAMP_PATTERNS = [
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\d{1,2}/\d{1,2}/\d{2}\s+@\s+\d{1,2}:\d{2}\s+(?:AM|PM)\b",
    r"\b(?:Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|Jul|July|Aug|August|Sep|Sept|September|Oct|October|Nov|November|Dec|December)\s+\d{1,2},\s+\d{4}(?:\s+at\s+\d{1,2}:\d{2}\s+(?:AM|PM))?\b",
    r"\b\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?\b",
]

SECTION_NAMES = ["Warm Up", "Cycling", "Cool Down", "Running", "Strength", "Recovery", "Intervals"]


def clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def extract_class_id(url: str) -> Optional[str]:
    m = re.search(r"[?&]classId=([A-Za-z0-9]+)", url)
    return m.group(1) if m else None


def build_url(args: argparse.Namespace) -> str:
    if args.url:
        return args.url
    if args.url_positional:
        return args.url_positional
    if args.class_id:
        return BASE_CLASS_URL.format(class_id=args.class_id)
    raise ValueError("Provide a URL or --class-id")


def maybe_click(page_or_root, labels: List[str], timeout_ms: int = 2500) -> bool:
    for label in labels:
        selectors = [
            f"button:has-text('{label}')",
            f"[role='button']:has-text('{label}')",
            f"text='{label}'",
        ]
        for selector in selectors:
            try:
                loc = page_or_root.locator(selector).first
                if loc.count() > 0 and loc.is_visible(timeout=timeout_ms):
                    loc.click(timeout=timeout_ms)
                    return True
            except Exception:
                pass
    return False


def maybe_get_text_from(root: Locator, selectors: List[str], timeout_ms: int = 1200) -> Optional[str]:
    for selector in selectors:
        try:
            loc = root.locator(selector).first
            if loc.count() > 0 and loc.is_visible(timeout=timeout_ms):
                txt = clean_text(loc.text_content(timeout=timeout_ms))
                if txt:
                    return txt
        except Exception:
            pass
    return None


def parse_duration_minutes(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"(\d+)\s*min\b", text, re.IGNORECASE)
    return int(m.group(1)) if m else None


def normalize_instructor_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def wait_for_class_modal(page) -> Optional[Locator]:
    maybe_click(page, ["More info", "More Info"], timeout_ms=1500)
    time.sleep(0.5)

    selectors = [
        "[role='dialog']",
        "[aria-modal='true']",
        "div[data-test-id*='modal']",
        "div[class*='modal']",
        "div[class*='drawer']",
    ]
    candidates = []
    for selector in selectors:
        try:
            loc = page.locator(selector)
            count = min(loc.count(), 8)
            for i in range(count):
                item = loc.nth(i)
                try:
                    if item.is_visible(timeout=800):
                        text = clean_text(item.inner_text(timeout=1500)) or ""
                        candidates.append((len(text), item))
                except Exception:
                    pass
        except Exception:
            pass

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
    return None


def infer_discipline_from_url(url: str) -> Optional[str]:
    m = re.search(r"/classes/([^/?]+)", url)
    if not m:
        return None
    raw = m.group(1).strip().lower()
    mapping = {
        "cycling": "Cycling",
        "running": "Running",
        "walking": "Walking",
        "strength": "Strength",
        "yoga": "Yoga",
        "stretching": "Stretching",
        "meditation": "Meditation",
        "bootcamp": "Bootcamp",
        "rowing": "Rowing",
    }
    return mapping.get(raw, raw.title())


def extract_timestamp(text: str) -> Optional[str]:
    for pat in TIMESTAMP_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return clean_text(m.group(0))
    return None


def extract_description_from_text(text: str, title: Optional[str], instructor: Optional[str]) -> Optional[str]:
    return None

def try_expand_plan(page, modal: Optional[Locator]) -> None:
    root = modal if modal is not None else page
    maybe_click(root, ["View Details", "Details", "Class Plan"], timeout_ms=2500)
    time.sleep(0.8)
    for _ in range(2):
        for name in SECTION_NAMES:
            maybe_click(root, [name], timeout_ms=1200)
            time.sleep(0.15)


def extract_section_block(text: str, section: str, next_sections: List[str]) -> str:
    escaped = re.escape(section)
    next_pattern = "|".join(re.escape(s) for s in next_sections) if next_sections else r"$^"
    pattern = rf"(?is)\b{escaped}\b(.*?)(?=\b(?:{next_pattern})\b|$)"
    m = re.search(pattern, text)
    return m.group(1).strip() if m else ""


def parse_items_from_block(block: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not block:
        return items

    lines = [clean_text(x) for x in block.splitlines()]
    lines = [x for x in lines if x]

    zone_time_re = re.compile(r"\bZone\s*([1-7])\b.*?\b(\d{1,2}:\d{2})\b", re.IGNORECASE)
    i = 0
    while i < len(lines):
        line = lines[i]
        m = zone_time_re.search(line)
        if m:
            items.append({"zone": f"Zone {m.group(1)}", "time": m.group(2), "raw": line})
            i += 1
            continue

        zone_only = re.fullmatch(r"Zone\s*([1-7])", line, re.IGNORECASE)
        if zone_only and i + 1 < len(lines):
            next_line = lines[i + 1]
            time_only = re.fullmatch(r"(\d{1,2}:\d{2})", next_line)
            if time_only:
                items.append({
                    "zone": f"Zone {zone_only.group(1)}",
                    "time": time_only.group(1),
                    "raw": f"{line} | {next_line}",
                })
                i += 2
                continue

        if i + 1 < len(lines):
            next_line = lines[i + 1]
            time_only = re.fullmatch(r"(\d{1,2}:\d{2})", next_line)
            if time_only and len(line) <= 40 and "@" not in line and "/" not in line:
                items.append({
                    "zone": None,
                    "time": time_only.group(1),
                    "raw": f"{line} | {next_line}",
                    "label": line,
                })
                i += 2
                continue
        i += 1

    deduped = []
    seen = set()
    for item in items:
        key = (item.get("zone"), item.get("time"), item.get("raw"))
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def scrape_segments_from_modal(modal: Locator) -> List[Dict[str, Any]]:
    sections: List[Dict[str, Any]] = []

    try:
        text = modal.inner_text(timeout=4000) or ""
    except Exception:
        text = ""

    if "Warm Up" in text:
        sections.append({"section": "Warm Up", "items": []})

    cycling_items = []

    # Debug dump: save nearby HTML/text for the Cycling section
    try:
        cycling_header = modal.locator("text='Cycling'").first
        if cycling_header.count() > 0:
            container = cycling_header.locator("xpath=..").first
            html = container.evaluate("(el) => el.outerHTML")
            Path("cycling_debug.html").write_text(html, encoding="utf-8")

            try:
                nearby_text = container.locator("xpath=following-sibling::*").all_inner_texts()
                Path("cycling_debug.txt").write_text("\n\n---\n\n".join(nearby_text), encoding="utf-8")
            except Exception:
                pass
    except Exception:
        pass

    # Keep current fallback behavior
    lines = [clean_text(x) for x in text.splitlines()]
    lines = [x for x in lines if x]

    i = 0
    while i < len(lines):
        line = lines[i]

        zone_only = re.fullmatch(r"Zone\s*([1-7])", line, re.IGNORECASE)
        if zone_only and i + 1 < len(lines):
            next_line = lines[i + 1]
            time_only = re.fullmatch(r"(\d{1,2}:\d{2})", next_line)
            if time_only:
                cycling_items.append({
                    "zone": f"Zone {zone_only.group(1)}",
                    "time": time_only.group(1),
                    "raw": f"{line} | {next_line}",
                })
                i += 2
                continue

        zone_time_same_line = re.search(r"\bZone\s*([1-7])\b.*?\b(\d{1,2}:\d{2})\b", line, re.IGNORECASE)
        if zone_time_same_line:
            cycling_items.append({
                "zone": f"Zone {zone_time_same_line.group(1)}",
                "time": zone_time_same_line.group(2),
                "raw": line,
            })
            i += 1
            continue

        i += 1

    deduped = []
    seen = set()
    for item in cycling_items:
        key = (item.get("zone"), item.get("time"), item.get("raw"))
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    cycling_items = deduped

    if "Cycling" in text:
        sections.append({"section": "Cycling", "items": cycling_items})

    if "Cool Down" in text:
        sections.append({"section": "Cool Down", "items": []})

    return sections

def scrape_class_metadata(page, url: str, debug: bool = False) -> Dict[str, Any]:
    page.goto(url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_load_state("networkidle", timeout=90000)
    time.sleep(1.5)

    modal = wait_for_class_modal(page)
    root = modal if modal is not None else page.locator("body")

    scoped_text = ""
    try:
        scoped_text = root.inner_text(timeout=4000) or ""
    except Exception:
        pass

    title = maybe_get_text_from(root, ["h1", "h2", "[data-test-id='classTitle']", "[class*='title']"])
    instructor = normalize_instructor_name(maybe_get_text_from(root, [
        "[data-test-id='instructorName']",
        "a[href*='/instructors/']",
        "[class*='instructor']",
    ]))

    duration_minutes = parse_duration_minutes(title)
    if duration_minutes is None:
        for line in scoped_text.splitlines()[:12]:
            duration_minutes = parse_duration_minutes(line)
            if duration_minutes is not None:
                break

    discipline = infer_discipline_from_url(url)
    if discipline is None:
        for line in scoped_text.splitlines()[:15]:
            line_clean = clean_text(line)
            if line_clean and line_clean.lower() in DISCIPLINES:
                discipline = line_clean.title()
                break

    class_timestamp = extract_timestamp(scoped_text)
    description = extract_description_from_text(scoped_text, title, instructor)

    try_expand_plan(page, modal)
    plan_text = scoped_text
    if modal is not None:
        try:
            plan_text = modal.inner_text(timeout=5000) or scoped_text
        except Exception:
            pass

    segments = scrape_segments_from_modal(modal) if modal is not None else []
    zone_allocations = []
    for seg in segments:
        for item in seg.get("items", []):
            out = {
                "section": seg.get("section"),
                "zone": item.get("zone"),
                "time": item.get("time"),
                "raw": item.get("raw"),
            }
            if item.get("label"):
                out["label"] = item.get("label")
            zone_allocations.append(out)

    result = {
        "class_id": extract_class_id(url),
        "class_detail_url": url,
        "ride_title": title,
        "instructor": instructor,
        "discipline": discipline,
        "duration_minutes": duration_minutes,
        "class_timestamp": class_timestamp,
        "description": description,
        "segments": segments,
        "zone_allocations": zone_allocations,
    }

    if debug:
        result["_debug_modal_text"] = scoped_text[:8000]
        result["_debug_plan_text"] = plan_text[:12000]

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape Peloton class metadata with saved session")
    parser.add_argument("url_positional", nargs="?", help="Peloton class/workout URL")
    parser.add_argument("--url", help="Peloton class/workout URL containing classId")
    parser.add_argument("--class-id", help="Peloton classId")
    parser.add_argument("--save-json", help="Optional path to write JSON output")
    parser.add_argument("--headful", action="store_true", help="Run browser with UI visible")
    parser.add_argument("--debug", action="store_true", help="Include debug text")
    parser.add_argument("--state-file", default="peloton_state.json", help="Path to Playwright storage state")
    args = parser.parse_args()

    if not args.url and not args.url_positional and not args.class_id:
        print("Provide a URL, positional URL, or --class-id.", flush=True)
        return 2

    url = build_url(args)
    state_path = Path(args.state_file).expanduser().resolve()
    if not state_path.exists():
        print(f"Missing session state file: {state_path}", flush=True)
        return 2

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not args.headful)
            context = browser.new_context(storage_state=str(state_path))
            page = context.new_page()
            data = scrape_class_metadata(page, url, debug=args.debug)
            browser.close()
    except PlaywrightTimeoutError as e:
        print(f"Playwright timeout: {e}", flush=True)
        return 1
    except Exception as e:
        print(f"Error: {e}", flush=True)
        return 1

    output = json.dumps(data, indent=2, ensure_ascii=False)
    print(output)

    if args.save_json:
        Path(args.save_json).write_text(output, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
