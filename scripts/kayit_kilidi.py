"""Excel degisikliklerini bot/CLI surecleri arasinda siraya alir."""
from contextlib import contextmanager
from functools import wraps
from threading import RLock
from config import TEMP_DIR

_kilit = RLock()

@contextmanager
def yazma_kilidi():
    with _kilit:
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        with (TEMP_DIR / 'finans_yazma.lock').open('a+b') as f:
            f.seek(0, 2)
            if f.tell() == 0:
                f.write(b'0')
                f.flush()
            f.seek(0)
            import msvcrt
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError('Başka bir finans kaydı devam ediyor; tekrar deneyin.') from exc
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)

def kilitli(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        with yazma_kilidi():
            return fn(*args, **kwargs)
    return wrapper
