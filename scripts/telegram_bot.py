# -*- coding: utf-8 -*-
"""Telegram botu - firma sorarak."""

import logging
import json
import os
import sys
import uuid
import hmac
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

sys.path.insert(0, str(Path(__file__).parent))

from config import (
    GELEN_EVRAK, LOGLAR, FIRMALAR, TELEGRAM_MAX_DOSYA_MB, TELEGRAM_STATE,
    TELEGRAM_OWNER_FILE, TELEGRAM_PAIRING_CODE,
)
from firma_taniyici import tani as firma_tani
from dogrulayici import deterministik_dogrula, FAIL
from guvenlik import audit_yaz, finansal_fingerprint
from main import belge_dosyasi_hazirla, hazirlanan_belgeyi_yaz, belgeyi_reddet, metin_hazirla
from belge_dosyasi import BELGE_UZANTILARI
from excel_yazici import belge_kaydini_iptal_et
from telegram_sorgu import finans_ozeti, kayit_ara, kayit_formatla, son_kayitlar
from faaliyet_yonetimi import faaliyet_olustur, proje_olustur, cari_olustur, metin_kontrol
from faaliyet_sema import anahtar
from invoice2data_adapter import durum as invoice2data_durumu
from akilli_excel import excel_soru_cevapla, indeks_olustur
from excel_kalite import excel_kalite_kontrol
from recete_motoru import recete_calistir, recete_listele
from finans_cekirdegi import (
    acilis_bakiyesi_kaydet, banka_csv_aktar, belgeyi_ters_kaydet, butce_kaydet, butce_raporu,
    cari_bakiyeleri, donem_durum_degistir, kullanici_rolu, muhasebe_csv_export,
    mizan, mutabakat_ozeti, nakit_tahmini, onay_oyu_ekle, politika_ayarla, rol_ata,
    senkron_kuyrugunu_isle, sistem_kontrolu, veritabani_yedekle,
    veritabanini_hazirla, yonetim_ozeti,
)

def _telegram_token_oku():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token:
        return token
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as anahtar:
                token, _ = winreg.QueryValueEx(anahtar, "TELEGRAM_BOT_TOKEN")
                if token:
                    return token
        except OSError:
            pass
    raise RuntimeError("TELEGRAM_BOT_TOKEN ortam degiskeni tanimli degil")


BOT_TOKEN = _telegram_token_oku()

log_dosya = LOGLAR / "telegram_bot.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    force=True,
    handlers=[
        logging.FileHandler(log_dosya, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Kullanici bazli override (chat_id -> firma_key)
KULLANICI_FIRMA = {}

# Firma sorma bekleyen dosyalar (chat_id -> dosya yolu)
BEKLEYEN_DOSYALAR = {}

# Deterministik dogrulamadan gecmis, kullanici karari bekleyen belgeler.
BEKLEYEN_ONAYLAR = {}
DUZELTME_BEKLEYEN = {}
BEKLEYEN_METINLER = {}
IPTAL_BEKLEYEN = {}
YONETIM_AKIS = {}

_izin_ham = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "")
YETKILI_CHAT_IDS = {int(x.strip()) for x in _izin_ham.split(",") if x.strip().lstrip("-").isdigit()}


def _sahipleri_yukle():
    try:
        data = json.loads(Path(TELEGRAM_OWNER_FILE).read_text(encoding='utf-8'))
        YETKILI_CHAT_IDS.update(int(x) for x in data.get('chat_ids', []) if str(x).lstrip('-').isdigit())
    except FileNotFoundError:
        pass
    except Exception as exc:
        log.error('Telegram sahip dosyası okunamadı: %s', exc)


def _eslestirme_kodu_hazirla():
    if YETKILI_CHAT_IDS:
        return
    path = Path(TELEGRAM_PAIRING_CODE)
    if path.exists() and path.read_text(encoding='utf-8').strip().isdigit():
        return
    LOGLAR.mkdir(parents=True, exist_ok=True)
    code = f'{secrets.randbelow(100_000_000):08d}'
    temp = path.with_suffix('.tmp')
    temp.write_text(code, encoding='utf-8')
    os.replace(temp, path)


_sahipleri_yukle()

# Guven esigi — bunun altindaysa kullaniciya sorulacak
GUvEN_ESIGI = 60


def _json_uyumlu(deger):
    if isinstance(deger, Path):
        return str(deger)
    if isinstance(deger, dict):
        return {str(k): _json_uyumlu(v) for k, v in deger.items()}
    if isinstance(deger, (list, tuple)):
        return [_json_uyumlu(v) for v in deger]
    return deger


def _durum_kaydet():
    """Bot yeniden baslasa da onay kuyrugunu ve firma secimini korur."""
    try:
        LOGLAR.mkdir(parents=True, exist_ok=True)
        veri = {
            "kullanici_firma": {str(k): v for k, v in KULLANICI_FIRMA.items()},
            "bekleyen_dosyalar": {str(k): v for k, v in BEKLEYEN_DOSYALAR.items()},
            "bekleyen_onaylar": _json_uyumlu(BEKLEYEN_ONAYLAR),
            "duzeltme_bekleyen": {str(k): v for k, v in DUZELTME_BEKLEYEN.items()},
            "bekleyen_metinler": {str(k): v for k, v in BEKLEYEN_METINLER.items()},
            "iptal_bekleyen": _json_uyumlu(IPTAL_BEKLEYEN),
            "yonetim_akis": _json_uyumlu(YONETIM_AKIS),
        }
        gecici = Path(str(TELEGRAM_STATE) + ".tmp")
        gecici.write_text(json.dumps(veri, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(gecici, TELEGRAM_STATE)
    except Exception as exc:
        log.error("Telegram durumu kaydedilemedi: %s", exc)


def _durum_yukle():
    if not Path(TELEGRAM_STATE).exists():
        return
    try:
        veri = json.loads(Path(TELEGRAM_STATE).read_text(encoding="utf-8"))
        KULLANICI_FIRMA.update({int(k): v for k, v in veri.get("kullanici_firma", {}).items()})
        BEKLEYEN_DOSYALAR.update({int(k): v for k, v in veri.get("bekleyen_dosyalar", {}).items()})
        BEKLEYEN_ONAYLAR.update(veri.get("bekleyen_onaylar", {}))
        DUZELTME_BEKLEYEN.update({int(k): v for k, v in veri.get("duzeltme_bekleyen", {}).items()})
        BEKLEYEN_METINLER.update({int(k): v for k, v in veri.get("bekleyen_metinler", {}).items()})
        IPTAL_BEKLEYEN.update(veri.get("iptal_bekleyen", {}))
        YONETIM_AKIS.update({int(k): v for k,v in veri.get('yonetim_akis',{}).items()})
    except Exception as exc:
        log.error("Telegram durumu yuklenemedi: %s", exc)


def _yetkili(chat_id):
    return chat_id in YETKILI_CHAT_IDS


def _admin(chat_id):
    rol = kullanici_rolu(chat_id)
    return rol == "ADMIN" or (rol is None and chat_id in YETKILI_CHAT_IDS)


def _yazabilir(chat_id):
    rol = kullanici_rolu(chat_id)
    return rol in {"ADMIN", "FINANS"} or (rol is None and chat_id in YETKILI_CHAT_IDS)


def _onaylayabilir(chat_id):
    rol = kullanici_rolu(chat_id)
    return rol in {"ADMIN", "FINANS", "ONAYCI"} or (rol is None and chat_id in YETKILI_CHAT_IDS)


async def _yazma_kontrol(update):
    if _yazabilir(update.effective_chat.id):
        return True
    await update.message.reply_text("Bu işlem ADMIN veya FINANS rolü gerektirir.")
    return False


async def _yetki_kontrol(update):
    chat_id = update.effective_chat.id
    if _yetkili(chat_id):
        return True
    mesaj = ("Bu Telegram hesabının işlem yetkisi yok. Chat ID: " + str(chat_id) +
             "\nKurulum için bilgisayardaki telegram_eslestirme.txt kodunu /yetkilendir <kod> biçiminde gönderin.")
    if update.callback_query:
        await update.callback_query.answer(mesaj, show_alert=True)
    elif update.message:
        await update.message.reply_text(mesaj)
    audit_yaz("telegram_yetkisiz", chat_id=chat_id)
    return False


_durum_yukle()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mesaj = (
        "Touche Holding finans asistanı hazır.\n\n"
        "Fotoğraf, JPG/PNG veya PDF gönderebilir; ayrıca normal mesajla finans kaydı yazabilirsin. "
        "Her kayıt Excel'e geçmeden önce Onayla / Düzelt / Reddet ekranına gelir.\n\n"
        "Komutlar:\n"
        "/firmalar — Firma seçimi\n"
        "/yeni_faaliyet — Yeni faaliyet ve Excel aç\n"
        "/yeni_proje — Seçili faaliyete proje ekle\n"
        "/yeni_cari — Yeni faaliyete cari ekle\n"
        "/vazgec — Açılış işleminden vazgeç\n"
        "/firma <key> — Sabit firma modu\n"
        "/serbest — Otomatik firma tespiti\n"
        "/durum — Aktif mod ve bekleyen işler\n"
        "/son 5 — Son kayıtlar\n"
        "/ara <metin> — Cari, proje veya kayıt ara\n"
        "/ozet — Finans kayıt özeti\n"
        "/mali_durum — Çift taraflı defter özeti\n"
        "/excel_sor — Seçili Excel'e kaynaklı soru sor\n"
        "/excel_index — Akıllı Excel indeksini yenile\n"
        "/excel_kontrol — Dosya ve formül kontrolü\n"
        "/receteler — Tekrarlanabilir işleri göster\n"
        "/recete_calistir — Aylık finans paketini üret\n"
        "/mizan YYYY-AA — Aylık mizan\n"
        "/nakit_tahmin 13 — Vadeli nakit planı\n"
        "/borclar ve /alacaklar — Cari bakiyeler\n"
        "/acilis_bakiyesi — Faaliyet açılış bakiyesi\n"
        "/butce ve /butce_rapor — Bütçe yönetimi\n"
        "/mutabakat — Banka eşleşme özeti\n"
        "/muhasebe_export YYYY-AA — Muhasebe CSV\n"
        "/bekleyen — Onay bekleyen belgeler\n"
        "/iptal <belge_id> — Yazılmış belgeyi güvenle iptal et\n"
        "/kimlik — Telegram Chat ID\n\n"
        "/yetkilendir <kod> — Bu hesabı ilk yetkili hesap yap\n\n"
        "Metin örneği:\n"
        "Gürsar, 25.09.2026, Göktürk Okulu, Sebahattin Çakmak, "
        "15.000 TL usta ödemesi, taş işleri."
    )
    await update.message.reply_text(mesaj)


async def firmalar_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _yetki_kontrol(update):
        return
    chat_id = update.effective_chat.id
    aktif = KULLANICI_FIRMA.get(chat_id, "otomatik")
    mesaj = "Kayitli firmalar:\n\n"
    for key, firma in FIRMALAR.items():
        isaret = " (AKTIF)" if key == aktif else ""
        mesaj += f"• {firma['ad']}\n  /firma {key}{isaret}\n\n"
    mesaj += f"Su anki mod: {aktif}"
    await update.message.reply_text(mesaj[:3500], reply_markup=_firma_butonlari('sec'))


async def firma_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _yetki_kontrol(update):
        return
    chat_id = update.effective_chat.id
    args = context.args
    if not args:
        await update.message.reply_text("Kullanim: /firma <key>\nOrnek: /firma gursar_mimarlik")
        return

    firma_key = args[0].strip().lower()
    if firma_key not in FIRMALAR:
        await update.message.reply_text(f"Bilinmeyen firma: {firma_key}\n/firmalar yazarak listeyi gor.")
        return

    KULLANICI_FIRMA[chat_id] = firma_key
    _durum_kaydet()
    await update.message.reply_text(
        f"Tamam! Bundan sonraki belgeler {FIRMALAR[firma_key]['ad']} icin islenecek."
    )


async def serbest_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _yetki_kontrol(update):
        return
    chat_id = update.effective_chat.id
    if chat_id in KULLANICI_FIRMA:
        del KULLANICI_FIRMA[chat_id]
    _durum_kaydet()
    await update.message.reply_text("Otomatik firma tespiti aktif.")


async def durum_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _yetki_kontrol(update):
        return
    chat_id = update.effective_chat.id
    aktif = KULLANICI_FIRMA.get(chat_id, "otomatik")
    i2d = invoice2data_durumu()
    okuyucu = "hazır" if all((i2d["etkin"], i2d["kurulu"], i2d["pdf_okuyucu"], i2d["sablon_sayisi"])) else "Qwen yedeğinde"
    ek = f"\nFatura okuyucu: invoice2data {okuyucu} ({i2d['sablon_sayisi']} şablon)"
    if aktif == "otomatik":
        await update.message.reply_text(f"Mod: Otomatik firma tespiti\nBekleyen onay: {_bekleyen_sayisi(chat_id)}{ek}")
    else:
        await update.message.reply_text(f"Mod: {FIRMALAR[aktif]['ad']}\nBekleyen onay: {_bekleyen_sayisi(chat_id)}{ek}")


def _firma_butonlari(on_ek="firma"):
    """Firma secim butonlarini olusturur."""
    butonlar = []
    for key, firma in FIRMALAR.items():
        butonlar.append([InlineKeyboardButton(firma["ad"], callback_data=f"{on_ek}:{key}")])
    return InlineKeyboardMarkup(butonlar)


def _onay_butonlari(token):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Onayla", callback_data=f"onay:{token}"),
        InlineKeyboardButton("Düzelt", callback_data=f"duzelt:{token}"),
        InlineKeyboardButton("Reddet", callback_data=f"reddet:{token}"),
    ]])


