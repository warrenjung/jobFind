"""Chrome, Playwright, and browser-session helpers for autofill."""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from autofill_review import clean_text


INDEED_LOGIN_URL = "https://secure.indeed.com/auth"


def persistent_profile_path(data_dir: Path, site: str = "indeed") -> Path:
    """Return the local-only browser profile directory for a job site."""
    return data_dir / "browser_profiles" / site


def find_chrome_executable() -> Optional[str]:
    """Locate a real Google Chrome / Chromium binary, or None if not installed."""
    system = platform.system()
    candidates: list[str] = []
    if system == "Darwin":
        candidates += [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif system == "Windows":
        candidates += [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ]
    else:
        candidates += [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ]

    for path in candidates:
        if path and Path(path).exists():
            return path
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def free_debug_port(preferred: int = 9222) -> int:
    """Return an open localhost port, preferring 9222."""
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
                return sock.getsockname()[1]
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def cdp_endpoint(debug_port: int) -> str:
    """CDP HTTP endpoint for a given debug port."""
    return f"http://127.0.0.1:{debug_port}"


def chrome_debug_ready(debug_port: int) -> bool:
    """Whether Chrome's remote-debugging endpoint is responding."""
    try:
        with urllib.request.urlopen(f"{cdp_endpoint(debug_port)}/json/version", timeout=1) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def session_metadata_path(user_data_dir: Path) -> Path:
    """Return the ignored file that stores managed Chrome session metadata."""
    return user_data_dir / "session.json"


def save_chrome_session(user_data_dir: Path, debug_port: int) -> dict[str, Any]:
    """Persist the managed Chrome debug port for reconnecting after server restart."""
    user_data_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "debug_port": debug_port,
        "profile_path": str(user_data_dir),
        "created_at": int(time.time()),
    }
    with session_metadata_path(user_data_dir).open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)
        file.write("\n")
    return payload


def load_chrome_session(user_data_dir: Path) -> Optional[dict[str, Any]]:
    """Load persisted managed Chrome session metadata, if valid enough to use."""
    path = session_metadata_path(user_data_dir)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        debug_port = int(payload.get("debug_port"))
    except (TypeError, ValueError):
        return None
    profile_path = clean_text(payload.get("profile_path"))
    if profile_path and Path(profile_path) != user_data_dir:
        return None
    payload["debug_port"] = debug_port
    return payload


def chrome_process_uses_profile(debug_port: int, user_data_dir: Path) -> bool:
    """Whether a local Chrome process for debug_port uses this profile directory."""
    profile = str(user_data_dir)
    port_arg = f"--remote-debugging-port={debug_port}"
    try:
        output = subprocess.check_output(["ps", "ax", "-o", "command="], text=True)
    except (OSError, subprocess.SubprocessError):
        return False
    return any(port_arg in line and f"--user-data-dir={profile}" in line for line in output.splitlines())


