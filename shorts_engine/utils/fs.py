"""
Leaf filesystem utility module for atomic writes and file replacements.
Contains ZERO imports from shorts_engine to guarantee no circular dependencies.
"""

import os
import json
import time
import logging
import tempfile
from pathlib import Path
from typing import Any, Union, Optional

logger = logging.getLogger(__name__)


def atomic_replace(src: Union[str, Path], dst: Union[str, Path], max_retries: int = 5) -> None:
    """
    Atomically replaces destination file with source file.
    On Windows, os.replace() can raise PermissionError if dst is briefly held open
    by an indexing service, antivirus, or reader process.
    Retries ONLY on PermissionError with exponential backoff.
    Immediately raises all other OSError exceptions.
    """
    src = Path(src)
    dst = Path(dst)

    backoff = 0.05
    for attempt in range(max_retries):
        try:
            os.replace(src, dst)
            return
        except PermissionError as e:
            if attempt == max_retries - 1:
                logger.error(
                    "Failed to replace %s -> %s after %d retries due to PermissionError: %s",
                    src,
                    dst,
                    max_retries,
                    e,
                )
                raise
            time.sleep(backoff)
            backoff = min(backoff * 2, 0.5)
        except OSError as e:
            logger.error("Fatal OSError during atomic replace %s -> %s: %s", src, dst, e)
            raise


def atomic_write_json(
    path: Union[str, Path],
    data: Any,
    indent: int = 2,
    encoding: str = "utf-8",
) -> None:
    """
    Atomically writes JSON-serializable data to the target path.
    Writes first to a temporary file in the same directory, flushes and fsyncs,
    then atomically replaces the destination.
    Cleans up the temporary file if an exception occurs.
    """
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_file: Optional[Path] = None
    try:
        # Create temp file in the same directory so os.replace is atomic across the same filesystem
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            dir=path.parent,
            prefix=f".{path.name}.tmp.",
            delete=False,
        ) as f:
            temp_file = Path(f.name)
            json.dump(data, f, indent=indent, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())

        atomic_replace(temp_file, path)
        temp_file = None  # Replaced successfully
    finally:
        if temp_file is not None and temp_file.exists():
            try:
                temp_file.unlink()
            except OSError as e:
                logger.warning("Failed to clean up temporary file %s: %s", temp_file, e)


def clean_temp_files(directory: Union[str, Path], max_age_sec: float = 3600) -> int:
    """
    Scans directory for orphaned temporary files (e.g. .*.tmp.* or *.tmp)
    and removes any older than max_age_sec.
    Returns the count of removed files.
    """
    dir_path = Path(directory).resolve()
    if not dir_path.exists() or not dir_path.is_dir():
        return 0

    removed = 0
    now = time.time()
    for item in dir_path.glob(".*.tmp.*"):
        if item.is_file():
            try:
                if (now - item.stat().st_mtime) >= max_age_sec:
                    item.unlink()
                    removed += 1
            except OSError as e:
                logger.warning("Could not clean temp file %s: %s", item, e)

    for item in dir_path.glob("*.tmp"):
        if item.is_file():
            try:
                if (now - item.stat().st_mtime) >= max_age_sec:
                    item.unlink()
                    removed += 1
            except OSError as e:
                logger.warning("Could not clean temp file %s: %s", item, e)

    return removed
