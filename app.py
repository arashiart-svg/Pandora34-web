import base64
import io
import os
import re
import threading

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("ORT_NUM_THREADS", "1")

import cv2
import numpy as np
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

SYNC_TOKEN = os.environ.get("SYNC_TOKEN", "")
LATIN_TO_CYR = str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ")
PLATE_RE = re.compile(r"[АВЕКМНОРСТУХ]\d{3}[АВЕКМНОРСТУХ]{2}\d{2,3}")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_engine = None
_doc_engine = None
_engine_lock = threading.Lock()

VIN_RE = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b")
FIO_WORD_RE = re.compile(r"[А-ЯЁ]{2,}")
SKIP_WORDS = {
    "РОССИЙСКАЯ", "ФЕДЕРАЦИЯ", "СВИДЕТЕЛЬСТВО", "РЕГИСТРАЦИИ", "СОБСТВЕННИК",
    "ВЛАДЕЛЕЦ", "ОСОБЫЕ", "ОТМЕТКИ", "ВОЛГОГРАДСКАЯ", "ОБЛАСТЬ", "СУБЪЕКТ",
    "НАСЕЛЕННЫЙ", "ПУНКТ", "УЛИЦА", "КВАРТИРА", "ГОСУДАРСТВЕННЫЙ", "НОМЕР",
    "ИДЕНТИФИКАЦИОННЫЙ", "МАРКА", "МОДЕЛЬ", "КАТЕГОРИЯ", "ШАССИ", "КУЗОВ",
    "ЦВЕТ", "МАССА", "ПАСПОРТ", "ОДОБРЕНИЕ", "ЭКОЛОГИЧЕСКИЙ", "КЛАСС",
    "ЛЕГКОВОЙ", "СЕДАН", "УНИВЕРСАЛ", "ХЭТЧБЕК", "ВНЕДОРОЖНИК", "ПИКАП",
    "ОТСУТСТВУЕТ", "ПЯТЫЙ", "ЧЕТВЕРТЫЙ", "ТРЕТИЙ", "БЕЛЫЙ", "ЧЕРНЫЙ",
    "СЕРЫЙ", "СИНИЙ", "КРАСНЫЙ", "ЗЕЛЕНЫЙ", "КОРИЧНЕВЫЙ", "СЕРЕБРИСТЫЙ",
    "ВЫДАЧИ", "ДАТА", "КОД", "ПОДРАЗДЕЛЕНИЯ", "ГОЗНАК", "ПЕРМЬ",
    "ОБОРУДОВАНО", "МОЩНОСТЬ", "ТИП", "ПРИЦЕП", "КАБИНА", "РАМА",
    "ВОЛГОГРАД", "ТИТОВА", "ГЕРМАНА",
}
BRAND_ALIASES = {
    "LADA": "Lada", "ВАЗ": "Lada", "VAZ": "Lada", "ЛАДА": "Lada",
    "BMW": "BMW", "MERCEDES": "Mercedes-Benz", "MERCEDES-BENZ": "Mercedes-Benz",
    "VW": "Volkswagen", "VOLKSWAGEN": "Volkswagen", "ФОЛЬКСВАГЕН": "Volkswagen",
    "TOYOTA": "Toyota", "ТОЙОТА": "Toyota", "KIA": "Kia", "КИА": "Kia",
    "HYUNDAI": "Hyundai", "ХЕНДАЙ": "Hyundai", "ХЕНДЭ": "Hyundai",
    "RENAULT": "Renault", "РЕНО": "Renault", "NISSAN": "Nissan", "НИССАН": "Nissan",
    "FORD": "Ford", "ФОРД": "Ford", "SKODA": "Skoda", "ШКОДА": "Skoda",
    "CHEVROLET": "Chevrolet", "ШЕВРОЛЕ": "Chevrolet", "HAVAL": "Haval", "ХАВЕЙЛ": "Haval",
    "CHERY": "Chery", "ЧЕРИ": "Chery", "GEELY": "Geely", "ДЖИЛИ": "Geely",
    "MAZDA": "Mazda", "МАЗДА": "Mazda", "HONDA": "Honda", "ХОНДА": "Honda",
    "MITSUBISHI": "Mitsubishi", "МИЦУБИСИ": "Mitsubishi", "AUDI": "Audi", "АУДИ": "Audi",
    "LEXUS": "Lexus", "ЛЕКСУС": "Lexus", "UAZ": "УАЗ", "УАЗ": "УАЗ", "ГАЗ": "ГАЗ",
}
LADA_MODELS = [
    ("VESTA SW CROSS", "Vesta SW Cross"),
    ("VESTA CROSS", "Vesta Cross"),
    ("VESTA SW", "Vesta SW"),
    ("GRANTA CROSS", "Granta Cross"),
    ("XRAY CROSS", "XRAY Cross"),
    ("NIVA TRAVEL", "Niva Travel"),
    ("NIVA LEGEND", "Niva Legend"),
    ("VESTA", "Vesta"),
    ("GRANTA", "Granta"),
    ("LARGUS", "Largus"),
    ("KALINA", "Kalina"),
    ("PRIORA", "Priora"),
    ("XRAY", "XRAY"),
    ("NIVA", "Niva"),
]


