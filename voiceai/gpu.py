"""Lock GPU tra processi (server, watcher, CLI): una sola elaborazione alla volta sui 6 GB.

flock su data_dir/gpu.lock. Chi aspetta crea gpu.want.<pid>: il server, che tiene i modelli in VRAM
tra un blocco e l'altro, quando è a riposo e vede la richiesta scarica tutto e cede il lock.
"""
import fcntl
import logging
import os
from contextlib import contextmanager

log = logging.getLogger(__name__)


class GpuLock:
    def __init__(self, cfg):
        d = cfg["storage"]["data_dir"]
        d.mkdir(parents=True, exist_ok=True)
        self.dir, self.path = d, d / "gpu.lock"
        self.want = d / f"gpu.want.{os.getpid()}"
        self.fd = None

    @property
    def held(self):
        return self.fd is not None

    def acquire(self):
        if self.held:
            return
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.info("GPU occupata da un altro processo: aspetto")
            self.want.touch()
            fcntl.flock(fd, fcntl.LOCK_EX)
            self.want.unlink(missing_ok=True)
        self.fd = fd

    def release(self):
        if self.held:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None

    def wanted(self):
        """True se un altro processo vivo aspetta la GPU (i file di processi morti si ignorano)."""
        for f in self.dir.glob("gpu.want.*"):
            pid = f.suffix[1:]
            if pid.isdigit() and int(pid) != os.getpid() and os.path.exists(f"/proc/{pid}"):
                return True
        return False


@contextmanager
def exclusive(cfg):
    lock = GpuLock(cfg)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
