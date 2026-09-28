# -*- coding: utf-8 -*-
"""Telegram/CLI belge dosyalarini Qwen'in okuyacagi gorsele donusturur."""

from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

from PIL import Image, ImageOps

from config import PDF_MAX_SAYFA, TEMP_DIR

GORSEL_UZANTILARI = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
BELGE_UZANTILARI = GORSEL_UZANTILARI | {".pdf"}


def model_gorseli_hazirla(dosya_yolu: str | Path):
    """Donus: (model_gorseli, gecici_dosyalar). Orijinal dosyaya dokunmaz."""
    dosya_yolu = Path(dosya_yolu)
    uzanti = dosya_yolu.suffix.lower()
    if uzanti in GORSEL_UZANTILARI:
        return dosya_yolu, []
    if uzanti != ".pdf":
        raise ValueError(f"Desteklenmeyen belge uzantisi: {uzanti}")
    return _pdf_contact_sheet(dosya_yolu)


def _pdf_contact_sheet(pdf_yolu: Path):
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        raise RuntimeError("PDF destegi icin pdftoppm bulunamadi")
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    kimlik = uuid.uuid4().hex
    onek = TEMP_DIR / f"pdf_{kimlik}"
    komut = [
        pdftoppm, "-f", "1", "-l", str(PDF_MAX_SAYFA),
        "-r", "160", "-jpeg", "-jpegopt", "quality=88",
        str(pdf_yolu), str(onek),
    ]
    sonuc = subprocess.run(komut, capture_output=True, text=True, timeout=120, check=False)
    if sonuc.returncode != 0:
        raise RuntimeError(f"PDF gorsele cevrilemedi: {sonuc.stderr.strip()[:300]}")
    sayfalar = sorted(TEMP_DIR.glob(f"pdf_{kimlik}-*.jpg"))
    if not sayfalar:
        raise RuntimeError("PDF icinden sayfa olusturulamadi")

    resimler = []
    try:
        for sayfa in sayfalar:
            with Image.open(sayfa) as img:
                resim = ImageOps.exif_transpose(img).convert("RGB")
                hedef_genislik = 1200
                if resim.width != hedef_genislik:
                    oran = hedef_genislik / resim.width
                    resim = resim.resize((hedef_genislik, int(resim.height * oran)), Image.LANCZOS)
                resimler.append(resim.copy())

        sutun = 2 if len(resimler) > 1 else 1
        satir = (len(resimler) + sutun - 1) // sutun
        hucre_w = max(img.width for img in resimler)
        hucre_h = max(img.height for img in resimler)
        birlesik = Image.new("RGB", (hucre_w * sutun, hucre_h * satir), "white")
        for i, img in enumerate(resimler):
            x = (i % sutun) * hucre_w
            y = (i // sutun) * hucre_h
            birlesik.paste(img, (x, y))
        cikti = TEMP_DIR / f"pdf_{kimlik}_contact.jpg"
        birlesik.save(cikti, "JPEG", quality=90, optimize=True)
        birlesik.close()
        return cikti, [*sayfalar, cikti]
    finally:
        for img in resimler:
            img.close()


def gecicileri_temizle(dosyalar):
    for dosya in dosyalar or []:
        try:
            yol = Path(dosya)
            if yol.exists():
                yol.unlink()
        except OSError:
            pass
