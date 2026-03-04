"""
Fetch Toast POS CSV data from Azure Blob Storage with local caching.

Usage:
    python tools/fetch_toast_data.py                    # Yesterday's data
    python tools/fetch_toast_data.py 20260215            # Specific date
    python tools/fetch_toast_data.py 20260210 20260215   # Date range
    python tools/fetch_toast_data.py --force 20260215    # Force refresh (bypass cache)

Cache strategy:
    - Historical dates (>1 day old): cached forever (Toast data doesn't change)
    - Today's date: always fetched fresh (data may still be uploading)
    - Cache location: .tmp/<date>/<filename>.csv
"""

import os
import sys
from io import StringIO
from datetime import datetime, timedelta
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
CONTAINER_NAME = os.getenv("AZURE_STORAGE_CONTAINER", "livitefiles")

DAILY_FILES = [
    "AllItemsReport.csv",
    "CashEntries.csv",
    "CheckDetails.csv",
    "ItemSelectionDetails.csv",
    "KitchenTimings.csv",
    "ModifiersSelectionDetails.csv",
    "OrderDetails.csv",
    "PaymentDetails.csv",
    "TimeEntries.csv",
]

CACHE_DIR = os.path.join(os.path.dirname(__file__), '..', '.tmp')

# Set to True to bypass cache for all fetches
FORCE_REFRESH = False

blob_service = BlobServiceClient.from_connection_string(CONNECTION_STRING)
container_client = blob_service.get_container_client(CONTAINER_NAME)


def _is_today(date_str: str) -> bool:
    """Check if date_str is today's date."""
    return date_str == datetime.now().strftime("%Y%m%d")


def _cache_path(date_str: str, filename: str) -> str:
    """Return the local cache file path for a given date/file."""
    return os.path.join(CACHE_DIR, date_str, filename)


def _read_from_cache(date_str: str, filename: str) -> Optional[pd.DataFrame]:
    """Try to read a CSV from local cache. Returns None on miss."""
    path = _cache_path(date_str, filename)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return pd.read_csv(path)
    return None


def _write_to_cache(date_str: str, filename: str, df: pd.DataFrame):
    """Save a DataFrame to local cache."""
    path = _cache_path(date_str, filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False)


def get_toast_csv(date_str: str, filename: str) -> pd.DataFrame:
    """Pull a single CSV from Azure Blob Storage (no caching)."""
    blob_path = f"{date_str}/{filename}"
    blob_client = container_client.get_blob_client(blob_path)
    data = blob_client.download_blob().readall().decode('utf-8')
    return pd.read_csv(StringIO(data))


def get_toast_csv_cached(date_str: str, filename: str) -> pd.DataFrame:
    """
    Pull a single CSV, using local cache when possible.

    - Today's date: always fetch fresh (data may still be uploading)
    - Historical dates: use cache if available, fetch + cache on miss
    - FORCE_REFRESH=True: always fetch fresh and update cache
    """
    use_cache = not FORCE_REFRESH and not _is_today(date_str)

    if use_cache:
        cached = _read_from_cache(date_str, filename)
        if cached is not None:
            return cached

    # Cache miss or forced refresh — fetch from Azure
    df = get_toast_csv(date_str, filename)

    # Always write to cache (even for today — next run will re-check freshness)
    _write_to_cache(date_str, filename, df)

    return df


def get_daily_data(date: datetime = None, quiet: bool = False) -> dict:
    """
    Pull all 9 Toast CSVs for a given date, using cache.
    Defaults to yesterday (most recent complete day).
    Returns dict of {name_without_extension: DataFrame}.
    Fetches all 9 files in parallel for faster cold loads.
    """
    if date is None:
        date = datetime.now() - timedelta(days=1)

    date_str = date.strftime("%Y%m%d")
    data = {}
    cached_count = 0

    def _fetch_one(f):
        key = f.replace('.csv', '')
        is_cached = (
            not FORCE_REFRESH
            and not _is_today(date_str)
            and os.path.exists(_cache_path(date_str, f))
            and os.path.getsize(_cache_path(date_str, f)) > 0
        )
        df = get_toast_csv_cached(date_str, f)
        return key, f, df, is_cached

    with ThreadPoolExecutor(max_workers=9) as pool:
        futures = {pool.submit(_fetch_one, f): f for f in DAILY_FILES}
        for future in as_completed(futures):
            try:
                key, fname, df, is_cached = future.result()
                data[key] = df
                if is_cached:
                    cached_count += 1
                if not quiet:
                    tag = "cached" if is_cached else f"{len(df)} rows"
                    print(f"  + {fname} ({tag})")
            except Exception as e:
                fname = futures[future]
                if not quiet:
                    print(f"  x {fname}: {e}")

    if not quiet and cached_count > 0:
        print(f"  ({cached_count}/{len(DAILY_FILES)} from cache)")

    return data