def _bekleyen_sayisi(chat_id):
    return sum(1 for b in BEKLEYEN_ONAYLAR.values() if int(b.get("chat_id", 0)) == int(chat_id))


def _bekleyen_ekle(chat_id, hazir):
    token = uuid.uuid4().hex[:12]
    BEKLEYEN_ONAYLAR[token] = {"chat_id": chat_id, "hazir": hazir}
    _durum_kaydet()
    return token


def _belge_ozeti(hazir):
    dogrulama = hazir["dogrulama"]
    kayitlar = hazir["veri"].get("kayitlar", [])
    satirlar = [
        f"Firma: {FIRMALAR[hazir['firma_key']]['ad']}",
        f"Belge türü: {hazir['tur']}",
        f"Okuma motoru: {hazir.get('cikarim_motoru', 'qwen2.5vl')}",
        f"Doğrulama: {dogrulama['durum']} ({dogrulama['skor']}/100)",
        f"Kayıt sayısı: {len(kayitlar)}",
    ]
    for i, kayit in enumerate(kayitlar[:3], 1):
        satirlar.append(
            f"{i}. {kayit.get('Tarih') or '-'} | "
            f"{kayit.get('Cari') or kayit.get('Alici') or '-'} | "
            f"{kayit.get('Tutar') or kayit.get('Toplam') or '-'} {kayit.get('PB') or 'TL'}"
        )
    if dogrulama["uyarilar"]:
        satirlar.append("Uyarılar: " + "; ".join(dogrulama["uyarilar"][:3]))
    satirlar.append("Excel'e yazmadan önce seçiminizi yapın.")
    return "\n".join(satirlar)[:3500]


def _belge_toplami(hazir):
    toplam = 0.0
    for kayit in hazir.get("veri", {}).get("kayitlar", []):
        try:
            toplam += float(kayit.get("Tutar") or kayit.get("Toplam") or 0)
        except (TypeError, ValueError):
            pass
    return toplam