def get_engine():
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        from rapidocr import RapidOCR

        _engine = RapidOCR(params={"Global.max_side_len": 320})
        return _engine


def get_doc_engine():
    global _doc_engine
    if _doc_engine is not None:
        return _doc_engine
    with _engine_lock:
        if _doc_engine is not None:
            return _doc_engine
        from rapidocr import RapidOCR

        try:
            _doc_engine = RapidOCR(params={"Global.max_side_len": 1280})
        except Exception:
            _doc_engine = RapidOCR()
        return _doc_engine


def check_token(authorization: str | None, token_q: str | None = None) -> None:
    if not SYNC_TOKEN:
        raise HTTPException(500, "ocr_not_configured")
    auth = (authorization or "").strip()
    if auth.lower().startswith("basic "):
        auth = ""
    token = (token_q or "").strip()
    if not token and auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if token != SYNC_TOKEN:
        raise HTTPException(403, "unauthorized_device")


def extract_plate(text: str) -> str:
    s = str(text or "").upper().replace("RUS", "")
    s = s.translate(LATIN_TO_CYR)
    s = re.sub(r"[^АВЕКМНОРСТУХ0-9]", "", s)
    if s[:1] == "0":
        s = "О" + s[1:]

    def as_let(ch: str) -> str:
        return {"0": "О", "3": "С", "4": "А", "7": "Т", "8": "В", "6": "Б"}.get(ch, ch)

    def as_dig(ch: str) -> str:
        return {"О": "0", "А": "4", "Т": "7", "В": "8", "Б": "6"}.get(ch, ch)

    cands = [s]
    if 8 <= len(s) <= 9:
        n = list(s)
        n[0] = as_let(n[0])
        for i in range(1, min(4, len(n))):
            n[i] = as_dig(n[i])
        if len(n) >= 6:
            n[4] = as_let(n[4])
            n[5] = as_let(n[5])
        for i in range(6, len(n)):
            n[i] = as_dig(n[i])
        cands.append("".join(n))
    for cand in cands:
        m = PLATE_RE.search(cand)
        if m:
            return m.group(0)
    return ""


def ocr_texts(img_bgr: np.ndarray, det: bool = False) -> str:
    h, w = img_bgr.shape[:2]
    if h > 0:
        if det:
            if h > 180:
                nh = 160
                img_bgr = cv2.resize(img_bgr, (max(200, int(w * nh / h)), nh), interpolation=cv2.INTER_AREA)
        elif h < 32 or h > 48:
            nh = 40
            img_bgr = cv2.resize(
                img_bgr,
                (max(160, int(w * nh / h)), nh),
                interpolation=cv2.INTER_CUBIC if h < 32 else cv2.INTER_AREA,
            )
    engine = get_engine()
    result = engine(img_bgr, use_det=det, use_cls=False, use_rec=True)
    texts: list[str] = []
    txts = getattr(result, "txts", None)
    if txts:
        texts = [str(x) for x in txts if x]
    elif isinstance(result, (list, tuple)) and result:
        rows = result[0] if result and isinstance(result[0], list) else result
        for item in rows or []:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                texts.append(str(item[1]))
            elif isinstance(item, str):
                texts.append(item)
    return " ".join(texts).strip()


