import fcntl
import logging
import argparse
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from .rm_process import run_rm_process
from .utils import validate_output_path

log = logging.getLogger(__name__)


class Syncd:
    def __init__(self, sync_dir: Path, viewer_url: str = "http://127.0.0.1:5000"):
        self.sync_dir = sync_dir
        self.dirty = sync_dir / "xochitl-dirty"
        self.staging = sync_dir / "xochitl-staging"
        self.stable = sync_dir / "stable"
        self.xochitl = self.stable / "xochitl"
        self.process_out = self.stable / "process_out"
        self.syncflag = sync_dir / "syncflag"
        self.lock_file = sync_dir / "syncd.lock"
        self.viewer_url = viewer_url

        # In-memory state
        self.staging_full: bool = False
        self.processing: bool = False
        self.process_thread: threading.Thread | None = None
        self.process_error: bool = False

    def ensure_dirs(self):
        """Create the directory structure if it doesn't exist."""
        for d in (self.dirty, self.staging, self.stable, self.xochitl, self.process_out):
            d.mkdir(parents=True, exist_ok=True)
        log.info(f"directory structure ensured under {self.sync_dir}")

    def recover_state(self):
        """Infer state from filesystem on startup."""
        # processing always starts False (thread died with previous syncd)
        self.processing = False
        self.process_thread = None
        self.process_error = False

        if self.syncflag.exists():
            # syncflag exists → dirty has unconsumed data, staging not yet filled
            self.staging_full = False
            log.info("recover: syncflag present, staging_full=False")
        else:
            # syncflag absent → conservative: assume we staged but didn't promote yet
            # rsync is idempotent so redoing promote is safe
            self.staging_full = True
            log.info("recover: syncflag absent, staging_full=True (conservative)")

    def _rsync(self, src: Path, dst: Path):
        """Run rsync --delete from src to dst. Raises on failure."""
        cmd = [
            "rsync", "-a", "--delete",
            str(src) + "/",
            str(dst) + "/",
        ]
        log.info(f"rsync: {src} → {dst}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            log.error(f"rsync failed (exit {result.returncode}): {result.stderr}")
            raise RuntimeError(f"rsync failed: {result.stderr}")

    def _run_process_thread(self):
        """Target for the processing thread."""
        try:
            log.info("rm_process starting")
            run_rm_process(self.xochitl, self.process_out)
            log.info("rm_process completed successfully")
        except Exception:
            log.exception("rm_process failed")
            self.process_error = True

    def _trigger_viewer_rebuild(self):
        """POST /api/rebuild to the viewer. Non-fatal on failure."""
        url = f"{self.viewer_url}/api/rebuild"
        try:
            req = urllib.request.Request(url, method="POST", data=b"")
            with urllib.request.urlopen(req, timeout=5) as resp:
                log.info(f"viewer rebuild: {resp.status}")
        except Exception as e:
            log.warning(f"viewer rebuild failed (non-fatal): {e}")

    def reconcile(self) -> bool:
        """Run one reconcile cycle. Returns True if any state changed."""
        changed = False

        # Phase 1: Reap — check if processing thread finished
        if self.processing and self.process_thread is not None:
            if not self.process_thread.is_alive():
                self.processing = False
                if self.process_error:
                    log.error("reap: rm_process failed, skipping viewer rebuild")
                    self.process_error = False
                else:
                    log.info("reap: rm_process done, triggering viewer rebuild")
                    self._trigger_viewer_rebuild()
                self.process_thread = None
                changed = True

        # Phase 2: Stage — if syncflag exists and staging not full
        if self.syncflag.exists() and not self.staging_full:
            log.info("stage: rsyncing dirty → staging")
            try:
                self._rsync(self.dirty, self.staging)
                self.syncflag.unlink()
                self.staging_full = True
                log.info("stage: done, syncflag removed")
                changed = True
            except RuntimeError:
                log.error("stage: rsync failed, will retry next cycle")

        # Phase 3: Promote+process — if staging full and not processing
        if self.staging_full and not self.processing:
            log.info("promote: rsyncing staging → xochitl")
            try:
                self._rsync(self.staging, self.xochitl)
                self.process_error = False
                self.process_thread = threading.Thread(
                    target=self._run_process_thread,
                    daemon=True,
                )
                self.process_thread.start()
                self.processing = True
                self.staging_full = False
                log.info("promote: done, rm_process thread started")
                changed = True
            except RuntimeError:
                log.error("promote: rsync failed, will retry next cycle")

        return changed

    def wait_any(self, timeout: float = 5.0, interval: float = 0.5):
        """Wait for syncflag creation or processing thread completion.

        Polls at `interval` seconds up to `timeout`.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.syncflag.exists():
                return
            if self.processing and self.process_thread and not self.process_thread.is_alive():
                return
            time.sleep(interval)

    def run(self):
        """Main reconcile loop."""
        self.ensure_dirs()
        self.recover_state()
        log.info("syncd running")

        while True:
            changed = self.reconcile()
            if not changed:
                self.wait_any(timeout=5.0)


def build_syncd_parser(parser: argparse._SubParsersAction):
    syncd_parser = parser.add_parser(
        "syncd",
        help="Run the sync daemon — reconciles device syncs into rm-viewer output",
    )
    syncd_parser.add_argument(
        "sync_dir",
        type=validate_output_path,
        help="Path to the sync directory (e.g. ~/Documents/Remarkable/rm-viewer-sync/sync)",
    )
    syncd_parser.add_argument(
        "--install", action="store_true",
        help="Create directory structure and print setup instructions, then exit",
    )
    syncd_parser.add_argument(
        "--viewer-url",
        default="http://127.0.0.1:5000",
        help="URL of the rm-viewer instance for rebuild notifications (default: http://127.0.0.1:5000)",
    )


def rm_syncd(args: argparse.Namespace):
    sync_dir = Path(args.sync_dir).resolve()

    if args.install:
        syncd = Syncd(sync_dir)
        syncd.ensure_dirs()
        sd = sync_dir

        # ANSI colours
        BOLD = "\033[1m"
        DIM = "\033[2m"
        CYAN = "\033[36m"
        GREEN = "\033[32m"
        YELLOW = "\033[33m"
        RESET = "\033[0m"

        print(f"""\
{GREEN}{BOLD}Sync directory created:{RESET} {sd}

  {CYAN}xochitl-dirty/{RESET}       {DIM}<- device rsyncs here{RESET}
  {CYAN}xochitl-staging/{RESET}     {DIM}<- staged snapshot{RESET}
  {CYAN}stable/{RESET}
    {CYAN}xochitl/{RESET}           {DIM}<- stable snapshot for processing{RESET}
    {CYAN}process_out/{RESET}       {DIM}<- viewer serves from here{RESET}

{BOLD}Device config{RESET} {DIM}(set in .rm-viewer/config.sh):{RESET}
  SYNC_DIR="{YELLOW}{sd}{RESET}"

{BOLD}Initial sync{RESET} {DIM}-- copy xochitl from tablet to the dirty dir (don't do this! follow the readme instead.):{RESET}
  {GREEN}scp -r root@<tablet-ip>:/home/root/.local/share/remarkable/xochitl/* {sd}/xochitl-dirty/{RESET}
  {GREEN}touch {sd}/syncflag{RESET}

{BOLD}Then start the viewer and syncd:{RESET}
  {GREEN}python -m rm_viewer view {sd}/stable/process_out{RESET}
  {GREEN}python -m rm_viewer syncd {sd}{RESET}

  {DIM}The viewer will auto-reload when syncd processes new data.{RESET}""")
        return

    viewer_url = args.viewer_url

    # Acquire flock for single-instance
    lock_file = sync_dir / "syncd.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = open(lock_file, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.error("syncd is already running (could not acquire lock)")
        raise SystemExit(1)

    try:
        syncd = Syncd(sync_dir, viewer_url=viewer_url)
        syncd.run()
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()
