"""High-performance parallel scraper for Moroccan legal documents from Adala (Ministère de la Justice).

Target: https://adala.justice.gov.ma/resources/1
Features:
- Fast discovery or instant load from adala_manifest.json
- Explicitly skips Constitution (folder ID 568 / دساتير المملكة)
- Removes '#toolbar=0&statusbar=0' and fragments from file URLs
- Safely truncates filenames to <= 180 UTF-8 bytes to respect Linux filesystem limits
- Concurrent file downloads with ThreadPoolExecutor (20 workers)
- Resumable (skips existing valid files)
- Full exception isolation so one file error never halts the scrape
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

ADALA_BASE = "https://adala.justice.gov.ma"
OUTPUT_DIR = Path(__file__).resolve().parent / "adala"
MANIFEST_PATH = OUTPUT_DIR / "adala_manifest.json"

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}


def sanitize_filename(name: str, max_bytes: int = 180) -> str:
    """Sanitize string for filesystem paths, cleanly truncating by UTF-8 bytes."""
    clean = re.sub(r'[\\/*?:"<>|\r\n\t]+', "_", name.strip())
    clean = re.sub(r"\s+", " ", clean).strip()
    encoded = clean.encode("utf-8")
    if len(encoded) > max_bytes:
        clean = encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip()
    return clean or "document"


def fetch_folder_content(folder_id: int | str) -> dict:
    """Fetch and parse __NEXT_DATA__ for a folder."""
    url = f"{ADALA_BASE}/resources/{folder_id}"
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, context=CTX, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                match = re.search(
                    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                    html,
                    re.DOTALL,
                )
                if match:
                    data = json.loads(match.group(1))
                    return (
                        data.get("props", {})
                        .get("pageProps", {})
                        .get("content", {})
                    )
        except Exception:
            time.sleep(0.5)
    return {}


def build_download_url(raw_path: str) -> str:
    """Convert raw file path from Adala to full download URL."""
    # Strip URL fragments like #toolbar=0&statusbar=0
    clean = raw_path.split("#")[0].strip()
    if clean.startswith("/"):
        clean = clean[1:]
    if clean.startswith("http"):
        return clean
    encoded = urllib.parse.quote(clean, safe="/:?=&")
    return f"{ADALA_BASE}/api/{encoded}"


def discover_all_items(root_id: int = 1, max_workers: int = 12) -> tuple[list[dict], list[dict]]:
    """Parallel discovery of all subcategories and files."""
    print("🔍 Discovering folder hierarchy...", flush=True)
    root_content = fetch_folder_content(root_id)

    visited_folders: set[int] = set()
    folders_metadata: list[dict] = []
    all_files: list[dict] = []

    current_level: list[tuple[int, list[str]]] = []
    for f in root_content.get("folders", []):
        f_id = f.get("id")
        f_name = f.get("name", "").strip()
        if f_id == 568 or "دساتير المملكة" in f_name:
            print(f"⏭️  Skipping Constitution: [{f_id}] {f_name}", flush=True)
            continue
        current_level.append((f_id, [sanitize_filename(f_name)]))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while current_level:
            next_level: list[tuple[int, list[str]]] = []
            future_to_info = {
                executor.submit(fetch_folder_content, fid): (fid, path)
                for fid, path in current_level
                if fid not in visited_folders
            }

            for fid, _ in current_level:
                visited_folders.add(fid)

            for future in as_completed(future_to_info):
                fid, path_parts = future_to_info[future]
                content = future.result()
                subfolders = content.get("folders", [])
                files = content.get("files", [])

                folders_metadata.append({
                    "id": fid,
                    "name": content.get("name"),
                    "path": path_parts,
                    "subfolders_count": len(subfolders),
                    "files_count": len(files),
                })

                for sf in subfolders:
                    sf_id = sf.get("id")
                    sf_name = sf.get("name", "").strip()
                    if sf_id not in visited_folders:
                        next_level.append((sf_id, path_parts + [sanitize_filename(sf_name)]))

                for fi in files:
                    raw_path = fi.get("path") or fi.get("url")
                    if not raw_path:
                        continue
                    dl_url = build_download_url(raw_path)
                    file_name = fi.get("name") or fi.get("title") or Path(raw_path).stem
                    sanitized_name = sanitize_filename(file_name)

                    all_files.append({
                        "file_id": fi.get("id"),
                        "original_name": file_name,
                        "sanitized_name": sanitized_name,
                        "folder_path": path_parts,
                        "raw_path": raw_path,
                        "download_url": dl_url,
                        "extension": fi.get("extension") or "pdf",
                    })

            print(f"   • Scanned level: {len(current_level)} folders -> Total files so far: {len(all_files)}", flush=True)
            current_level = next_level

    return folders_metadata, all_files


def download_single_file(item: dict) -> dict:
    """Download and save a single file with retries and proper renaming."""
    try:
        folder_parts = [sanitize_filename(p) for p in item["folder_path"]]
        folder_dir = OUTPUT_DIR.joinpath(*folder_parts)
        folder_dir.mkdir(parents=True, exist_ok=True)

        clean_name = sanitize_filename(item.get("original_name") or item.get("sanitized_name") or "doc")
        filename = f"{clean_name}.pdf"
        target_path = folder_dir / filename

        # Resume check
        if target_path.exists() and target_path.stat().st_size > 1024:
            return {
                "file_id": item.get("file_id"),
                "status": "skipped",
                "path": str(target_path),
                "size": target_path.stat().st_size,
            }

        url = item.get("download_url") or build_download_url(item.get("raw_path", ""))
        req = urllib.request.Request(url, headers=HEADERS)

        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, context=CTX, timeout=25) as resp:
                    data = resp.read()
                    if len(data) < 50:
                        continue
                    with open(target_path, "wb") as f:
                        f.write(data)
                    return {
                        "file_id": item.get("file_id"),
                        "status": "downloaded",
                        "path": str(target_path),
                        "size": len(data),
                    }
            except Exception as e:
                if attempt == 2:
                    return {
                        "file_id": item.get("file_id"),
                        "status": "failed",
                        "error": str(e),
                        "url": url,
                    }
                time.sleep(0.5)

        return {
            "file_id": item.get("file_id"),
            "status": "failed",
            "error": "Max retries exceeded",
        }
    except Exception as ex:
        return {
            "file_id": item.get("file_id"),
            "status": "failed",
            "error": str(ex),
        }


def run_scraper(max_workers: int = 20):
    """Main scraping routine."""
    print("=" * 70, flush=True)
    print("🚀 Starting Adala Justice Portal Scraper", flush=True)
    print(f"📁 Destination Directory: {OUTPUT_DIR}", flush=True)
    print("=" * 70, flush=True)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Load from manifest or discover
    if MANIFEST_PATH.exists():
        print(f"📦 Loading cached discovery from: {MANIFEST_PATH}", flush=True)
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
        files = manifest_data.get("files", [])
        folders = manifest_data.get("folders", [])
        print(f"   • Loaded {len(files)} files across {len(folders)} categories", flush=True)
    else:
        t0 = time.time()
        folders, files = discover_all_items(root_id=1, max_workers=12)
        disc_time = time.time() - t0
        print(f"\n✅ Discovery complete in {disc_time:.1f}s:", flush=True)
        print(f"   • Total categories/subcategories: {len(folders)}", flush=True)
        print(f"   • Total PDF documents to download: {len(files)}", flush=True)

        manifest_data = {
            "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "root_url": f"{ADALA_BASE}/resources/1",
            "total_folders": len(folders),
            "total_files": len(files),
            "folders": folders,
            "files": files,
        }
        with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, ensure_ascii=False, indent=2)
        print(f"📄 Manifest saved to: {MANIFEST_PATH}", flush=True)

    # 2. Download files concurrently
    print(f"\n⬇️  Starting concurrent download with {max_workers} workers...", flush=True)
    start_time = time.time()
    results = []
    completed = 0
    total = len(files)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(download_single_file, fi): fi for fi in files}
        for future in as_completed(futures):
            res = future.result()
            results.append(res)
            completed += 1
            if completed % 100 == 0 or completed == total:
                elapsed = time.time() - start_time
                rate = completed / elapsed if elapsed > 0 else 0
                eta = (total - completed) / rate if rate > 0 else 0
                print(
                    f"[{completed}/{total}] "
                    f"({completed/total*100:.1f}%) — "
                    f"{rate:.1f} files/s — "
                    f"ETA: {int(eta//60)}m{int(eta%60)}s",
                    flush=True,
                )

    # Summary
    downloaded = sum(1 for r in results if r.get("status") == "downloaded")
    skipped = sum(1 for r in results if r.get("status") == "skipped")
    failed = sum(1 for r in results if r.get("status") == "failed")
    total_size_mb = (
        sum(r.get("size", 0) for r in results if "size" in r) / (1024 * 1024)
    )

    print("\n" + "=" * 70, flush=True)
    print("🎉 Scrape and Download Completed!", flush=True)
    print(f"   • Successfully downloaded: {downloaded}", flush=True)
    print(f"   • Already present (skipped): {skipped}", flush=True)
    print(f"   • Failed: {failed}", flush=True)
    print(f"   • Total size: {total_size_mb:.2f} MB", flush=True)
    print(f"   • Storage location: {OUTPUT_DIR}", flush=True)
    print("=" * 70, flush=True)


if __name__ == "__main__":
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    run_scraper(max_workers=workers)
