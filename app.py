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
    "СУБЪЕКТ", "РАЙОН", "ОБЛАСТЬ",
}
CYR_TO_LATIN = str.maketrans("АВСЕНКМОРТХУ", "ABCEHKMOPTXY")
PLATE_SPACED_RE = re.compile(
    r"[АВЕКМНОРСТУХ]\s*\d{3}\s*[АВЕКМНОРСТУХ]{2}\s*\d{2,3}"
)
VIN_GROUP_RE = re.compile(r"[A-HJ-NPR-Z0-9]{3,11}(?:[\s\-][A-HJ-NPR-Z0-9]{2,11}){1,3}")
VIN_WMI = ("XTA", "XTK", "XTT", "XWB", "XW8", "XW7", "XWE", "X7L", "XUF", "Z94", "JHM", "JT1", "JT2", "JT3", "JT7", "JTD", "KMH", "TMB", "WAU", "WBA", "WDB", "WDD", "VF1", "VF3", "VF7")
FIO_STOP = {
    "УЛИЦА", "ДОМ", "ГОРОД", "СУБЪЕКТ", "НАСЕЛЕННЫЙ", "ПУНКТ", "КВАРТИРА",
    "ОБЛАСТЬ", "РАЙОН", "ОСОБЫЕ", "ОТМЕТКИ", "КОРПУС", "СТРОЕНИЕ",
}
ADDR_LABELS = {
    "РОССИЙСКАЯ", "ФЕДЕРАЦИЯ", "СУБЪЕКТ", "НАСЕЛЕННЫЙ", "ПУНКТ", "УЛИЦА",
    "ДОМ", "КВАРТИРА", "РАЙОН", "ОБЛАСТЬ", "ОСОБЫЕ", "ОТМЕТКИ", "ВЛАДЕЛЕЦ",
    "СОБСТВЕННИК", "КОРПУС", "СТРОЕНИЕ",
}
LADA_VAZ_MODELS = {
    "2190": "Granta", "2191": "Granta", "2192": "Granta", "2194": "Granta Cross",
    "2180": "Vesta", "2181": "Vesta", "2116": "Vesta",
    "1117": "Kalina", "1118": "Kalina", "1119": "Kalina",
    "2170": "Priora", "2171": "Priora", "2172": "Priora",
    "2123": "Niva", "21214": "Niva",
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


def _box_center(box) -> tuple[float, float]:
    try:
        pts = list(box)
        xs = [float(p[0]) for p in pts]
        ys = [float(p[1]) for p in pts]
        return sum(xs) / max(len(xs), 1), sum(ys) / max(len(ys), 1)
    except Exception:
        return 0.0, 0.0


def result_texts(result) -> list[str]:
    items: list[tuple[str, float, float]] = []
    txts = getattr(result, "txts", None)
    boxes = getattr(result, "boxes", None)
    if txts:
        if boxes is not None:
            for t, b in zip(txts, boxes):
                text = str(t).strip()
                if text:
                    x, y = _box_center(b)
                    items.append((text, y, x))
        else:
            items = [(str(t).strip(), float(i), 0.0) for i, t in enumerate(txts) if str(t).strip()]
    elif isinstance(result, (list, tuple)) and result:
        rows = result[0] if result and isinstance(result[0], list) else result
        for i, item in enumerate(rows or []):
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                text = str(item[1]).strip()
                x, y = _box_center(item[0]) if item[0] is not None else (0.0, float(i))
                if text:
                    items.append((text, y, x))
            elif isinstance(item, str) and item.strip():
                items.append((item.strip(), float(i), 0.0))
    items.sort(key=lambda it: (round(it[1] / 18.0), it[2]))
    return [t for t, _, _ in items]


def prep_doc_image(img_bgr: np.ndarray) -> np.ndarray:
    h, w = img_bgr.shape[:2]
    side = max(h, w)
    if side > 2000:
        scale = 2000 / side
        img_bgr = cv2.resize(img_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    elif side < 1100:
        scale = 1400 / max(side, 1)
        img_bgr = cv2.resize(img_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def ocr_doc_texts(img_bgr: np.ndarray) -> list[str]:
    img_bgr = prep_doc_image(img_bgr)
    engine = get_doc_engine()
    result = engine(img_bgr, use_det=True, use_cls=False, use_rec=True)
    return result_texts(result)


def _latin_alnum(text: str) -> str:
    s = str(text or "").upper().translate(CYR_TO_LATIN)
    s = s.replace("I", "1").replace("O", "0").replace("Q", "0")
    return re.sub(r"[^A-Z0-9]", "", s)


def _vin_ok(vin: str) -> bool:
    if not VIN_RE.fullmatch(vin):
        return False
    letters = sum(ch.isalpha() for ch in vin)
    digits = sum(ch.isdigit() for ch in vin)
    return letters >= 3 and digits >= 4


def extract_vin(texts: list[str], blob: str) -> str:
    found: list[tuple[int, str]] = []

    def add(raw: str, bonus: int = 0) -> None:
        vin = _latin_alnum(raw)
        if len(vin) > 17:
            vin = vin[:17]
        if _vin_ok(vin):
            score = bonus
            if vin.startswith(VIN_WMI):
                score += 5
            found.append((score, vin))

    for line in texts:
        compact = _latin_alnum(line)
        if len(compact) == 17:
            add(compact, 3)
        elif 17 < len(compact) <= 20:
            add(compact[:17], 1)
        lat = line.upper().translate(CYR_TO_LATIN)
        for m in VIN_GROUP_RE.finditer(lat):
            chunk = re.sub(r"[^A-Z0-9]", "", m.group(0))
            if len(chunk) == 17:
                add(chunk, 2)
        for wmi in VIN_WMI:
            idx = lat.find(wmi)
            if idx < 0:
                continue
            tail = re.sub(r"[^A-Z0-9]", "", lat[idx : idx + 28])
            if len(tail) >= 17:
                add(tail[:17], 4)
    lat_blob = blob.upper().translate(CYR_TO_LATIN)
    for wmi in VIN_WMI:
        start = 0
        while True:
            idx = lat_blob.find(wmi, start)
            if idx < 0:
                break
            tail = re.sub(r"[^A-Z0-9]", "", lat_blob[idx : idx + 28])
            if len(tail) >= 17:
                add(tail[:17], 4)
            start = idx + 1
    if not found:
        return ""
    found.sort(reverse=True)
    return found[0][1]


def extract_sts_plate(texts: list[str], blob: str) -> str:
    cands: list[str] = []
    for line in list(texts) + [blob]:
        cyr = str(line or "").upper().translate(LATIN_TO_CYR)
        for m in PLATE_SPACED_RE.finditer(cyr):
            cands.append(re.sub(r"\s+", "", m.group(0)))
    for line in texts:
        if len(re.sub(r"\s+", "", line)) > 36:
            continue
        tight = extract_plate(line)
        if tight:
            cands.append(tight)
    if not cands:
        return ""
    uniq = []
    for p in cands:
        if p not in uniq:
            uniq.append(p)

    def plate_score(p: str) -> tuple[int, int]:
        return (1 if 8 <= len(p) <= 9 else 0, 1 if len(p) == 9 else 0)

    uniq.sort(key=plate_score, reverse=True)
    return uniq[0]


def extract_brand_model(texts: list[str], blob: str) -> tuple[str, str]:
    up = blob.upper().replace("Ё", "Е")
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
            for code, nice in LADA_VAZ_MODELS.items():
                if re.search(r"\b" + code, up):
                    model = nice
                    break
    if not model:
        m = re.search(r"(?:LADA|ВАЗ|ЛАДА|VAZ)(?:\s+LADA)?\s+([A-ZА-Я0-9][A-ZА-Я0-9\- ]{1,24})", up)
        if m:
            raw = re.sub(r"\s+[A-Z]{2,4}-[A-Z0-9]{2,4}\b", "", m.group(1)).strip()
            raw = re.sub(r"\b(?:LADA|ВАЗ|ЛАДА|VAZ)\b", "", raw).strip()
            raw = re.sub(r"\b(?:СЕДАН|УНИВЕРСАЛ|ХЭТЧБЕК|ЛЕГКОВОЙ)\b", "", raw).strip()
            if raw:
                model = raw.title()
    return brand, model


def extract_name(texts: list[str], blob: str) -> str:
    near = blob
    idx = max(near.upper().find("СОБСТВЕННИК"), near.upper().find("ВЛАДЕЛЕЦ"))
    window = near[idx : idx + 240] if idx >= 0 else " ".join(texts)
    window = re.sub(r"\s*/\s*[A-Z][A-Z\- ]*", " ", window)
    words = [w for w in FIO_WORD_RE.findall(window.upper().replace("Ё", "Е")) if w not in SKIP_WORDS]
    picked: list[str] = []
    for w in words:
        if w in FIO_STOP or w in ADDR_LABELS:
            break
        if w in BRAND_ALIASES or any(w == k for k, _ in LADA_MODELS):
            continue
        picked.append(w.title())
        if len(picked) == 3:
            break
    return " ".join(picked)


def _value_after_label(texts: list[str], labels: tuple[str, ...]) -> str:
    for i, line in enumerate(texts):
        up = line.upper().replace("Ё", "Е")
        if not any(lab in up for lab in labels):
            continue
        rest = line
        for lab in labels:
            rest = re.sub(lab, " ", rest, flags=re.I)
        rest = re.sub(r"[^A-Za-zА-Яа-яЁё0-9.\-\s]", " ", rest)
        rest = re.sub(r"\s+", " ", rest).strip(" .,-")
        if rest and rest.upper().replace("Ё", "Е") not in ADDR_LABELS and not any(
            lab in rest.upper() for lab in labels
        ):
            return rest
        if i + 1 < len(texts):
            nxt = re.sub(r"\s+", " ", texts[i + 1]).strip(" .,-")
            nxt_up = nxt.upper().replace("Ё", "Е")
            if nxt and nxt_up not in ADDR_LABELS and not any(lab in nxt_up for lab in labels):
                return nxt
    return ""


def extract_address(texts: list[str], blob: str) -> str:
    city = _value_after_label(texts, ("НАСЕЛЕННЫЙ", "ПУНКТ"))
    if not city:
        m = re.search(r"г\.?\s*([А-Яа-яЁё\-]+)", blob, re.I)
        if m and m.group(1).upper() not in ADDR_LABELS:
            city = m.group(1)
    city = re.sub(r"^(г\.?\s*)", "", city or "", flags=re.I).strip()
    street = _value_after_label(texts, ("УЛИЦА",))
    if not street:
        sm = re.search(r"ул\.?\s*(?:им\.?\s*)?([А-Яа-яЁё.\-]+(?:\s+[А-Яа-яЁё.\-]+){0,3})", blob, re.I)
        if sm:
            street = re.sub(r"\s+", " ", sm.group(1)).strip(" .,")
            street = re.sub(r"\b(дом|д|квартира|кв)\b.*", "", street, flags=re.I).strip(" .,")
    street = re.sub(r"^(ул\.?\s*)?(им\.?\s*)?", "", street or "", flags=re.I).strip(" .,")
    house = _value_after_label(texts, ("ДОМ",))
    if house:
        hm = re.search(r"\d+[А-Яа-яA-Za-z]?", house)
        house = hm.group(0) if hm else ""
    if not house:
        hm = re.search(r"(?:дом|д\.?)\s*(\d+[А-Яа-я]?)", blob, re.I)
        if hm:
            house = hm.group(1)
    apt = _value_after_label(texts, ("КВАРТИРА",))
    if apt:
        am = re.search(r"\d+", apt)
        apt = am.group(0) if am else ""
    if not apt:
        am = re.search(r"(?:квартира|кв\.?)\s*(\d+)", blob, re.I)
        if am:
            apt = am.group(1)
    parts = []
    if city:
        parts.append("г. " + city.title())
    if street:
        prefix = "ул. им. " if re.search(r"им\.?", blob, re.I) or "ИМ" in street.upper() else "ул. "
        street = re.sub(r"^им\.?\s*", "", street, flags=re.I).strip()
        parts.append(prefix + street.title())
    if house:
        parts.append(house)
    if apt:
        parts.append("кв. " + apt)
    return ", ".join(parts)


def parse_sts(texts: list[str], side: str = "") -> dict:
    blob = " ".join(texts)
    side = (side or "").strip().lower()
    plate = extract_sts_plate(texts, blob)
    vin = extract_vin(texts, blob)
    brand, model = extract_brand_model(texts, blob)
    name = extract_name(texts, blob)
    address = extract_address(texts, blob)
    if side == "face":
        name = ""
        address = ""
    elif side == "back":
        plate = ""
        vin = ""
        brand = ""
        model = ""
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
    side: str | None = Query(default=None),
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
    return JSONResponse(parse_sts(texts, side or ""))
