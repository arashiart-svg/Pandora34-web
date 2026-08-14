import io
import os
import re

import cv2
import numpy as np
import pytesseract
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

SYNC_TOKEN = os.environ.get("SYNC_TOKEN", "")
LATIN_TO_CYR = str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ")
PLATE_RE = re.compile(r"[АВЕКМНОРСТУХ]\d{3}[АВЕКМНОРСТУХ]{2}\d{2,3}")
WHITELIST = "ABCEHKMOPTXYАВЕКМНОРСТУХ0123456789"

app = FastAPI()


def check_token(authorization: str | None) -> None:
    if not SYNC_TOKEN:
        raise HTTPException(500, "ocr_not_configured")
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if token != SYNC_TOKEN:
        raise HTTPException(401, "unauthorized_device")


def extract_plate(text: str) -> str:
    s = str(text or "").upper().translate(LATIN_TO_CYR)
    s = re.sub(r"[^АВЕКМНОРСТУХ0-9]", "", s)
    m = PLATE_RE.search(s)
    return m.group(0) if m else ""


def resize_max(img: np.ndarray, max_side: int = 1600) -> np.ndarray:
    h, w = img.shape[:2]
    m = max(h, w)
    if m <= max_side:
        return img
    scale = max_side / m
    return cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)


def ocr_image(gray: np.ndarray, psm: int) -> str:
    cfg = f"--oem 3 --psm {psm} -c tessedit_char_whitelist={WHITELIST}"
    return pytesseract.image_to_string(gray, lang="rus+eng", config=cfg)


def variants(gray: np.ndarray) -> list[np.ndarray]:
    out = [gray]
    blur = cv2.bilateralFilter(gray, 9, 75, 75)
    out.append(blur)
    _, otsu = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    out.append(otsu)
    out.append(cv2.bitwise_not(otsu))
    adap = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 7)
    out.append(adap)
    out.append(cv2.bitwise_not(adap))
    return out


def plate_crops(gray: np.ndarray) -> list[np.ndarray]:
    h, w = gray.shape
    rect = cv2.getStructuringElement(cv2.MORPH_RECT, (17, 5))
    blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, rect)
    grad = cv2.Sobel(blackhat, cv2.CV_32F, 1, 0, ksize=1)
    grad = np.abs(grad)
    grad = cv2.normalize(grad, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    grad = cv2.blur(grad, (5, 5))
    _, th = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, rect)
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        if ch < 18 or cw < 60:
            continue
        ar = cw / float(ch)
        if ar < 2.0 or ar > 7.5:
            continue
        if cw < w * 0.10:
            continue
        pad = int(ch * 0.2)
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(w, x + cw + pad), min(h, y + ch + pad)
        crop = gray[y0:y1, x0:x1]
        boxes.append((cw * ch, crop))
    boxes.sort(key=lambda t: t[0], reverse=True)
    return [b[1] for b in boxes[:8]]


def read_plate(img_bgr: np.ndarray) -> str:
    img_bgr = resize_max(img_bgr)
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    crops = plate_crops(gray)
    crops.append(gray)
    found = []
    for crop in crops:
        if crop.size == 0:
            continue
        ch, cw = crop.shape[:2]
        if ch < 32:
            scale = 32 / ch
            crop = cv2.resize(crop, (int(cw * scale), 32), interpolation=cv2.INTER_CUBIC)
        for v in variants(crop):
            for psm in (7, 8, 13):
                plate = extract_plate(ocr_image(v, psm))
                if plate:
                    found.append(plate)
                    if len(found) >= 3:
                        break
            if len(found) >= 3:
                break
        if found:
            break
    if not found:
        return ""
    return max(set(found), key=found.count)


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/plate")
async def plate(
    image: UploadFile = File(...),
    authorization: str | None = Header(default=None),
):
    check_token(authorization)
    data = await image.read()
    if not data:
        raise HTTPException(400, "empty")
    if len(data) > 12 * 1024 * 1024:
        raise HTTPException(413, "too_large")
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        pil = None
        try:
            from PIL import Image

            pil = Image.open(io.BytesIO(data)).convert("RGB")
            img = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
        except Exception as exc:
            raise HTTPException(400, "bad_image") from exc
    value = read_plate(img)
    return JSONResponse({"plate": value, "ok": bool(value)})