def get_multi_day_data(start_date: datetime, end_date: datetime,
                       quiet: bool = False) -> dict:
    """
    Fetch all 9 CSVs for each day in a date range, using cache.
    Returns dict keyed by date_str → {name: DataFrame}.
    """
    all_days = {}
    current = start_date
    while current <= end_date:
        date_str = current.strftime("%Y%m%d")
        if not quiet:
            print(f"  Fetching {date_str}...")
        day_data = get_daily_data(current, quiet=True)
        if day_data:
            all_days[date_str] = day_data
            if not quiet:
                cached = sum(
                    1 for f in DAILY_FILES
                    if os.path.exists(_cache_path(date_str, f))
                )
                fetched = len(day_data)
                print(f"    {fetched} files loaded")
        current += timedelta(days=1)

    if not quiet:
        print(f"  Total: {len(all_days)} day(s) loaded")

    return all_days


def save_daily_data(date_str: str, output_dir: str = None):
    """Download all 9 CSVs for a date and save to .tmp/<date>/."""
    if output_dir is None:
        output_dir = os.path.join(CACHE_DIR, date_str)

    os.makedirs(output_dir, exist_ok=True)
    dt = datetime.strptime(date_str, "%Y%m%d")
    data = get_daily_data(dt)

    for name, df in data.items():
        path = os.path.join(output_dir, f"{name}.csv")
        df.to_csv(path, index=False)
        print(f"  Saved {path}")

    return data


def list_available_dates() -> list:
    """List all YYYYMMDD date folders in the container."""
    blobs = container_client.list_blobs()
    dates = set()
    for blob in blobs:
        folder = blob.name.split('/')[0]
        if len(folder) == 8 and folder.isdigit():
            dates.add(folder)
    return sorted(dates)


def get_date_range(start: str, end: str, filename: str = "OrderDetails.csv") -> pd.DataFrame:
    """
    Pull a specific CSV across a date range, using cache.
    Returns concatenated DataFrame with _date column added.
    """
    start_dt = datetime.strptime(start, "%Y%m%d")
    end_dt = datetime.strptime(end, "%Y%m%d")

    frames = []
    current = start_dt
    while current <= end_dt:
        date_str = current.strftime("%Y%m%d")
        try:
            df = get_toast_csv_cached(date_str, filename)
            df['_date'] = date_str
            frames.append(df)
        except Exception:
            pass
        current += timedelta(days=1)

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


if __name__ == "__main__":
    args = sys.argv[1:]

    # Check for --force flag
    if "--force" in args:
        FORCE_REFRESH = True
        args = [a for a in args if a != "--force"]
        print("Force refresh enabled — bypassing cache.\n")

    if len(args) == 0:
        # Default: yesterday
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        print(f"Fetching data for {yesterday}...")
        save_daily_data(yesterday)

    elif len(args) == 1:
        # Single date
        print(f"Fetching data for {args[0]}...")
        save_daily_data(args[0])

    elif len(args) == 2:
        # Date range — save each day
        start_dt = datetime.strptime(args[0], "%Y%m%d")
        end_dt = datetime.strptime(args[1], "%Y%m%d")
        current = start_dt
        while current <= end_dt:
            ds = current.strftime("%Y%m%d")
            print(f"\nFetching data for {ds}...")
            save_daily_data(ds)
            current += timedelta(days=1)
    else:
        print("Usage: python tools/fetch_toast_data.py [--force] [YYYYMMDD] [YYYYMMDD]")
        sys.exit(1)
