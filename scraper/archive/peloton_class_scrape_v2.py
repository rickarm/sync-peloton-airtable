#!/usr/bin/env python3
"""
Peloton class metadata scraper (v2)

Improvements over the earlier version:
- scopes extraction to the class details modal when possible
- avoids scraping random page-wide nav text
- ignores images entirely
- tries harder to capture the class timestamp / air date
- supports URL as positional argument or via --url
- auto-loads env vars from ~/.env, ./.env, and script-dir .env

Required env vars:
PELOTON_EMAIL=you@example.com
PELOTON_PASSWORD=your-password
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import Locator, sync_playwright

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


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


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def load_env_files() -> None:
    if load_dotenv is None:
        return
    candidates = [
        Path.home() / ".env",
        Path.cwd() / ".env",
        Path(__file__).resolve().parent / ".env",
    ]
    seen = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        if resolved in seen:
            continue
        seen.add(resolved)
        if path.exists():
            load_dotenv(path, override=False)


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


def maybe_click(page, labels: List[str], timeout_ms: int = 2500) -> bool:
    for label in labels:
        selectors = [
            f"button:has-text('{label}')",
            f"[role='button']:has-text('{label}')",
            f"text='{label}'",
        ]
        for selector in selectors:
            try:
                loc = page.locator(selector).first
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
    if not text:
        return None
    lines = [clean_text(x) for x in text.splitlines()]
    lines = [x for x in lines if x]

    for line in lines:
        if title and line == title:
            continue
        if instructor and instructor.replace(" ", "") in line.replace(" ", ""):
            continue
        if parse_duration_minutes(line) is not None and len(line) < 80:
            continue
        if extract_timestamp(line):
            continue
        if len(line) >= 50 and not re.fullmatch(r"[A-Za-z ]{1,20}", line):
            return line
    return None


def scrape_segments_from_modal(modal: Locator) -> List[Dict[str, Any]]:
    segments: List[Dict[str, Any]] = []

    try:
        text = modal.inner_text(timeout=3000)
    except Exception:
        return segments

    lines = [clean_text(line) for line in text.splitlines()]
    lines = [line for line in lines if line]

    time_re = re.compile(r"\b(\d{1,2}:\d{2})\b")
    zone_re = re.compile(r"\bZone\s*([1-7])\b", re.IGNORECASE)
    section_names = {"warm up", "cycling", "cool down", "running", "strength", "recovery", "intervals"}

    current_section = None
    section_items: Dict[str, List[Dict[str, Any]]] = {}

    for line in lines:
        low = line.lower()
        if low in section_names:
            current_section = line
            section_items.setdefault(current_section, [])
            continue

        zone_match = zone_re.search(line)
        time_match = time_re.search(line)

        if zone_match and time_match:
            item = {
                "zone": f"Zone {zone_match.group(1)}",
                "time": time_match.group(1),
                "raw": line,
            }
            key = current_section or "Uncategorized"
            section_items.setdefault(key, []).append(item)

    for section_name, items in section_items.items():
        segments.append({"section": section_name, "items": items})

    return segments


def login(page, email: str, password: str) -> None:
    page.goto("https://members.onepeloton.com/", wait_until="domcontentloaded", timeout=90000)
    time.sleep(2)

    maybe_click(page, ["Log In", "Sign In"], timeout_ms=2500)
    time.sleep(2)

    email_selectors = [
        "input[type='email']",
        "input[name='usernameOrEmail']",
        "input[name='email']",
        "input[autocomplete='username']",
    ]
    password_selectors = [
        "input[type='password']",
        "input[name='password']",
        "input[autocomplete='current-password']",
    ]

    email_filled = False
    for sel in email_selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                loc.fill(email, timeout=4000)
                email_filled = True
                break
        except Exception:
            pass

    password_filled = False
    for sel in password_selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                loc.fill(password, timeout=4000)
                password_filled = True
                break
        except Exception:
            pass

    if not (email_filled and password_filled):
        raise RuntimeError("Could not find Peloton login fields. UI may have changed.")

    if not maybe_click(page, ["Log In", "Sign In", "Continue"], timeout_ms=4000):
        try:
            page.keyboard.press("Enter")
        except Exception:
            pass

    page.wait_for_load_state("domcontentloaded", timeout=90000)
    time.sleep(3)


def scrape_class_metadata(page, url: str, debug: bool = False) -> Dict[str, Any]:
    page.goto(url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_load_state("networkidle", timeout=90000)
    time.sleep(1.5)

    modal = wait_for_class_modal(page)
    root = modal if modal is not None else page.locator("body")

    try:
        scoped_text = root.inner_text(timeout=4000)
    except Exception:
        scoped_text = ""
    scoped_text = scoped_text or ""

    title = maybe_get_text_from(root, [
        "h1",
        "h2",
        "[data-test-id='classTitle']",
        "[class*='title']",
    ])

    instructor = maybe_get_text_from(root, [
        "[data-test-id='instructorName']",
        "a[href*='/instructors/']",
        "[class*='instructor']",
    ])
    instructor = normalize_instructor_name(instructor)

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

    maybe_click(page, ["View Details", "Details", "Class Plan"], timeout_ms=2000)
    time.sleep(0.8)
    segments = scrape_segments_from_modal(modal) if modal is not None else []

    zone_allocations = []
    for seg in segments:
        for item in seg.get("items", []):
            zone_allocations.append({
                "section": seg.get("section"),
                "zone": item.get("zone"),
                "time": item.get("time"),
                "raw": item.get("raw"),
            })

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

    return result


def main() -> int:
    load_env_files()

    parser = argparse.ArgumentParser(description="Scrape Peloton class metadata")
    parser.add_argument("url_positional", nargs="?", help="Peloton class/workout URL")
    parser.add_argument("--url", help="Peloton class/workout URL containing classId")
    parser.add_argument("--class-id", help="Peloton classId")
    parser.add_argument("--email", default=os.getenv("PELOTON_EMAIL"))
    parser.add_argument("--password", default=os.getenv("PELOTON_PASSWORD"))
    parser.add_argument("--save-json", help="Optional path to write JSON output")
    parser.add_argument("--headful", action="store_true", help="Run browser with UI visible")
    parser.add_argument("--debug", action="store_true", help="Include captured modal text in output")
    args = parser.parse_args()

    if not args.url and not args.url_positional and not args.class_id:
        eprint("Provide a URL, positional URL, or --class-id.")
        return 2
    if not args.email or not args.password:
        eprint("Missing Peloton credentials. Add PELOTON_EMAIL and PELOTON_PASSWORD to ~/.env.")
        return 2

    url = build_url(args)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not args.headful)
            context = browser.new_context()
            page = context.new_page()

            login(page, args.email, args.password)
            data = scrape_class_metadata(page, url, debug=args.debug)

            browser.close()
    except PlaywrightTimeoutError as e:
        eprint(f"Playwright timeout: {e}")
        return 1
    except Exception as e:
        eprint(f"Error: {e}")
        return 1

    output = json.dumps(data, indent=2, ensure_ascii=False)
    print(output)

    if args.save_json:
        Path(args.save_json).write_text(output, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