def read_plate(img_bgr: np.ndarray) -> tuple[str, str]:
    h, w = img_bgr.shape[:2]
    if max(h, w) > 720:
        scale = 720 / max(h, w)
        img_bgr = cv2.resize(img_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    raw = ocr_texts(img_bgr, det=False)
    plate = extract_plate(raw)
    if plate:
        return plate, raw
    raw2 = ocr_texts(img_bgr, det=True)
    raw = f"{raw} {raw2}".strip()
    return extract_plate(raw), raw


async def read_upload_bytes(request: Request) -> bytes:
    ctype = (request.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "bad_json")
        raw = str(body.get("image") or "")
        if raw.startswith("data:"):
            raw = raw.split(",", 1)[-1]
        if not raw:
            raise HTTPException(400, "empty")
        try:
            return base64.b64decode(raw)
        except Exception as exc:
            raise HTTPException(400, "bad_base64") from exc
    if "multipart" in ctype:
        form = await request.form()
        up = form.get("image")
        if up is None:
            raise HTTPException(400, "empty")
        return await up.read() if hasattr(up, "read") else bytes(up)
    return await request.body()


def decode_image(data: bytes) -> np.ndarray:
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is not None:
        return img
    from PIL import Image

    pil = Image.open(io.BytesIO(data)).convert("RGB")
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


@app.get("/health")
def health():
    return {"ok": True}


@app.on_event("startup")
def warmup():
    try:
        get_engine()
    except Exception as exc:
        print("ocr warmup failed:", exc)


@app.post("/ocr/plate")
@app.post("/plate")
async def plate(
    request: Request,
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
):
    check_token(authorization, token)
    data = await read_upload_bytes(request)
    if not data:
        raise HTTPException(400, "empty")
    if len(data) > 12 * 1024 * 1024:
        raise HTTPException(413, "too_large")
    try:
        img = decode_image(data)
    except Exception as exc:
        raise HTTPException(400, "bad_image") from exc
    value, raw = read_plate(img)
    return JSONResponse({"plate": value, "ok": bool(value), "raw": (raw or "")[:120]})


def result_texts(result) -> list[str]:
    texts: list[str] = []
    txts = getattr(result, "txts", None)
    if txts:
        texts = [str(x).strip() for x in txts if x]
    elif isinstance(result, (list, tuple)) and result:
        rows = result[0] if result and isinstance(result[0], list) else result
        for item in rows or []:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                texts.append(str(item[1]).strip())
            elif isinstance(item, str):
                texts.append(item.strip())
    return [t for t in texts if t]


def ocr_doc_texts(img_bgr: np.ndarray) -> list[str]:
    h, w = img_bgr.shape[:2]
    side = max(h, w)
    if side > 1600:
        scale = 1600 / side
        img_bgr = cv2.resize(img_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    engine = get_doc_engine()
    result = engine(img_bgr, use_det=True, use_cls=False, use_rec=True)
    return result_texts(result)


def extract_vin(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]", "", text.upper())
    s = s.replace("I", "1").replace("O", "0").replace("Q", "0")
    m = VIN_RE.search(s)
    if m:
        return m.group(1)
    for i in range(0, max(0, len(s) - 16)):
        chunk = s[i : i + 17]
        if VIN_RE.fullmatch(chunk):
            return chunk
    return ""


def extract_brand_model(text: str) -> tuple[str, str]:
    up = text.upper().replace("Ё", "Е")
    brand = ""
    for key, name in BRAND_ALIASES.items():
        if re.search(r"(?<![A-ZА-Я])" + re.escape(key) + r"(?![A-ZА-Я])", up):
            brand = name
            break
    model = ""
    if brand == "Lada":
        for needle, nice in LADA_MODELS:
            if needle in up:
                model = nice
                break
    if not model:
        m = re.search(r"(?:LADA|ВАЗ|ЛАДА)(?:\s+LADA)?\s+([A-ZА-Я0-9][A-ZА-Я0-9\- ]{1,24})", up)
        if m:
            raw = re.sub(r"\s+[A-Z]{2,4}-[A-Z0-9]{2,4}\b", "", m.group(1)).strip()
            raw = re.sub(r"\bLADA\b", "", raw).strip()
            if raw:
                model = raw.title()
    return brand, model


def extract_name(texts: list[str], blob: str) -> str:
    joined = " ".join(texts)
    near = blob
    idx = max(near.upper().find("СОБСТВЕННИК"), near.upper().find("ВЛАДЕЛЕЦ"))
    window = near[idx : idx + 220] if idx >= 0 else joined
    window = re.sub(r"\s*/\s*[A-Z][A-Z\- ]*", " ", window)
    words = [w for w in FIO_WORD_RE.findall(window.upper().replace("Ё", "Е")) if w not in SKIP_WORDS]
    picked: list[str] = []
    for w in words:
        if w in BRAND_ALIASES or any(w == k for k, _ in LADA_MODELS):
            continue
        if w in {"УЛИЦА", "ДОМ", "ГОРОД", "ТИТОВА", "ГЕРМАНА"}:
            break
        picked.append(w.title())
        if len(picked) == 3:
            break
    return " ".join(picked)


def extract_address(blob: str) -> str:
    city = ""
    m = re.search(r"г\.?\s*([А-Яа-яЁё\-]+)", blob, re.I)
    if m:
        city = m.group(1)
    street = ""
    sm = re.search(r"ул\.?\s*(?:им\.?\s*)?([А-Яа-яЁё.\-]+(?:\s+[А-Яа-яЁё.\-]+){0,3})", blob, re.I)
    if sm:
        street = re.sub(r"\s+", " ", sm.group(1)).strip(" .,")
        street = re.sub(r"\b(дом|д|квартира|кв)\b.*", "", street, flags=re.I).strip(" .,")
    house = ""
    apt = ""
    hm = re.search(r"(?:дом|д\.?)\s*(\d+[А-Яа-я]?)", blob, re.I)
    if hm:
        house = hm.group(1)
    am = re.search(r"(?:квартира|кв\.?)\s*(\d+)", blob, re.I)
    if am:
        apt = am.group(1)
    if not house:
        nums = re.findall(r"\b(\d{1,4})\b", blob)
        skip = {"99", "93", "5", "78"}
        nums = [n for n in nums if n not in skip and len(n) < 5]
        if len(nums) >= 2:
            house, apt = nums[-2], nums[-1]
        elif nums:
            house = nums[-1]
    parts = []
    if city:
        parts.append("г. " + city)
    if street:
        street = re.sub(r"^им\.?\s*", "", street, flags=re.I)
        prefix = "ул. им. " if re.search(r"им\.?", blob, re.I) else "ул. "
        parts.append(prefix + street)
    if house:
        parts.append(house)
    if apt:
        parts.append("кв. " + apt)
    return ", ".join(parts)


def parse_sts(texts: list[str]) -> dict:
    blob = " ".join(texts)
    plate = extract_plate(blob)
    vin = extract_vin(blob)
    brand, model = extract_brand_model(blob)
    name = extract_name(texts, blob)
    address = extract_address(blob)
    filled = any([plate, vin, brand, model, name, address])
    return {
        "ok": filled,
        "plate": plate,
        "vin": vin,
        "brand": brand,
        "model": model,
        "name": name,
        "address": address,
        "raw": " | ".join(texts)[:500],
    }


@app.post("/ocr/sts")
@app.post("/sts")
async def sts(
    request: Request,
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
):
    check_token(authorization, token)
    data = await read_upload_bytes(request)
    if not data:
        raise HTTPException(400, "empty")
    if len(data) > 12 * 1024 * 1024:
        raise HTTPException(413, "too_large")
    try:
        img = decode_image(data)
    except Exception as exc:
        raise HTTPException(400, "bad_image") from exc
    texts = ocr_doc_texts(img)
    return JSONResponse(parse_sts(texts))
