#!/usr/bin/env python3
"""
Scrape Peloton class metadata from a class URL using Playwright.

Requirements
------------
python3 -m pip install playwright python-dotenv
python3 -m playwright install chromium

.env support
------------
This script automatically tries to load environment variables from:
1. ~/.env
2. ./.env (current working directory)
3. .env next to this script

Expected variables:
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
from playwright.sync_api import sync_playwright

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


BASE_CLASS_URL = "https://members.onepeloton.com/classes/cycling?modal=classDetailsModal&classId={class_id}"


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
    raise ValueError("Provide a URL as a positional argument, use --url, or provide --class-id")


def clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def maybe_click(page, labels: List[str], timeout_ms: int = 2500) -> bool:
    candidates = []
    for label in labels:
        candidates.extend([
            f"button:has-text('{label}')",
            f"[role='button']:has-text('{label}')",
            f"text='{label}'",
        ])
    for selector in candidates:
        try:
            locator = page.locator(selector).first
            if locator.is_visible(timeout=timeout_ms):
                locator.click(timeout=timeout_ms)
                return True
        except Exception:
            pass
    return False


def maybe_get_text(page, selectors: List[str], timeout_ms: int = 1500) -> Optional[str]:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.is_visible(timeout=timeout_ms):
                txt = locator.text_content(timeout=timeout_ms)
                txt = clean_text(txt)
                if txt:
                    return txt
        except Exception:
            pass
    return None


def maybe_get_attr(page, selectors: List[str], attr: str, timeout_ms: int = 1500) -> Optional[str]:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() > 0:
                value = locator.get_attribute(attr, timeout=timeout_ms)
                value = clean_text(value)
                if value:
                    return value
        except Exception:
            pass
    return None


def parse_duration_minutes(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"(\d+)\s*min", text, re.IGNORECASE)
    return int(m.group(1)) if m else None


def scrape_segments(page) -> List[Dict[str, Any]]:
    segments: List[Dict[str, Any]] = []

    maybe_click(page, ["View Details", "Details", "Class Plan"])
    time.sleep(1.0)

    for label in ["Warm Up", "Cycling", "Cool Down", "Running", "Strength", "Intervals", "Recovery"]:
        maybe_click(page, [label], timeout_ms=800)

    time.sleep(1.0)

    body_text = page.locator("body").inner_text(timeout=3000)
    lines = [clean_text(line) for line in body_text.splitlines()]
    lines = [line for line in lines if line]

    current_section = None
    section_names = {
        "warm up", "cycling", "cool down", "running", "strength",
        "intervals", "recovery", "rowing", "walk", "jog"
    }

    section_items: Dict[str, List[Dict[str, Any]]] = {}
    time_re = re.compile(r"\b(\d{1,2}:\d{2})\b")
    zone_re = re.compile(r"\bZone\s*([1-7])\b", re.IGNORECASE)

    for line in lines:
        low = line.lower()
        if low in section_names:
            current_section = line
            section_items.setdefault(current_section, [])
            continue

        zone_match = zone_re.search(line)
        time_match = time_re.search(line)
        if zone_match or time_match:
            item: Dict[str, Any] = {}
            if zone_match:
                item["zone"] = f"Zone {zone_match.group(1)}"
            if time_match:
                item["time"] = time_match.group(1)
            item["raw"] = line
            key = current_section or "Uncategorized"
            section_items.setdefault(key, []).append(item)

    for section_name, items in section_items.items():
        segments.append({"section": section_name, "items": items})

    return segments


def login(page, email: str, password: str) -> None:
    page.goto("https://members.onepeloton.com/", wait_until="domcontentloaded", timeout=90000)
    time.sleep(2)

    maybe_click(page, ["Log In", "Sign In"])
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


def scrape_class_metadata(page, url: str) -> Dict[str, Any]:
    page.goto(url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_load_state("networkidle", timeout=90000)
    time.sleep(2)

    maybe_click(page, ["More info", "More Info"])
    time.sleep(0.75)

    title = maybe_get_text(page, ["h1", "h2", "[data-test-id='classTitle']", "[class*='title']"])
    subtitle = maybe_get_text(page, ["[data-test-id='classSubtitle']", "h3", "[class*='subtitle']"])
    instructor = maybe_get_text(page, ["[data-test-id='instructorName']", "a[href*='/instructors/']", "[class*='instructor']"])
    description = maybe_get_text(page, ["[data-test-id='description']", "[class*='description']"])
    image_url = maybe_get_attr(page, ["img", "[role='img'] img"], "src")

    page_text = page.locator("body").inner_text(timeout=3000)
    discipline = None
    duration_minutes = None
    class_timestamp = None

    lines = [clean_text(line) for line in page_text.splitlines()]
    lines = [line for line in lines if line]

    for line in lines:
        if duration_minutes is None:
            duration_minutes = parse_duration_minutes(line)
        if discipline is None and line.lower() in {
            "cycling", "running", "walking", "strength", "yoga",
            "stretching", "meditation", "bootcamp", "rowing"
        }:
            discipline = line
        if class_timestamp is None and re.search(r"\b\d{4}-\d{2}-\d{2}\b", line):
            class_timestamp = line

    class_id = extract_class_id(url)
    segments = scrape_segments(page)

    zone_allocations = []
    for seg in segments:
        for item in seg.get("items", []):
            if item.get("zone") or item.get("time"):
                zone_allocations.append({
                    "section": seg.get("section"),
                    "zone": item.get("zone"),
                    "time": item.get("time"),
                    "raw": item.get("raw"),
                })

    return {
        "class_id": class_id,
        "class_detail_url": url,
        "ride_title": title,
        "subtitle": subtitle,
        "instructor": instructor,
        "discipline": discipline,
        "duration_minutes": duration_minutes,
        "class_timestamp": class_timestamp,
        "description": description,
        "image_url": image_url,
        "segments": segments,
        "zone_allocations": zone_allocations,
    }


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
    args = parser.parse_args()

    if not args.url and not args.url_positional and not args.class_id:
        eprint("Provide a URL, a positional URL argument, or --class-id.")
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
            data = scrape_class_metadata(page, url)

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
        with open(args.save_json, "w", encoding="utf-8") as f:
            f.write(output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())