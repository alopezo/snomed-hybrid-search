#!/usr/bin/env python3
"""
Download the latest SNOMED CT International Edition RF2 release from the SNOMED syndication
service (MLDS) and extract it, then point SNOMED_SNAPSHOT_DIR at the extracted Snapshot folder.

The Atom feed is public; downloading the ZIP requires MLDS credentials (SNOMED_USER / SNOMED_PASSWORD
in .env). You need an active SNOMED CT Affiliate/Member account with access to the International Edition.

Usage:
    python etl/syndication_downloader.py                 # latest International, into ./data
    python etl/syndication_downloader.py --dir /some/dir # custom download dir
    python etl/syndication_downloader.py --no-write-env  # don't touch .env, just print the path
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

import httpx
from dotenv import load_dotenv

FEED_URL_DEFAULT = "https://mlds.ihtsdotools.org/api/feed"
ATOM = {"atom": "http://www.w3.org/2005/Atom"}
NCTS = {"ncts": "http://ns.electronichealth.net.au/ncts/syndication/asf/extensions/1.0.0"}
ACCEPTABLE = {"SCT_RF2_SNAPSHOT", "SCT_RF2_FULL", "SCT_RF2_ALL"}


def find_latest_international(feed_url: str) -> tuple[str, str]:
    """Return (zip_url, title) for the newest International Edition RF2 package.
    Prefers the SNAPSHOT package (smaller) at the newest content version."""
    print(f"Fetching syndication feed (public): {feed_url}")
    resp = httpx.get(feed_url, timeout=60, follow_redirects=True)
    resp.raise_for_status()
    tree = ET.fromstring(resp.content)

    entries = []
    for entry in tree.findall("atom:entry", ATOM):
        title = (entry.findtext("atom:title", default="", namespaces=ATOM) or "")
        if "SNOMED CT International Edition" not in title:
            continue
        category = entry.find("atom:category", ATOM)
        term = category.attrib.get("term", "") if category is not None else ""
        if term not in ACCEPTABLE:
            continue
        version = entry.findtext("ncts:contentItemVersion", default="", namespaces=NCTS) or ""
        updated = entry.findtext("atom:updated", default="", namespaces=ATOM) or ""
        zip_url = None
        for link in entry.findall("atom:link", ATOM):
            if link.attrib.get("type") == "application/zip":
                zip_url = link.attrib.get("href")
                break
        if zip_url:
            entries.append({"title": title, "term": term, "zip_url": zip_url,
                            "version": version or updated})

    if not entries:
        sys.exit("ERROR: no International Edition RF2 package found in the feed "
                 "(check acceptable package types / your access).")

    # Newest version first; at equal version prefer the SNAPSHOT package.
    entries.sort(key=lambda e: (e["version"], e["term"] == "SCT_RF2_SNAPSHOT"), reverse=True)
    best = entries[0]
    print(f"✓ Latest: {best['title']}  [{best['term']}]  version={best['version']}")
    return best["zip_url"], best["title"]


def download_and_extract(zip_url: str, out_dir: str, user: str, password: str) -> str:
    """Download the ZIP (Basic auth) with a progress readout and extract it. Returns the
    extracted SnomedCT_* root folder path."""
    os.makedirs(out_dir, exist_ok=True)
    zip_path = os.path.join(out_dir, "snomed_release.zip")
    print(f"Downloading {os.path.basename(zip_url.split('?')[0])} → {zip_path}")

    with httpx.stream("GET", zip_url, auth=(user, password), timeout=None,
                      follow_redirects=True) as r:
        if r.status_code == 401:
            sys.exit("ERROR: 401 Unauthorized. Check SNOMED_USER/SNOMED_PASSWORD and that your "
                     "MLDS account can download the International Edition.")
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        with open(zip_path, "wb") as f:
            for chunk in r.iter_bytes(chunk_size=1 << 20):
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    print(f"\r  {done/1e6:6.1f} / {total/1e6:.1f} MB ({pct}%)", end="", flush=True)
    print()

    print("Extracting…")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(out_dir)
    os.remove(zip_path)

    roots = [os.path.join(out_dir, d) for d in os.listdir(out_dir)
             if os.path.isdir(os.path.join(out_dir, d)) and d.startswith("SnomedCT_")]
    if not roots:
        sys.exit("ERROR: no SnomedCT_* folder found after extraction.")
    return roots[0]


def update_env_snapshot_dir(snapshot_dir: str) -> None:
    """Set SNOMED_SNAPSHOT_DIR in .env (replacing the line if present)."""
    if not os.path.exists(".env"):
        print(f"\nSet this in your .env:\n  SNOMED_SNAPSHOT_DIR={snapshot_dir}")
        return
    with open(".env", encoding="utf-8") as f:
        text = f.read()
    line = f"SNOMED_SNAPSHOT_DIR={snapshot_dir}"
    if re.search(r"(?m)^SNOMED_SNAPSHOT_DIR=.*$", text):
        text = re.sub(r"(?m)^SNOMED_SNAPSHOT_DIR=.*$", line, text)
    else:
        text += ("\n" if not text.endswith("\n") else "") + line + "\n"
    with open(".env", "w", encoding="utf-8") as f:
        f.write(text)
    print(f"\n✓ Updated .env → SNOMED_SNAPSHOT_DIR={snapshot_dir}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.environ.get("SNOMED_DOWNLOAD_DIR", "data"),
                    help="download/extract directory (default: ./data)")
    ap.add_argument("--feed", default=os.environ.get("MLDS_FEED_URL", FEED_URL_DEFAULT))
    ap.add_argument("--no-write-env", action="store_true", help="print the path instead of editing .env")
    args = ap.parse_args()

    load_dotenv()
    user = os.environ.get("SNOMED_USER")
    password = os.environ.get("SNOMED_PASSWORD")
    if not user or not password:
        sys.exit("ERROR: set SNOMED_USER and SNOMED_PASSWORD in .env (your MLDS credentials). "
                 "The feed is public, but downloading the release requires them.")
    print(f"MLDS credentials found for: {user}")

    zip_url, _ = find_latest_international(args.feed)
    root = download_and_extract(zip_url, args.dir, user, password)
    snapshot = os.path.abspath(os.path.join(root, "Snapshot"))
    if not os.path.isdir(snapshot):
        sys.exit(f"ERROR: extracted release has no Snapshot folder at {snapshot}")

    print(f"\n✓ Release ready. Snapshot: {snapshot}")
    if args.no_write_env:
        print(f"\nSet this in your .env:\n  SNOMED_SNAPSHOT_DIR={snapshot}")
    else:
        update_env_snapshot_dir(snapshot)
    print("\nNext: make etl index-lexical embed index-hnsw")


if __name__ == "__main__":
    main()