def profile_lock_files_exist(user_data_dir: Path) -> bool:
    """Whether Chrome profile lock files are present for the managed profile."""
    return any((user_data_dir / name).exists() for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"))


def recover_chrome_session(user_data_dir: Path, preferred_port: int = 9222) -> Optional[dict[str, Any]]:
    """Recover a live managed Chrome session when session.json is missing/stale."""
    session = load_chrome_session(user_data_dir)
    if session and chrome_debug_ready(int(session["debug_port"])):
        return session
    if chrome_debug_ready(preferred_port) and chrome_process_uses_profile(preferred_port, user_data_dir):
        session = save_chrome_session(user_data_dir, preferred_port)
        session["recovered"] = True
        return session
    return None


def launch_user_chrome(
    user_data_dir: Path,
    debug_port: int,
    start_url: Optional[str] = None,
    chrome_path: Optional[str] = None,
    ready_timeout_s: float = 20.0,
) -> "subprocess.Popen":
    """Launch the user's real Chrome as a local process with remote debugging."""
    chrome = chrome_path or find_chrome_executable()
    if not chrome:
        raise RuntimeError(
            "Google Chrome was not found. Install Chrome to use the autofill "
            "login flow."
        )
    user_data_dir.mkdir(parents=True, exist_ok=True)
    args = [
        chrome,
        f"--remote-debugging-port={debug_port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-debugging-address=127.0.0.1",
        start_url or INDEED_LOGIN_URL,
    ]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    deadline = time.time() + ready_timeout_s
    while time.time() < deadline:
        if chrome_debug_ready(debug_port):
            return proc
        if proc.poll() is not None:
            raise RuntimeError(
                "Chrome exited before the debugging port became ready. Close the JobFind "
                "Chrome windows and click Open Indeed Login again."
            )
        time.sleep(0.3)
    raise RuntimeError(
        "Timed out waiting for Chrome's remote-debugging port. Close the JobFind "
        "Chrome windows and click Open Indeed Login again."
    )


def connect_user_chrome(pw: Any, debug_port: int) -> tuple[Any, Any]:
    """Attach Playwright to a running real Chrome over CDP."""
    browser = pw.chromium.connect_over_cdp(cdp_endpoint(debug_port))
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    return browser, context


def open_browser_context(pw: Any, headless: bool, user_data_dir: Optional[Path] = None) -> tuple[Any, Optional[Any]]:
    """Open a persistent or temporary context."""
    channel = "chrome" if find_chrome_executable() else None
    if user_data_dir:
        user_data_dir.mkdir(parents=True, exist_ok=True)
        context = pw.chromium.launch_persistent_context(
            str(user_data_dir),
            headless=headless,
            channel=channel,
        )
        return context, None

    browser = pw.chromium.launch(headless=headless, channel=channel)
    return browser.new_context(), browser


def attach_browser_report(
    report: dict[str, Any],
    pw: Any,
    context: Any,
    browser: Optional[Any],
    page: Any,
    user_data_dir: Optional[Path] = None,
) -> None:
    """Attach live Playwright handles to a report for review/cleanup."""
    report["_playwright"] = pw
    report["_browser"] = browser
    report["_context"] = context
    report["_page"] = page
    if user_data_dir:
        report["persistent_profile"] = str(user_data_dir)


def close_report_browser(report: dict[str, Any]) -> None:
    """Close Playwright handles attached to a report, ignoring stale handles."""
    for key in ("_context", "_browser"):
        handle = report.get(key)
        if handle is None:
            continue
        try:
            handle.close()
        except Exception:
            pass
    playwright = report.get("_playwright")
    if playwright is not None:
        try:
            playwright.stop()
        except Exception:
            pass
    proc = report.get("_chrome_proc")
    if proc is not None:
        try:
            proc.terminate()
        except Exception:
            pass


def start_playwright() -> Any:
    """Import and start Playwright with a helpful install message."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is not installed. Run: make install") from exc
    return sync_playwright().start()


def open_indeed_login(user_data_dir: Path, login_url: str = INDEED_LOGIN_URL) -> dict[str, Any]:
    """Launch the user's real Chrome at the Indeed login page for manual login."""
    report: dict[str, Any] = {
        "url": login_url,
        "stages": ["opened_login_page"],
        "filled": [],
        "skipped": [],
        "needs_review": [],
        "review_items": [],
        "current_step_needs_review": [],
        "current_step_review_items": [],
        "submitted": False,
        "status_reason": "Log into Indeed in the Chrome window, then return to JobFind and click Resume Autofill.",
    }
    debug_port = free_debug_port()
    proc = launch_user_chrome(user_data_dir, debug_port, start_url=login_url)
    save_chrome_session(user_data_dir, debug_port)
    report["_chrome_proc"] = proc
    report["_debug_port"] = debug_port
    report["_user_data_dir"] = str(user_data_dir)
    report["debug_port"] = debug_port
    return report
