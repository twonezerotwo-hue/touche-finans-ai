"""Excel degisikliklerini surecler ve is parcaciklari arasinda siraya alir.

Windows'ta ``msvcrt``, diger sistemlerde ``fcntl`` kullanir. Kilit ayni is
parcacigi icinde yeniden girilebilir (re-entrant): ``excel_deposu`` kilidi kendisi
alir, bu yuzden zaten ``@kilitli`` olan bir cagiran onu sorunsuz cagirabilir.
Baska bir surec kilidi tutuyorsa kisa bir sure beklenir; sure dolarsa anlasilir
bir hata verilir.
"""
import sys
import threading
import time
from contextlib import contextmanager
from functools import wraps

import config

_kilit = threading.RLock()
_durum = threading.local()

# Baska surecin kilidini bekleme suresi (sn). Testlerde kisaltilabilir.
BEKLEME_SURESI = 10.0
_DENEME_ARALIGI = 0.1


class KilitHatasi(RuntimeError):
    """Baska bir finans kaydi islenirken kilit alinamadi."""


def _dosya_kilitle(f):
    if sys.platform == "win32":  # pragma: no cover - Windows
        import msvcrt

        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _dosya_birak(f):
    if sys.platform == "win32":  # pragma: no cover - Windows
        import msvcrt

        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


@contextmanager
def yazma_kilidi():
    # Ayni is parcacigi kilidi zaten tutuyorsa dosya kilidine tekrar dokunma.
    if getattr(_durum, "derinlik", 0) > 0:
        _durum.derinlik += 1
        try:
            yield
        finally:
            _durum.derinlik -= 1
        return

    with _kilit:
        config.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        with (config.TEMP_DIR / "finans_yazma.lock").open("a+b") as f:
            f.seek(0, 2)
            if f.tell() == 0:
                f.write(b"0")
                f.flush()
            f.seek(0)
            bitis = time.monotonic() + max(0.0, float(BEKLEME_SURESI))
            while True:
                try:
                    _dosya_kilitle(f)
                    break
                except OSError as exc:
                    if time.monotonic() >= bitis:
                        raise KilitHatasi(
                            "Başka bir finans kaydı devam ediyor; birkaç saniye sonra tekrar deneyin."
                        ) from exc
                    time.sleep(_DENEME_ARALIGI)
            _durum.derinlik = 1
            try:
                yield
            finally:
                _durum.derinlik = 0
                try:
                    _dosya_birak(f)
                except OSError:  # pragma: no cover - kilit zaten dusmus olabilir
                    pass


def kilitli(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        with yazma_kilidi():
            return fn(*args, **kwargs)

    return wrapper