async def foto_al(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _yetki_kontrol(update):
        return
    if not await _yazma_kontrol(update):
        return
    chat_id = update.effective_chat.id
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)

    zaman = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dosya_adi = f"telegram_{zaman}.jpg"
    hedef = GELEN_EVRAK / dosya_adi

    await file.download_to_drive(hedef)
    log.info(f"Fotograf indirildi: {dosya_adi}")

    # Manuel firma secilmisse direkt isle
    manuel_firma = KULLANICI_FIRMA.get(chat_id)
    if manuel_firma:
        await update.message.reply_text(
            f"Fotograf alindi, {FIRMALAR[manuel_firma]['ad']} icin isleniyor..."
        )
        await _isle_ve_cevapla(update, context, hedef, manuel_firma)
        return

    # Otomatik tespit dene
    await update.message.reply_text("Fotograf alindi, firma tespit ediliyor...")
    firma_key, guven, kaynak = firma_tani(hedef)

    log.info(f"  Tespit: {firma_key} (%{guven}, kaynak: {kaynak})")

    # Guven yeterliyse direkt isle
    if guven >= GUvEN_ESIGI:
        await update.message.reply_text(
            f"Firma: {FIRMALAR[firma_key]['ad']} (%{guven})\nIsleniyor..."
        )
        await _isle_ve_cevapla(update, context, hedef, firma_key)
        return

    # Guven dusuk — kullaniciya sor
    BEKLEYEN_DOSYALAR[chat_id] = str(hedef)
    _durum_kaydet()
    await update.message.reply_text(
        f"Belgede firma adini net goremedim.\n"
        f"(On tahmin: {FIRMALAR[firma_key]['ad']}, guven: %{guven})\n\n"
        f"Bu belge hangi sirkete ait?",
        reply_markup=_firma_butonlari(),
    )


