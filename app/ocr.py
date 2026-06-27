from io import BytesIO

import fitz
import pytesseract
from PIL import Image
from pytesseract import Output, TesseractError

from .models import BoundingBox, PageSize, WordBox


OCR_DPI = 200


def extract_ocr_words(
    page: fitz.Page,
    *,
    languages: str = "rus+eng",
) -> tuple[list[WordBox], str, list[str]]:
    warnings: list[str] = []
    pixmap = page.get_pixmap(dpi=OCR_DPI, alpha=False)
    image = Image.open(BytesIO(pixmap.tobytes("png")))
    page_size = PageSize(width=page.rect.width, height=page.rect.height)

    try:
        data = pytesseract.image_to_data(
            image,
            lang=languages,
            output_type=Output.DICT,
            config="--psm 6",
        )
        used_languages = languages
    except TesseractError as exc:
        warnings.append(f"ocr_language_fallback:{languages}")
        try:
            data = pytesseract.image_to_data(
                image,
                lang="eng",
                output_type=Output.DICT,
                config="--psm 6",
            )
            used_languages = "eng"
        except Exception as fallback_exc:
            return [], "", [*warnings, f"ocr_failed:{fallback_exc}"]
    except Exception as exc:
        return [], "", [f"ocr_failed:{exc}"]

    words: list[WordBox] = []
    text_parts: list[str] = []
    x_scale = page_size.width / pixmap.width
    y_scale = page_size.height / pixmap.height

    for index, raw_text in enumerate(data.get("text", [])):
        text = str(raw_text).strip()
        if not text:
            continue
        confidence = parse_confidence(data.get("conf", ["-1"])[index])
        if confidence < 35:
            warnings.append("low_ocr_confidence")
            continue

        x0 = float(data["left"][index]) * x_scale
        y0 = float(data["top"][index]) * y_scale
        x1 = x0 + float(data["width"][index]) * x_scale
        y1 = y0 + float(data["height"][index]) * y_scale
        if x1 <= x0 or y1 <= y0:
            continue

        words.append(
            WordBox(
                text=text,
                bbox=BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1),
            )
        )
        text_parts.append(text)

    if not words:
        warnings.append("ocr_no_words")
    warnings.append(f"ocr_languages:{used_languages}")
    return words, " ".join(text_parts), sorted(set(warnings))


def parse_confidence(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return -1
