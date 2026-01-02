from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path


def setup_logging(level: str, log_dir: Path, max_log_files: int = 50) -> None:
    """
    Setup logging with a new log file for each run.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_dir: Directory to store log files
        max_log_files: Maximum number of log files to keep (default: 50)
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Create log file with timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = log_dir / f"app_{timestamp}.log"
    
    # Create symlink to latest log (for convenience)
    latest_link = log_dir / "app_latest.log"
    if latest_link.exists() or latest_link.is_symlink():
        latest_link.unlink()
    try:
        # Create relative symlink
        latest_link.symlink_to(log_file.name)
    except (OSError, NotImplementedError):
        # Symlinks might not work on some systems (Windows), skip silently
        pass
    
    # Cleanup old log files (keep only max_log_files most recent)
    _cleanup_old_logs(log_dir, max_log_files)
    
    root = logging.getLogger()
    root.setLevel(level.upper())

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.setLevel(level.upper())

    file_handler = logging.FileHandler(
        log_file,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(level.upper())

    # Avoid duplicate handlers in dev reloads
    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(file_handler)
    
    # Log the start of new session
    logging.info("=" * 80)
    logging.info(f"Bot started - Log file: {log_file.name}")
    logging.info("=" * 80)


def _cleanup_old_logs(log_dir: Path, max_files: int) -> None:
    """Remove old log files, keeping only the most recent max_files."""
    try:
        # Get all log files (exclude symlinks)
        log_files = [
            f for f in log_dir.glob("app_*.log")
            if f.is_file() and not f.is_symlink()
        ]
        
        # Sort by modification time (newest first)
        log_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        
        # Remove old files
        for old_file in log_files[max_files:]:
            try:
                old_file.unlink()
                logging.debug(f"Removed old log file: {old_file.name}")
            except OSError:
                pass
    except Exception:
        # Don't fail if cleanup fails
        pass


