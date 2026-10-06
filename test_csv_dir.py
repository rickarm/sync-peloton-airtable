#!/usr/bin/env python3
"""
Offline tests for where peloton-sync.sh looks for the newest CSV.

PELOTON_CSV_DIR (default ~/.local/share/peloton-sync/csv) is shared with
peloton-workout-extract's downloader. It must never be a macOS per-app
protected folder (Downloads, Desktop, Documents), where an unpermitted process
hangs, or a git repo. These runs stop before any network call.

    pytest test_csv_dir.py
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "peloton-sync.sh"


def run(home, csv_dir=None):
    env = {"HOME": str(home), "PATH": os.environ["PATH"]}
    if csv_dir is not None:
        env["PELOTON_CSV_DIR"] = str(csv_dir)
    return subprocess.run(
        ["bash", str(SCRIPT), "--dry-run"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(home),
    )


def test_default_dir_is_created_with_mode_700_and_named_in_the_error(tmp_path):
    r = run(tmp_path)
    default = tmp_path / ".local/share/peloton-sync/csv"
    assert r.returncode == 1
    assert default.is_dir()
    assert stat.S_IMODE(default.stat().st_mode) == 0o700
    assert "No Peloton CSV found in" in r.stdout
    assert "Downloads" not in r.stdout + r.stderr


@pytest.mark.parametrize("name", ["Downloads", "Desktop", "Documents"])
def test_protected_folders_are_refused_without_being_created(tmp_path, name):
    target = tmp_path / name / "peloton"
    r = run(tmp_path, target)
    assert r.returncode == 1
    assert f"inside ~/{name}" in r.stderr
    assert not (tmp_path / name).exists()


def test_dir_inside_git_repo_is_refused(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    r = run(tmp_path, repo / "csv")
    assert r.returncode == 1
    assert "inside a git repository" in r.stderr


def test_newest_csv_in_the_dir_is_picked(tmp_path):
    d = tmp_path / "csv"
    d.mkdir()
    old, new = d / "Big__Cheese_workouts_old.csv", d / "Big__Cheese_workouts.csv"
    old.write_text("x\n")
    new.write_text("x\n")
    os.utime(old, (1_000_000_000, 1_000_000_000))
    (d / "Big__Cheese_workouts.csv.part").write_text("partial\n")
    r = run(tmp_path, d)
    assert f"Auto-detected: {new.resolve()}" in r.stdout
