#!/usr/bin/env python3
"""
Log into Peloton once in a visible browser and save Playwright session state.

Usage:
  python peloton_login_save_session.py

It loads credentials from:
1. ~/.env
2. ./.env
3. .env next to this script

Expected vars:
PELOTON_EMAIL=you@example.com
PELOTON_PASSWORD=your-password

Output:
- peloton_state.json in the current directory by default
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


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


def maybe_click(page, labels, timeout_ms=2500) -> bool:
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


def main() -> int:
    load_env_files()

    parser = argparse.ArgumentParser(description="Log into Peloton and save session state")
    parser.add_argument("--state-file", default="peloton_state.json", help="Path to save Playwright storage state")
    parser.add_argument("--email", default=os.getenv("PELOTON_EMAIL"))
    parser.add_argument("--password", default=os.getenv("PELOTON_PASSWORD"))
    args = parser.parse_args()

    if not args.email or not args.password:
        print("Missing PELOTON_EMAIL or PELOTON_PASSWORD in ~/.env", flush=True)
        return 2

    state_path = Path(args.state_file).expanduser().resolve()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

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

        for sel in email_selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.fill(args.email, timeout=4000)
                    break
            except Exception:
                pass

        for sel in password_selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.fill(args.password, timeout=4000)
                    break
            except Exception:
                pass

        maybe_click(page, ["Log In", "Sign In", "Continue"], timeout_ms=4000)

        print("\nFinish login in the browser window if prompted.")
        print("When your Peloton homepage or a logged-in page is visible, press Enter here to save the session.\n")
        input()

        context.storage_state(path=str(state_path))
        browser.close()

    print(f"Saved session state to {state_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
