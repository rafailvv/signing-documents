from io import BytesIO

import fitz
from PIL import Image, ImageDraw

from app.pdf_export import find_unicode_font


def make_pdf_bytes(page_count: int = 1, width: int = 595, height: int = 842) -> bytes:
    document = fitz.open()
    for page_number in range(page_count):
        page = document.new_page(width=width, height=height)
        page.insert_text(
            (72, 72),
            f"Test page {page_number + 1}",
            fontsize=12,
        )

    buffer = BytesIO()
    document.save(buffer)
    document.close()
    return buffer.getvalue()


def make_signature_pdf_bytes(
    *,
    text: str,
    line: tuple[float, float, float, float] | None = None,
    extra_lines: list[tuple[float, float, float, float]] | None = None,
    text_position: tuple[float, float] = (72, 720),
    width: int = 595,
    height: int = 842,
) -> bytes:
    document = fitz.open()
    page = document.new_page(width=width, height=height)
    font_path = find_unicode_font()
    kwargs = {}
    if font_path is not None:
        kwargs = {"fontfile": str(font_path), "fontname": "testfont"}

    page.insert_text(text_position, text, fontsize=12, **kwargs)

    if line is not None:
        page.draw_line((line[0], line[1]), (line[2], line[3]), width=1)

    for extra_line in extra_lines or []:
        page.draw_line(
            (extra_line[0], extra_line[1]),
            (extra_line[2], extra_line[3]),
            width=1,
        )

    buffer = BytesIO()
    document.save(buffer)
    document.close()
    return buffer.getvalue()


def make_scanned_line_pdf_bytes(width: int = 595, height: int = 842) -> bytes:
    image = Image.new("RGB", (width * 2, height * 2), "white")
    draw = ImageDraw.Draw(image)
    draw.line((210 * 2, 720 * 2, 430 * 2, 720 * 2), fill="black", width=4)

    image_buffer = BytesIO()
    image.save(image_buffer, format="PNG")

    document = fitz.open()
    page = document.new_page(width=width, height=height)
    page.insert_image(page.rect, stream=image_buffer.getvalue())

    pdf_buffer = BytesIO()
    document.save(pdf_buffer)
    document.close()
    return pdf_buffer.getvalue()
