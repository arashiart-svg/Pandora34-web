import base64
import io
import os
import re
import threading

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
_engine_lock = threading.Lock()


def get_engine():
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        from rapidocr import RapidOCR

        _engine = RapidOCR()
        return _engine


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


def ocr_texts(img_bgr: np.ndarray) -> str:
    engine = get_engine()
    result = engine(img_bgr)
    texts: list[str] = []
    txts = getattr(result, "txts", None)
    if txts:
        texts = [str(x) for x in txts if x]
    elif isinstance(result, (list, tuple)) and result:
        rows = result[0] if result and isinstance(result[0], list) else result
        for item in rows or []:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                texts.append(str(item[1]))
    return " ".join(texts).strip()


def read_plate(img_bgr: np.ndarray) -> tuple[str, str]:
    h, w = img_bgr.shape[:2]
    if max(h, w) > 900:
        scale = 900 / max(h, w)
        img_bgr = cv2.resize(img_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    raw = ocr_texts(img_bgr)
    plate = extract_plate(raw)
    if plate:
        return plate, raw
    # узкая полоска: чуть обрезать края (флаг / RUS) и повторить
    h, w = img_bgr.shape[:2]
    if w > 40:
        cut = img_bgr[:, int(w * 0.08) : int(w * 0.92)]
        raw2 = ocr_texts(cut)
        raw = f"{raw} {raw2}".strip()
        plate = extract_plate(raw)
    return plate, raw


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
