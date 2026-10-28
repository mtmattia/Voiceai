"""Polling sulla cartella inbox: robusto con Syncthing/NAS, niente inotify su mount di rete."""
import logging
import shutil
import time
import traceback
from pathlib import Path

log = logging.getLogger(__name__)

TEMP_MARKERS = (".tmp", ".part", ".crdownload", ".partial")


def candidates(inbox, exts):
    exts = {e.lower() for e in exts}
    for p in sorted(Path(inbox).iterdir()):
        n = p.name
        if not p.is_file() or n.startswith((".", "~")) or n.lower().endswith(TEMP_MARKERS):
            continue
        if p.suffix.lower() in exts:
            yield p


class Stability:
    """Un file è pronto quando size e mtime non cambiano da stable_seconds."""

    def __init__(self, stable_seconds, clock=time.time):
        self.stable, self.clock, self.seen = stable_seconds, clock, {}

    def ready(self, p):
        st = p.stat()
        sig, now = (st.st_size, st.st_mtime), self.clock()
        prev = self.seen.get(p)
        if not prev or prev[0] != sig:
            self.seen[p] = (sig, now)
            return False
        return st.st_size > 0 and now - prev[1] >= self.stable

    def forget(self, p):
        self.seen.pop(p, None)


def _move(p, dest_dir):
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / p.name
    i = 1
    while dest.exists():
        dest = dest_dir / f"{p.stem}_{i}{p.suffix}"
        i += 1
    shutil.move(str(p), dest)
    return dest


def run(cfg, handle, once=False):
    inbox, archive = cfg["paths"]["inbox"], cfg["paths"]["archive"]
    inbox.mkdir(parents=True, exist_ok=True)
    w = cfg["watch"]
    stab = Stability(0 if once else w["stable_seconds"])
    log.info("in ascolto su %s", inbox)
    while True:
        for p in list(candidates(inbox, w["extensions"])):
            if not (once or stab.ready(p)):
                continue
            stab.forget(p)
            log.info("elaboro %s", p.name)
            try:
                out = handle(p)
                _move(p, archive)
                log.info("fatto: %s", out)
            except Exception:
                failed = _move(p, inbox / "failed")
                failed.with_suffix(failed.suffix + ".log").write_text(traceback.format_exc())
                log.exception("errore su %s -> %s", p.name, failed)
        if once:
            return
        time.sleep(w["poll_seconds"])
