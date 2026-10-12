#!/usr/bin/env python3
"""Monthly source monitor for the SafeBite allergen database.

It does NOT change allergens.json. It only fetches the official pages listed in
sources.json, compares a hash of their visible text with the previous run
(state.json) and writes report.md when something changed or could not be read.
A person then reviews the page and edits allergens.json by hand.

Usage: python3 monitor/check_sources.py [--sources FILE] [--state FILE] [--report FILE]
Exit code is always 0; the workflow reads report.md to decide whether to open an issue.
"""
import argparse
import hashlib
import json
import re
import urllib.request
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

UA = "SafeBiteSourceMonitor/1.0 (+https://github.com/gunn23dev-rgb/SafeBite_data)"


def visible_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|svg|header|footer|nav)\b.*?</\1>", " ", html)
    html = re.sub(r"(?s)<!--.*?-->", " ", html)
    text = unescape(re.sub(r"(?s)<[^>]+>", " ", html))
    return re.sub(r"\s+", " ", text).strip().lower()


def fetch(url: str, headers: dict | None = None) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html", **(headers or {})})
    with urllib.request.urlopen(req, timeout=40) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        return resp.read(5_000_000).decode("utf-8", errors="replace")


def read_page(url: str, headers: dict | None, fallback: str | None) -> str:
    """Visible text of `url`; if that fails, of `fallback`. Raises with both errors."""
    errors = []
    for candidate, extra in ((url, headers), (fallback, None)):
        if not candidate:
            continue
        try:
            text = visible_text(fetch(candidate, extra))
            if len(text) < 500:
                raise RuntimeError("page text too short (blocked or empty page?)")
            return text
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{candidate}: {exc}")
    raise RuntimeError(" | ".join(errors))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default="monitor/sources.json")
    ap.add_argument("--state", default="monitor/state.json")
    ap.add_argument("--report", default="monitor/report.md")
    args = ap.parse_args()

    sources = json.loads(Path(args.sources).read_text(encoding="utf-8"))
    state_path = Path(args.state)
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    changed, failed, baseline = [], [], []
    for src in sources:
        url, name = src["url"], src["name"]
        entry = state.get(url, {})
        try:
            text = read_page(url, src.get("headers"), src.get("fallback"))
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        except Exception as exc:  # noqa: BLE001 - any failure is reported, never fatal
            failed.append((name, url, str(exc)))
            entry["lastError"] = f"{now}: {exc}"
            state[url] = entry
            continue
        if "hash" not in entry:
            baseline.append(name)
        elif entry["hash"] != digest:
            changed.append((name, url, entry.get("checkedAt", "?")))
        entry.update({"name": name, "hash": digest, "checkedAt": now})
        entry.pop("lastError", None)
        state[url] = entry

    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = Path(args.report)
    if not changed and not failed:
        report.unlink(missing_ok=True)
        print(f"No changes. First-time baselines recorded: {baseline or 'none'}")
        return
    lines = [f"Monthly source check, {now}.", ""]
    if changed:
        lines += ["## Pages whose text changed", ""]
        lines += [f"- [{n}]({u}) (last compared: {d})" for n, u, d in changed]
        lines += ["", "A change can also be a harmless site update. Open each page, look for new or removed",
                  "allergens/rules, and if needed edit `allergens.json` (raise `version`, set `updatedAt`).", ""]
    if failed:
        lines += ["## Pages that could not be read", ""]
        lines += [f"- [{n}]({u}): {e}" for n, u, e in failed]
        lines += ["", "Some official sites block automated requests. Check these pages by hand this month.", ""]
    report.write_text("\n".join(lines), encoding="utf-8")
    print(report.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