async def dosya_al(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Telegram belge eki olarak gelen PDF/JPG/PNG dosyalarini alir."""
    if not await _yetki_kontrol(update):
        return
    if not await _yazma_kontrol(update):
        return
    belge = update.message.document
    if belge.file_size and belge.file_size > TELEGRAM_MAX_DOSYA_MB * 1024 * 1024:
        await update.message.reply_text(f"Dosya çok büyük. Üst sınır: {TELEGRAM_MAX_DOSYA_MB} MB")
        return
    guvenli_ad = Path(belge.file_name or "belge").name
    uzanti = Path(guvenli_ad).suffix.lower()
    if uzanti not in BELGE_UZANTILARI | {".csv"}:
        await update.message.reply_text("Desteklenen dosyalar: PDF, JPG, JPEG, PNG, WEBP, BMP ve banka CSV.")
        return
    zaman = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    hedef = GELEN_EVRAK / f"telegram_{zaman}_{guvenli_ad}"
    tg_dosya = await context.bot.get_file(belge.file_id)
    await tg_dosya.download_to_drive(hedef)
    chat_id = update.effective_chat.id
    if uzanti == ".csv":
        try:
            adet = banka_csv_aktar(hedef)
            ozet = mutabakat_ozeti()
            await update.message.reply_text(f"Banka ekstresi alındı. Yeni hareket: {adet}\nMutabakat: {ozet}")
        except Exception as exc:
            await update.message.reply_text(f"Banka CSV işlenemedi: {str(exc)[:500]}")
        return
    firma_key = KULLANICI_FIRMA.get(chat_id)
    if firma_key:
        await update.message.reply_text(f"Dosya alındı; {FIRMALAR[firma_key]['ad']} için hazırlanıyor...")
        await _isle_ve_cevapla(update, context, hedef, firma_key)
        return
    BEKLEYEN_DOSYALAR[chat_id] = str(hedef)
    _durum_kaydet()
    await update.message.reply_text("Dosya alındı. Hangi firmaya işlenecek?", reply_markup=_firma_butonlari())


def _metinden_firma(metin):
    normalized = anahtar(metin)
    eslesen = [key for key,firma in FIRMALAR.items()
               if firma.get('adapter') == 'standart_v1' and anahtar(firma['ad']) in normalized]
    if len(eslesen) == 1:
        return eslesen[0]
    if len(eslesen) > 1:
        return None
    sade = str(metin).casefold()
    if "gürsar" in sade or "gursar" in sade or "göktürk" in sade or "gokturk" in sade:
        return "gursar_mimarlik"
    if "touche" in sade or "holding" in sade:
        return "touche_holding"
    return None


async def _metin_hazirla_ve_cevapla(update, context, metin, firma_key):
    await update.message.reply_text(f"Metin {FIRMALAR[firma_key]['ad']} için analiz ediliyor...")
    hazir = metin_hazirla(metin, firma_key)
    if not hazir.get("basari"):
        await update.message.reply_text(f"Metin kayda dönüştürülemedi.\n\n{hazir.get('mesaj', '')[:1000]}")
        return
    token = _bekleyen_ekle(update.effective_chat.id, hazir)
    await update.message.reply_text(_belge_ozeti(hazir), reply_markup=_onay_butonlari(token))


async def _isle_ve_cevapla(update, context, hedef, firma_key):
    """Belgeyi hazirlar; Excel yazimini kullanici onayina birakir."""
    try:
        hazir = belge_dosyasi_hazirla(hedef, manuel_firma=firma_key)
        if not hazir.get("basari"):
            if hazir.get("foto_yolu"):
                belgeyi_reddet(hazir, "dogrulama_hatasi")
            mesaj = hazir.get("mesaj", "Belge islenemedi.")
            await update.message.reply_text(f"Belge işlenemedi.\n\n{mesaj[:1000]}")
            return

        token = _bekleyen_ekle(update.effective_chat.id, hazir)
        await update.message.reply_text(_belge_ozeti(hazir), reply_markup=_onay_butonlari(token))
    except Exception as e:
        log.error(f"Isleme hatasi: {e}")
        await update.message.reply_text(f"Hata: {str(e)[:200]}")


async def buton_cevabi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Firma secimi ile Onayla / Duzelt / Reddet akislarini yonetir."""
    query = update.callback_query
    chat_id = query.message.chat_id
    if not _yetkili(chat_id):
        await query.answer("Bu Telegram hesabının işlem yetkisi yok.", show_alert=True)
        return
    await query.answer()
    data = query.data
    try:
        if data.startswith('sec:'):
            key = data.split(':',1)[1]
            if key not in FIRMALAR:
                raise ValueError('Faaliyet bulunamadı.')
            KULLANICI_FIRMA[chat_id] = key
            _durum_kaydet()
            await query.edit_message_text(f"Seçildi: {FIRMALAR[key]['ad']}. Belge veya finans işlemi gönderebilirsiniz.")
            return
        if data.startswith('kurulum:'):
            if not _yazabilir(chat_id):
                await query.edit_message_text("Bu işlem ADMIN veya FINANS rolü gerektirir.")
                return
            token = data.split(':',1)[1]
            akis = YONETIM_AKIS.get(chat_id)
            if not akis or akis.get('token') != token or akis.get('adim') != 'onay':
                await query.edit_message_text('Bu açılış isteği artık geçerli değil.')
                return
            try:
                if akis['tur'] == 'faaliyet':
                    key = faaliyet_olustur(**akis['veri'])
                    KULLANICI_FIRMA[chat_id] = key
                    sonuc = f"{FIRMALAR[key]['ad']} açıldı. Faaliyet Excel’i ve Holding tanımları kaydedildi.\nExcel: {FIRMALAR[key]['excel']}\nBu faaliyet seçili; işlem gönderebilirsiniz."
                elif akis['tur'] == 'cari':
                    cari_olustur(akis['firma_key'], **akis['veri'])
                    sonuc = 'Cari, faaliyet ve Holding tanımlarına eklendi.'
                else:
                    proje_olustur(akis['firma_key'], akis['veri']['proje'])
                    sonuc = 'Proje, faaliyet ve Holding tanımlarına eklendi.'
            except Exception as exc:
                await context.bot.send_message(chat_id=chat_id, text=f'Açılış tamamlanamadı: {str(exc)[:700]}\nExcel dosyaları açıksa kapatıp tekrar deneyin.', reply_markup=_kurulum_butonu(token))
                return
            YONETIM_AKIS.pop(chat_id,None)
            _durum_kaydet()
            await query.edit_message_text(sonuc)
            return
        if data.startswith("firma:"):
            if not _yazabilir(chat_id):
                await query.edit_message_text("Belge işleme yetkiniz yok.")
                return
            firma_key = data.split(":", 1)[1]
            hedef_str = BEKLEYEN_DOSYALAR.pop(chat_id, None)
            _durum_kaydet()
            if not hedef_str:
                await query.edit_message_text("Bekleyen belge bulunamadı. Lütfen tekrar fotoğraf gönderin.")
                return
            hedef = Path(hedef_str)
            if not hedef.exists():
                await query.edit_message_text("Belge artık mevcut değil. Lütfen tekrar gönderin.")
                return
            await query.edit_message_text(f"Firma: {FIRMALAR[firma_key]['ad']}\nİşleniyor... (30-90 sn)")
            hazir = belge_dosyasi_hazirla(hedef, manuel_firma=firma_key)
            if not hazir.get("basari"):
                if hazir.get("foto_yolu"):
                    belgeyi_reddet(hazir, "dogrulama_hatasi")
                await context.bot.send_message(chat_id=chat_id, text=f"Belge işlenemedi.\n\n{hazir.get('mesaj', '')[:1000]}")
                return
            token = _bekleyen_ekle(chat_id, hazir)
            await context.bot.send_message(chat_id=chat_id, text=_belge_ozeti(hazir), reply_markup=_onay_butonlari(token))
            return

        if data.startswith("metinfirma:"):
            if not _yazabilir(chat_id):
                await query.edit_message_text("Kayıt oluşturma yetkiniz yok.")
                return
            firma_key = data.split(":", 1)[1]
            metin = BEKLEYEN_METINLER.pop(chat_id, None)
            _durum_kaydet()
            if not metin:
                await query.edit_message_text("Bekleyen metin bulunamadı; işlemi yeniden yazın.")
                return
            await query.edit_message_text(f"Firma: {FIRMALAR[firma_key]['ad']}\nMetin analiz ediliyor...")
            hazir = metin_hazirla(metin, firma_key)
            if not hazir.get("basari"):
                await context.bot.send_message(chat_id=chat_id, text=f"Metin işlenemedi.\n\n{hazir.get('mesaj', '')[:1000]}")
                return
            token = _bekleyen_ekle(chat_id, hazir)
            await context.bot.send_message(chat_id=chat_id, text=_belge_ozeti(hazir), reply_markup=_onay_butonlari(token))
            return

        if data.startswith("iptalonay:") or data.startswith("iptalvazgec:"):
            if not _yazabilir(chat_id):
                await query.edit_message_text("Kayıt iptal yetkiniz yok.")
                return
            eylem, token = data.split(":", 1)
            istek = IPTAL_BEKLEYEN.get(token)
            if not istek or int(istek["chat_id"]) != int(chat_id):
                await query.edit_message_text("İptal isteği artık geçerli değil.")
                return
            if eylem == "iptalvazgec":
                IPTAL_BEKLEYEN.pop(token, None)
                _durum_kaydet()
                await query.edit_message_text("İptal işleminden vazgeçildi.")
                return
            await query.edit_message_text("Kayıtlar güvenli şekilde iptal ediliyor...")
            sonuc = belge_kaydini_iptal_et(istek["belge_id"])
            if sonuc["basari"]:
                try:
                    belgeyi_ters_kaydet(istek["belge_id"], f"telegram:{chat_id}")
                except Exception as exc:
                    await context.bot.send_message(chat_id=chat_id, text=f"Excel iptal edildi; ters muhasebe kaydı uyarısı: {str(exc)[:400]}")
                IPTAL_BEKLEYEN.pop(token, None)
                _durum_kaydet()
                await context.bot.send_message(chat_id=chat_id, text=f"İptal tamamlandı. Etkilenen satır: {sonuc['etkilenen']}")
            else:
                await context.bot.send_message(chat_id=chat_id, text=f"İptal başarısız: {sonuc['hata'][:500]}")
            return

        eylem, token = data.split(":", 1)
        bekleyen = BEKLEYEN_ONAYLAR.get(token)
        if not bekleyen:
            await query.edit_message_text("Bu onay kaydı artık geçerli değil. Belgeyi tekrar gönderin.")
            return
        hazir = bekleyen["hazir"]

        if not _onaylayabilir(chat_id):
            await query.edit_message_text("Bu belge için onay yetkiniz yok.")
            return

        if eylem == "onay":
            oy = onay_oyu_ekle(hazir["belge_id"], chat_id, _belge_toplami(hazir))
            if not oy["tamam"]:
                await query.edit_message_text(f"Onay kaydedildi: {oy['mevcut']}/{oy['gerekli']}. İkinci yetkili bekleniyor.")
                for diger in YETKILI_CHAT_IDS:
                    if int(diger) != int(chat_id):
                        await context.bot.send_message(chat_id=diger, text=_belge_ozeti(hazir), reply_markup=_onay_butonlari(token))
                return
            await query.edit_message_text("Onay alındı; güvenli Excel kaydı yapılıyor...")
            basarili, mesaj = hazirlanan_belgeyi_yaz(hazir, onay_kaynagi=f"telegram:{chat_id}")
            if basarili:
                BEKLEYEN_ONAYLAR.pop(token, None)
                _durum_kaydet()
                await context.bot.send_message(chat_id=chat_id, text=f"Başarılı! Belge {FIRMALAR[hazir['firma_key']]['ad']} sistemine kaydedildi.")
            else:
                await context.bot.send_message(chat_id=chat_id, text=f"Excel kaydı başarısız.\n\n{mesaj[:1000]}")
                await context.bot.send_message(chat_id=chat_id, text=_belge_ozeti(hazir), reply_markup=_onay_butonlari(token))
            return

        if eylem == "reddet":
            belgeyi_reddet(hazir)
            BEKLEYEN_ONAYLAR.pop(token, None)
            DUZELTME_BEKLEYEN.pop(chat_id, None)
            _durum_kaydet()
            await query.edit_message_text("Belge reddedildi; Excel'e yazılmadı ve hata arşivine taşındı.")
            return

        if eylem == "duzelt":
            DUZELTME_BEKLEYEN[chat_id] = token
            _durum_kaydet()
            await query.edit_message_text(
                "Düzeltilecek alanları gönderin. JSON nesnesi veya her satırda Alan=Değer kullanabilirsiniz.\n"
                "Örnek:\nTutar=12500,50\nCari=Örnek Firma\nProje=Göktürk Okulu"
            )
    except Exception as e:
        log.exception("Buton isleme hatasi")
        await context.bot.send_message(chat_id=chat_id, text=f"Hata: {str(e)[:200]}")


def _duzeltme_parse(metin):
    metin = metin.strip()
    if metin.startswith("{"):
        veri = json.loads(metin)
        if not isinstance(veri, dict):
            raise ValueError("JSON nesnesi bekleniyor")
        return veri
    sonuc = {}
    for satir in metin.splitlines():
        if not satir.strip():
            continue
        if "=" not in satir:
            raise ValueError(f"Alan=Değer biçimi bekleniyor: {satir}")
        alan, deger = satir.split("=", 1)
        sonuc[alan.strip()] = deger.strip()
    if not sonuc:
        raise ValueError("Düzeltme alanı bulunamadı")
    return sonuc


async def duzeltme_metni_al(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _yetki_kontrol(update):
        return
    chat_id = update.effective_chat.id
    if not _yazabilir(chat_id):
        await update.message.reply_text("Kayıt oluşturma/düzeltme yetkiniz yok.")
        return
    if chat_id in YONETIM_AKIS:
        await _yonetim_metni(update, context)
        return
    token = DUZELTME_BEKLEYEN.get(chat_id)
    if not token:
        metin = update.message.text.strip()
        firma_key = KULLANICI_FIRMA.get(chat_id) or _metinden_firma(metin)
        if not firma_key:
            BEKLEYEN_METINLER[chat_id] = metin
            _durum_kaydet()
            await update.message.reply_text("Bu işlemi hangi firmaya kaydedelim?", reply_markup=_firma_butonlari("metinfirma"))
            return
        await _metin_hazirla_ve_cevapla(update, context, metin, firma_key)
        return
    bekleyen = BEKLEYEN_ONAYLAR.get(token)
    if not bekleyen:
        DUZELTME_BEKLEYEN.pop(chat_id, None)
        _durum_kaydet()
        await update.message.reply_text("Düzeltme oturumu sona ermiş. Belgeyi tekrar gönderin.")
        return
    try:
        duzeltmeler = _duzeltme_parse(update.message.text)
        hazir = bekleyen["hazir"]
        kayitlar = hazir["veri"].get("kayitlar", [])
        if not kayitlar:
            raise ValueError("Düzeltilecek kayıt yok")
        kayitlar[0].update(duzeltmeler)
        dogrulama = deterministik_dogrula(hazir["veri"], hazir["firma_key"], hazir["tur"])
        hazir["dogrulama"] = dogrulama
        if dogrulama["durum"] == FAIL:
            await update.message.reply_text("Düzeltme sonrası doğrulama FAIL:\n" + "\n".join(dogrulama["hatalar"][:5]))
            return
        for kayit in hazir["veri"]["kayitlar"]:
            kayit["_fingerprint"] = finansal_fingerprint(kayit, hazir["firma_key"], hazir["tur"])
            kayit["_dogrulama_durumu"] = dogrulama["durum"]
        DUZELTME_BEKLEYEN.pop(chat_id, None)
        _durum_kaydet()
        audit_yaz("telegram_duzeltme", belge_id=hazir["belge_id"], alanlar=list(duzeltmeler.keys()), durum=dogrulama["durum"])
        await update.message.reply_text(_belge_ozeti(hazir), reply_markup=_onay_butonlari(token))
    except Exception as e:
        await update.message.reply_text(f"Düzeltme okunamadı: {str(e)[:300]}")


async def kimlik_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Telegram Chat ID: {update.effective_chat.id}")


async def yetkilendir_komutu(update, context):
    chat_id = update.effective_chat.id
    if chat_id in YETKILI_CHAT_IDS:
        await update.message.reply_text('Bu hesap zaten yetkili.')
        return
    if YETKILI_CHAT_IDS:
        await update.message.reply_text('İlk yetkili hesap zaten tanımlı. Yeni hesap yerel ayardan eklenebilir.')
        audit_yaz('telegram_yetkilendirme_reddi', chat_id=chat_id, sebep='sahip_mevcut')
        return
    girilen = ''.join(context.args).strip()
    try:
        beklenen = Path(TELEGRAM_PAIRING_CODE).read_text(encoding='utf-8').strip()
    except FileNotFoundError:
        await update.message.reply_text('Eşleştirme kodu bulunamadı. Botu yeniden başlatın.')
        return
    if not girilen or not hmac.compare_digest(girilen, beklenen):
        await update.message.reply_text('Eşleştirme kodu hatalı.')
        audit_yaz('telegram_yetkilendirme_reddi', chat_id=chat_id, sebep='kod_hatasi')
        return
    LOGLAR.mkdir(parents=True, exist_ok=True)
    temp = Path(str(TELEGRAM_OWNER_FILE) + '.tmp')
    temp.write_text(json.dumps({'chat_ids':[chat_id]}, ensure_ascii=False), encoding='utf-8')
    os.replace(temp, TELEGRAM_OWNER_FILE)
    YETKILI_CHAT_IDS.add(chat_id)
    rol_ata(chat_id, "ADMIN", "ilk_eslestirme")
    Path(TELEGRAM_PAIRING_CODE).unlink(missing_ok=True)
    audit_yaz('telegram_yetkilendirildi', chat_id=chat_id)
    await update.message.reply_text('Yetkilendirme tamamlandı. Artık tüm finans komutlarını kullanabilirsiniz.')


def _kurulum_butonu(token):
    return InlineKeyboardMarkup([[InlineKeyboardButton('Onayla ve oluştur', callback_data='kurulum:'+token)]])

async def yeni_faaliyet_komutu(update, context):
    if not await _yetki_kontrol(update):
        return
    if not await _yazma_kontrol(update):
        return
    chat_id = update.effective_chat.id
    if chat_id in DUZELTME_BEKLEYEN:
        await update.message.reply_text('Önce bekleyen belge düzeltmesini tamamlayın.')
        return
    YONETIM_AKIS[chat_id] = {'tur':'faaliyet','adim':'ad','veri':{},'token':uuid.uuid4().hex[:12]}
    _durum_kaydet()
    await update.message.reply_text('Yeni faaliyetin adı nedir? Örnek: Touche Enerji\nVazgeçmek için /vazgec')

async def yeni_proje_komutu(update, context):
    if not await _yetki_kontrol(update):
        return
    if not await _yazma_kontrol(update):
        return
    chat_id = update.effective_chat.id
    key = KULLANICI_FIRMA.get(chat_id)
    if not key or key == 'touche_holding':
        await update.message.reply_text('Önce /firmalar ile projenin bağlı olduğu faaliyeti seçin.')
        return
    YONETIM_AKIS[chat_id] = {'tur':'proje','adim':'proje','firma_key':key,'veri':{},'token':uuid.uuid4().hex[:12]}
    _durum_kaydet()
    await update.message.reply_text(f"{FIRMALAR[key]['ad']} için yeni proje adını yazın. Vazgeçmek için /vazgec")

async def vazgec_komutu(update, context):
    if not await _yetki_kontrol(update):
        return
    YONETIM_AKIS.pop(update.effective_chat.id,None)
    _durum_kaydet()
    await update.message.reply_text('Açılış akışı kapatıldı.')

async def yeni_cari_komutu(update, context):
    if not await _yetki_kontrol(update):
        return
    if not await _yazma_kontrol(update):
        return
    chat_id = update.effective_chat.id
    key = KULLANICI_FIRMA.get(chat_id)
    if FIRMALAR.get(key,{}).get('adapter') != 'standart_v1':
        await update.message.reply_text('Önce /firmalar ile standart şablonla açılan bir faaliyeti seçin.')
        return
    YONETIM_AKIS[chat_id] = {'tur':'cari','adim':'cari','firma_key':key,'veri':{},'token':uuid.uuid4().hex[:12]}
    _durum_kaydet()
    await update.message.reply_text('Cari adını yazın. Vazgeçmek için /vazgec')

async def _yonetim_metni(update, context):
    akis = YONETIM_AKIS[update.effective_chat.id]
    adim = akis['adim']
    text = update.message.text.strip()
    if adim == 'onay':
        await update.message.reply_text('Açılış özeti onay bekliyor. Değiştirmek için /vazgec ardından komutu yeniden başlatın.', reply_markup=_kurulum_butonu(akis['token']))
        return
    try:
        if adim == 'pb':
            text = text.upper()
            if text not in ('TL','USD','EUR'):
                raise ValueError('TL, USD veya EUR yazın.')
        elif adim == 'holding_payi':
            import math
            text = float(text.replace('%','').replace(',','.'))
            if not math.isfinite(text) or not 0 < text <= 100:
                raise ValueError('Pay 0’dan büyük, en fazla 100 olmalı.')
        else:
            text = metin_kontrol(text)
        akis['veri'][adim] = text
        if akis['tur'] == 'cari':
            if adim == 'cari':
                from master_data import master_data_oku
                next_step, question = 'cari_tipi', 'Cari tipini yazın: '+', '.join(master_data_oku(akis['firma_key'])['cari_tipler'])
            else:
                next_step, question = 'onay', f"Cari: {akis['veri']['cari']}\nTip: {text}\nFaaliyet: {FIRMALAR[akis['firma_key']]['ad']}"
        elif akis['tur'] == 'proje':
            next_step, question = 'onay', f"Proje: {text}\nFaaliyet: {FIRMALAR[akis['firma_key']]['ad']}"
        else:
            steps = {'ad':('proje','İlk projenin adı nedir?'),
                     'proje':('pb','Ana hesabın para birimi nedir? TL / USD / EUR'),
                     'pb':('hesap','Banka/kasa hesabının adı nedir? Örnek: Enerji TL Banka'),
                     'hesap':('holding_payi','Touche Holding’in bu faaliyetteki payını yüzde olarak yazın. Örnek: 100'),
                     'holding_payi':('dis_ortak','Dış ortağın adı nedir?'),
                     'dis_ortak':('onay','')}
            next_step, question = steps[adim]
            if adim == 'holding_payi' and text == 100:
                next_step = 'onay'
            if next_step == 'onay':
                v = akis['veri']
                question = f"Faaliyet: {v['ad']}\nProje: {v['proje']}\nHesap: {v['hesap']} ({v['pb']})\nHolding payı: %{v['holding_payi']}\nDış ortak: {v.get('dis_ortak') or 'Yok'}\n\nAyrı faaliyet Excel’i açılıp Holding tanımlarına bağlanacak."
        akis['adim'] = next_step
        _durum_kaydet()
        await update.message.reply_text(question, reply_markup=_kurulum_butonu(akis['token']) if next_step == 'onay' else None)
    except ValueError as exc:
        await update.message.reply_text(str(exc))


def _aktif_firma(chat_id):
    return KULLANICI_FIRMA.get(chat_id)


async def son_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _yetki_kontrol(update):
        return
    firma_key = _aktif_firma(update.effective_chat.id)
    if not firma_key:
        await update.message.reply_text("Önce /firma gursar_mimarlik veya /firma touche_holding seçin.")
        return
    try:
        adet = int(context.args[0]) if context.args else 5
        kayitlar = son_kayitlar(firma_key, adet)
        if not kayitlar:
            await update.message.reply_text("Kayıt bulunamadı.")
            return
        metin = "Son kayıtlar:\n\n" + "\n".join(kayit_formatla(firma_key, k) for k in kayitlar)
        await update.message.reply_text(metin[:4000])
    except Exception as exc:
        await update.message.reply_text(f"Kayıtlar okunamadı: {str(exc)[:300]}")


async def ara_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _yetki_kontrol(update):
        return
    firma_key = _aktif_firma(update.effective_chat.id)
    if not firma_key:
        await update.message.reply_text("Önce /firma ile sorgulanacak firmayı seçin.")
        return
    arama = " ".join(context.args).strip()
    if not arama:
        await update.message.reply_text("Kullanım: /ara <cari, proje, kayıt veya belge ID>")
        return
    try:
        kayitlar = kayit_ara(firma_key, arama)
        metin = "Arama sonucu:\n\n" + ("\n".join(kayit_formatla(firma_key, k) for k in kayitlar) or "Kayıt bulunamadı.")
        await update.message.reply_text(metin[:4000])
    except Exception as exc:
        await update.message.reply_text(f"Arama yapılamadı: {str(exc)[:300]}")


async def ozet_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _yetki_kontrol(update):
        return
    firma_key = _aktif_firma(update.effective_chat.id)
    if not firma_key:
        await update.message.reply_text("Önce /firma ile özet alınacak firmayı seçin.")
        return
    try:
        ozet = finans_ozeti(firma_key)
        await update.message.reply_text(
            f"{FIRMALAR[firma_key]['ad']} özeti\n"
            f"Toplam kayıt: {ozet['kayit_sayisi']}\n"
            f"Aktif kayıt: {ozet['aktif_kayit']}\n"
            f"Kayıt tutar toplamı: {ozet['toplam_tutar']:,.2f} TL"
        )
    except Exception as exc:
        await update.message.reply_text(f"Özet alınamadı: {str(exc)[:300]}")


async def bekleyen_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _yetki_kontrol(update):
        return
    chat_id = update.effective_chat.id
    satirlar = []
    for token, bekleyen in BEKLEYEN_ONAYLAR.items():
        if int(bekleyen.get("chat_id", 0)) != int(chat_id):
            continue
        hazir = bekleyen["hazir"]
        satirlar.append(f"{hazir.get('belge_id')} | {FIRMALAR[hazir['firma_key']]['ad']} | {hazir['dogrulama']['durum']} | kod: {token}")
    await update.message.reply_text("Onay bekleyenler:\n\n" + ("\n".join(satirlar) or "Bekleyen kayıt yok."))


async def iptal_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _yetki_kontrol(update):
        return
    if not context.args:
        await update.message.reply_text("Kullanım: /iptal <belge_id>\nBelge ID'yi /son veya /ara ile bulabilirsiniz.")
        return
    belge_id = context.args[0].strip()
    token = uuid.uuid4().hex[:12]
    IPTAL_BEKLEYEN[token] = {"chat_id": update.effective_chat.id, "belge_id": belge_id}
    _durum_kaydet()
    butonlar = InlineKeyboardMarkup([[
        InlineKeyboardButton("İptali Onayla", callback_data=f"iptalonay:{token}"),
        InlineKeyboardButton("Vazgeç", callback_data=f"iptalvazgec:{token}"),
    ]])
    await update.message.reply_text(
        f"{belge_id} belgesine bağlı kayıtlar silinmeden İptal/Reddedildi durumuna getirilecek. Onaylıyor musunuz?",
        reply_markup=butonlar,
    )


def _ay_parse(metin):
    parca = str(metin).split("-")
    if len(parca) != 2:
        raise ValueError("Dönem YYYY-AA biçiminde olmalı.")
    return int(parca[0]), int(parca[1])


async def mali_durum_komutu(update, context):
    if not await _yetki_kontrol(update): return
    faaliyet = _aktif_firma(update.effective_chat.id)
    o = yonetim_ozeti(None if faaliyet == "touche_holding" else faaliyet)
    await update.message.reply_text(
        f"Mali durum\nGelir: {o['gelir']:,.2f} TL\nGider: {o['gider']:,.2f} TL\n"
        f"Net sonuç: {o['net']:,.2f} TL\nNakit/banka: {o['nakit']:,.2f} TL\n"
        f"Alacak: {o['alacak']:,.2f} TL\nBorç: {o['borc']:,.2f} TL")


async def mizan_komutu(update, context):
    if not await _yetki_kontrol(update): return
    try:
        yil, ay = _ay_parse(context.args[0])
        bas = date(yil, ay, 1)
        son = date(yil + (ay == 12), 1 if ay == 12 else ay + 1, 1) - timedelta(days=1)
        faaliyet = _aktif_firma(update.effective_chat.id)
        rows = mizan(bas, son, None if faaliyet == "touche_holding" else faaliyet)
        metin = f"Mizan {yil:04d}-{ay:02d}\n\n" + ("\n".join(
            f"{r['hesap']} {r['ad']}: B {r['borc']:,.2f} / A {r['alacak']:,.2f} / Net {r['bakiye']:,.2f}"
            for r in rows[:40]) or "Kayıt yok.")
        await update.message.reply_text(metin[:4000])
    except Exception as exc:
        await update.message.reply_text(f"Kullanım: /mizan YYYY-AA\n{str(exc)[:400]}")


async def nakit_tahmin_komutu(update, context):
    if not await _yetki_kontrol(update): return
    try:
        hafta = int(context.args[0]) if context.args else 13
        faaliyet = _aktif_firma(update.effective_chat.id)
        rows = nakit_tahmini(hafta, None if faaliyet == "touche_holding" else faaliyet)
        metin = f"{hafta} haftalık vadeli nakit tahmini\n\n" + ("\n".join(
            f"{r['hafta']}: giriş {r['giris']:,.2f}, çıkış {r['cikis']:,.2f}, net {r['net']:,.2f} TL"
            for r in rows[:30]) or "Vadesi girilmiş gelecek kayıt yok.")
        await update.message.reply_text(metin[:4000])
    except Exception as exc:
        await update.message.reply_text(f"Kullanım: /nakit_tahmin [hafta]\n{str(exc)[:400]}")


async def acilis_bakiyesi_komutu(update, context):
    if not await _yetki_kontrol(update): return
    if not await _yazma_kontrol(update): return
    faaliyet = _aktif_firma(update.effective_chat.id)
    if not faaliyet or faaliyet == "touche_holding":
        await update.message.reply_text("Önce /firmalar ile ilgili faaliyeti seçin."); return
    try:
        hesap, tarih, tutar = context.args[:3]
        fis_id = acilis_bakiyesi_kaydet(faaliyet, hesap, tarih, tutar, update.effective_chat.id)
        await update.message.reply_text(f"Açılış bakiyesi kaydedildi: {fis_id}")
    except Exception as exc:
        await update.message.reply_text(f"Kullanım: /acilis_bakiyesi <hesap> <GG.AA.YYYY> <tutar>\n{str(exc)[:400]}")


async def cari_liste_komutu(update, context, hesap, baslik):
    if not await _yetki_kontrol(update): return
    rows = cari_bakiyeleri(hesap)
    metin = baslik + "\n\n" + ("\n".join(f"{r['cari']}: {r['tutar']:,.2f} TL" for r in rows[:30]) or "Kayıt yok.")
    await update.message.reply_text(metin[:4000])


async def borclar_komutu(update, context):
    await cari_liste_komutu(update, context, "320", "Tedarikçi borçları")


async def alacaklar_komutu(update, context):
    await cari_liste_komutu(update, context, "120", "Müşteri alacakları")


async def donem_kapat_komutu(update, context):
    if not await _yetki_kontrol(update): return
    if not _admin(update.effective_chat.id):
        await update.message.reply_text("Bu işlem ADMIN yetkisi gerektirir."); return
    try:
        yil, ay = _ay_parse(context.args[0])
        durum = donem_durum_degistir(yil, ay, True, update.effective_chat.id)
        await update.message.reply_text(f"{yil:04d}-{ay:02d} dönemi {durum}.")
    except Exception as exc:
        await update.message.reply_text(f"Kullanım: /donem_kapat YYYY-AA\n{str(exc)[:400]}")


async def donem_ac_komutu(update, context):
    if not await _yetki_kontrol(update): return
    if not _admin(update.effective_chat.id):
        await update.message.reply_text("Bu işlem ADMIN yetkisi gerektirir."); return
    try:
        yil, ay = _ay_parse(context.args[0])
        durum = donem_durum_degistir(yil, ay, False, update.effective_chat.id)
        await update.message.reply_text(f"{yil:04d}-{ay:02d} dönemi {durum}.")
    except Exception as exc:
        await update.message.reply_text(f"Kullanım: /donem_ac YYYY-AA\n{str(exc)[:400]}")


async def butce_komutu(update, context):
    if not await _yetki_kontrol(update): return
    if not await _yazma_kontrol(update): return
    faaliyet = _aktif_firma(update.effective_chat.id)
    if not faaliyet:
        await update.message.reply_text("Önce /firma ile faaliyet seçin."); return
    try:
        hesap, donem, tutar = context.args[:3]
        proje = " ".join(context.args[3:])
        yil, ay = _ay_parse(donem)
        butce_kaydet(faaliyet, proje, hesap, yil, ay, tutar, update.effective_chat.id)
        await update.message.reply_text("Bütçe kaydedildi.")
    except Exception as exc:
        await update.message.reply_text(f"Kullanım: /butce <hesap> <YYYY-AA> <tutar> [proje]\n{str(exc)[:400]}")


async def butce_rapor_komutu(update, context):
    if not await _yetki_kontrol(update): return
    faaliyet = _aktif_firma(update.effective_chat.id)
    try:
        yil, ay = _ay_parse(context.args[0])
        rows = butce_raporu(faaliyet, yil, ay)
        metin = f"Bütçe / gerçekleşen {yil:04d}-{ay:02d}\n\n" + ("\n".join(
            f"{r['hesap']} {r['proje'] or '-'}: {r['butce']:,.2f} / {r['gerceklesen']:,.2f} TL, fark {r['fark']:,.2f}"
            for r in rows[:30]) or "Bütçe kaydı yok.")
        await update.message.reply_text(metin[:4000])
    except Exception as exc:
        await update.message.reply_text(f"Kullanım: /butce_rapor YYYY-AA\n{str(exc)[:400]}")


async def mutabakat_komutu(update, context):
    if not await _yetki_kontrol(update): return
    o = mutabakat_ozeti()
    await update.message.reply_text("Banka mutabakatı\n" + ("\n".join(f"{k}: {v['adet']} hareket, {v['tutar']:,.2f} TL" for k,v in o.items()) or "Ekstre yüklenmedi."))


async def muhasebe_export_komutu(update, context):
    if not await _yetki_kontrol(update): return
    try:
        yil, ay = _ay_parse(context.args[0])
        bas = date(yil, ay, 1)
        son = (date(yil + (ay == 12), 1 if ay == 12 else ay + 1, 1) - timedelta(days=1))
        yol = muhasebe_csv_export(bas, son)
        with yol.open("rb") as f:
            await update.message.reply_document(document=f, filename=yol.name, caption="Muhasebe fiş dışa aktarımı")
    except Exception as exc:
        await update.message.reply_text(f"Kullanım: /muhasebe_export YYYY-AA\n{str(exc)[:400]}")


async def kullanici_ekle_komutu(update, context):
    if not await _yetki_kontrol(update): return
    chat_id = update.effective_chat.id
    if not _admin(chat_id):
        await update.message.reply_text("Bu işlem ADMIN yetkisi gerektirir."); return
    try:
        yeni_id, rol = int(context.args[0]), context.args[1].upper()
        rol_ata(yeni_id, rol, chat_id)
        YETKILI_CHAT_IDS.add(yeni_id)
        temp = Path(str(TELEGRAM_OWNER_FILE) + '.tmp')
        temp.write_text(json.dumps({'chat_ids': sorted(YETKILI_CHAT_IDS)}, ensure_ascii=False), encoding='utf-8')
        os.replace(temp, TELEGRAM_OWNER_FILE)
        await update.message.reply_text(f"Kullanıcı eklendi: {yeni_id} ({rol})")
    except Exception as exc:
        await update.message.reply_text(f"Kullanım: /kullanici_ekle <chat_id> <rol>\n{str(exc)[:400]}")


async def onay_politikasi_komutu(update, context):
    if not await _yetki_kontrol(update): return
    if not _admin(update.effective_chat.id):
        await update.message.reply_text("Bu işlem ADMIN yetkisi gerektirir."); return
    try:
        politika_ayarla(context.args[0], context.args[1], update.effective_chat.id)
        await update.message.reply_text("Onay politikası güncellendi.")
    except Exception as exc:
        await update.message.reply_text(f"Kullanım: /onay_politikasi <limit_TL> <onay_sayısı>\n{str(exc)[:400]}")


async def finans_kontrol_komutu(update, context):
    if not await _yetki_kontrol(update): return
    senkron = senkron_kuyrugunu_isle()
    await update.message.reply_text("Finans çekirdeği: " + json.dumps(
        {**sistem_kontrolu(), "senkron_sonucu": senkron}, ensure_ascii=False))


async def excel_index_komutu(update, context):
    if not await _yetki_kontrol(update): return
    firma = _aktif_firma(update.effective_chat.id) or "touche_holding"
    try:
        await update.message.reply_text("Excel yapısı yerel olarak indeksleniyor...")
        sonuc = indeks_olustur(firma, zorla=True)
        await update.message.reply_text(
            f"İndeks hazır: {FIRMALAR[firma]['ad']}\nParça: {sonuc['total_chunks']}\nToken: {sonuc['total_tokens']}")
    except Exception as exc:
        await update.message.reply_text(f"Excel indeksi oluşturulamadı: {str(exc)[:700]}")


async def excel_sor_komutu(update, context):
    if not await _yetki_kontrol(update): return
    firma = _aktif_firma(update.effective_chat.id) or "touche_holding"
    soru = " ".join(context.args).strip()
    if not soru:
        await update.message.reply_text("Kullanım: /excel_sor <soru>"); return
    try:
        await update.message.reply_text("Excel kanıtları aranıyor...")
        sonuc = excel_soru_cevapla(firma, soru)
        kaynaklar = "\n".join(f"- {x}" for x in sonuc["kaynaklar"])
        await update.message.reply_text(
            f"{sonuc['cevap']}\n\nGüven: %{sonuc['guven']}\nKaynaklar:\n{kaynaklar}"[:4000])
    except Exception as exc:
        await update.message.reply_text(f"Excel sorusu cevaplanamadı: {str(exc)[:700]}")


async def excel_kontrol_komutu(update, context):
    if not await _yetki_kontrol(update): return
    firma = _aktif_firma(update.effective_chat.id) or "touche_holding"
    witan = bool(context.args and context.args[0].casefold() == "witan")
    try:
        sonuc = excel_kalite_kontrol(firma, witan=witan)
        yerel = sonuc["yerel"]
        metin = (f"Excel kontrolü: {yerel['durum']}\nSayfa: {yerel['istatistik'].get('sayfa', 0)}"
                 f"\nFormül: {yerel['istatistik'].get('formul', 0)}")
        if yerel["sorunlar"]:
            metin += "\nSorunlar:\n" + "\n".join(yerel["sorunlar"][:15])
        if yerel["uyarilar"]:
            metin += "\nUyarılar:\n" + "\n".join(yerel["uyarilar"][:15])
        if witan:
            uzak = sonuc.get("witan", {})
            metin += f"\nWitan: {uzak.get('durum')} — {uzak.get('sebep', 'rapor oluşturuldu')}"
        await update.message.reply_text(metin[:4000])
    except Exception as exc:
        await update.message.reply_text(f"Excel kontrolü çalışmadı: {str(exc)[:700]}")


async def receteler_komutu(update, context):
    if not await _yetki_kontrol(update): return
    rows = recete_listele()
    await update.message.reply_text("Reçeteler\n\n" + ("\n".join(
        f"{x['name']} (v{x['version']}): {x['description']}" for x in rows) or "Kayıtlı reçete yok."))


async def recete_calistir_komutu(update, context):
    if not await _yetki_kontrol(update): return
    if not await _yazma_kontrol(update): return
    try:
        name, donem = context.args[:2]
        await update.message.reply_text("Reçete çalıştırılıyor; Excel'ler kontrol edilip finans paketi hazırlanıyor...")
        sonuc = recete_calistir(name, {"donem": donem}, f"telegram:{update.effective_chat.id}")
        ozet = next((x["result"] for x in sonuc["steps"] if x["action"] == "management_summary"), {})
        await update.message.reply_text(
            f"Reçete tamamlandı: {name}\nDönem: {donem}\nNet sonuç: {ozet.get('net', 0):,.2f} TL")
        for path in sonuc.get("files", []):
            dosya = Path(path)
            if dosya.exists():
                with dosya.open("rb") as f:
                    await update.message.reply_document(document=f, filename=dosya.name)
    except Exception as exc:
        await update.message.reply_text(
            f"Kullanım: /recete_calistir aylik_finans_paketi YYYY-AA\n{str(exc)[:700]}")


async def _bot_hazir(app):
    _eslestirme_kodu_hazirla()
    veritabanini_hazirla()
    try:
        veritabani_yedekle()
        senkron_kuyrugunu_isle()
    except Exception as exc:
        log.error("Finans veritabanı yedeklenemedi: %s", exc)
    for chat_id in YETKILI_CHAT_IDS:
        if kullanici_rolu(chat_id) is None:
            rol_ata(chat_id, "ADMIN", "mevcut_yetkili")
    await app.bot.set_my_commands([
        BotCommand("start", "Yardım ve kullanım"),
        BotCommand("firmalar", "Firma listesini göster"),
        BotCommand('yeni_faaliyet', 'Faaliyet ve Excel aç'),
        BotCommand('yeni_proje', 'Seçili faaliyete proje ekle'),
        BotCommand('yeni_cari', 'Yeni faaliyete cari ekle'),
        BotCommand('vazgec', 'Açılış akışından vazgeç'),
        BotCommand("firma", "Aktif firmayı seç"),
        BotCommand("serbest", "Otomatik firma moduna dön"),
        BotCommand("durum", "Bot ve onay durumunu göster"),
        BotCommand("son", "Son finans kayıtlarını göster"),
        BotCommand("ara", "Kayıtlarda ara"),
        BotCommand("ozet", "Finans özeti"),
        BotCommand("mali_durum", "Merkezi mali durum"),
        BotCommand("excel_sor", "Seçili Excel'e soru sor"),
        BotCommand("excel_index", "Akıllı Excel indeksini yenile"),
        BotCommand("excel_kontrol", "Excel kalite kontrolü"),
        BotCommand("receteler", "Tekrarlanabilir işleri göster"),
        BotCommand("recete_calistir", "Finans reçetesi çalıştır"),
        BotCommand("mizan", "Aylık mizan"),
        BotCommand("nakit_tahmin", "Vadeli nakit tahmini"),
        BotCommand("borclar", "Tedarikçi borçları"),
        BotCommand("alacaklar", "Müşteri alacakları"),
        BotCommand("acilis_bakiyesi", "Faaliyet açılış bakiyesi"),
        BotCommand("butce", "Bütçe kaydı"),
        BotCommand("butce_rapor", "Bütçe gerçekleşen raporu"),
        BotCommand("mutabakat", "Banka mutabakatı"),
        BotCommand("muhasebe_export", "Muhasebe CSV dışa aktar"),
        BotCommand("donem_kapat", "Muhasebe dönemini kapat"),
        BotCommand("finans_kontrol", "Finans çekirdeğini kontrol et"),
        BotCommand("bekleyen", "Onay bekleyen işlemler"),
        BotCommand("iptal", "Yazılmış belgeyi iptal et"),
        BotCommand("kimlik", "Telegram Chat ID'yi göster"),
        BotCommand('yetkilendir', 'İlk yetkili hesabı eşleştir'),
    ])


async def _genel_hata(update, context):
    log.exception("Telegram handler hatasi", exc_info=context.error)


def uygulama_olustur():
    app = Application.builder().token(BOT_TOKEN).post_init(_bot_hazir).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("firmalar", firmalar_komutu))
    app.add_handler(CommandHandler('faaliyetler', firmalar_komutu))
    app.add_handler(CommandHandler('yeni_faaliyet', yeni_faaliyet_komutu))
    app.add_handler(CommandHandler('yeni_proje', yeni_proje_komutu))
    app.add_handler(CommandHandler('yeni_cari', yeni_cari_komutu))
    app.add_handler(CommandHandler('vazgec', vazgec_komutu))
    app.add_handler(CommandHandler("firma", firma_komutu))
    app.add_handler(CommandHandler("serbest", serbest_komutu))
    app.add_handler(CommandHandler("durum", durum_komutu))
    app.add_handler(CommandHandler("son", son_komutu))
    app.add_handler(CommandHandler("ara", ara_komutu))
    app.add_handler(CommandHandler("ozet", ozet_komutu))
    app.add_handler(CommandHandler("mali_durum", mali_durum_komutu))
    app.add_handler(CommandHandler("kar_zarar", mali_durum_komutu))
    app.add_handler(CommandHandler("nakit", mali_durum_komutu))
    app.add_handler(CommandHandler("excel_sor", excel_sor_komutu))
    app.add_handler(CommandHandler("excel_index", excel_index_komutu))
    app.add_handler(CommandHandler("excel_kontrol", excel_kontrol_komutu))
    app.add_handler(CommandHandler("receteler", receteler_komutu))
    app.add_handler(CommandHandler("recete_calistir", recete_calistir_komutu))
    app.add_handler(CommandHandler("mizan", mizan_komutu))
    app.add_handler(CommandHandler("nakit_tahmin", nakit_tahmin_komutu))
    app.add_handler(CommandHandler("borclar", borclar_komutu))
    app.add_handler(CommandHandler("alacaklar", alacaklar_komutu))
    app.add_handler(CommandHandler("acilis_bakiyesi", acilis_bakiyesi_komutu))
    app.add_handler(CommandHandler("butce", butce_komutu))
    app.add_handler(CommandHandler("butce_rapor", butce_rapor_komutu))
    app.add_handler(CommandHandler("mutabakat", mutabakat_komutu))
    app.add_handler(CommandHandler("muhasebe_export", muhasebe_export_komutu))
    app.add_handler(CommandHandler("donem_kapat", donem_kapat_komutu))
    app.add_handler(CommandHandler("donem_ac", donem_ac_komutu))
    app.add_handler(CommandHandler("kullanici_ekle", kullanici_ekle_komutu))
    app.add_handler(CommandHandler("onay_politikasi", onay_politikasi_komutu))
    app.add_handler(CommandHandler("finans_kontrol", finans_kontrol_komutu))
    app.add_handler(CommandHandler("bekleyen", bekleyen_komutu))
    app.add_handler(CommandHandler("iptal", iptal_komutu))
    app.add_handler(CommandHandler("kimlik", kimlik_komutu))
    app.add_handler(CommandHandler('yetkilendir', yetkilendir_komutu))
    app.add_handler(CommandHandler("yardim", start))
    app.add_handler(MessageHandler(filters.PHOTO, foto_al))
    app.add_handler(MessageHandler(filters.Document.ALL, dosya_al))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, duzeltme_metni_al))
    app.add_handler(CallbackQueryHandler(buton_cevabi))
    app.add_error_handler(_genel_hata)

    return app


def main():
    app = uygulama_olustur()

    log.info("Bot baslatildi, mesaj bekleniyor...")
    log.info(f"Kayitli firmalar: {list(FIRMALAR.keys())}")
    if not YETKILI_CHAT_IDS:
        log.warning("Telegram sahibi tanımlı değil; finans komutları eşleştirme yapılana kadar kapalı")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
