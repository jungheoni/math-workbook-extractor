"""PDF 페이지를 다크 이미지로 렌더링해 PDF와 16:9 PPTX로 만든다."""
from __future__ import annotations

import io
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image
from reportlab.pdfgen import canvas

from dark_mode import convert_to_dark_mode
from pptx_exporter import export_pptx


def convert_pdf_to_dark_outputs(pdf_bytes: bytes, work_dir: Path) -> tuple[bytes, bytes]:
    """입력 PDF를 페이지 단위로 반전하고 (다크 PDF, 다크 PPTX)를 반환한다."""
    if not pdf_bytes.startswith(b"%PDF"):
        raise ValueError("올바른 PDF 파일이 아닙니다.")

    image_dir = work_dir / "dark_pages"
    image_dir.mkdir(parents=True, exist_ok=True)
    document = pdfium.PdfDocument(pdf_bytes)
    if len(document) == 0:
        raise ValueError("페이지가 없는 PDF입니다.")

    paths: list[Path] = []
    page_sizes: list[tuple[int, int]] = []
    for index in range(len(document)):
        page = document[index]
        bitmap = page.render(scale=2.0)
        source = bitmap.to_pil().convert("RGB")
        dark = convert_to_dark_mode(source, preserve_colors=True)
        path = image_dir / f"{index + 1:04d}p.png"
        dark.save(path, "PNG", optimize=True)
        paths.append(path)
        page_sizes.append(dark.size)
        source.close()
        dark.close()
        bitmap.close()
        page.close()
    document.close()

    pdf_buffer = io.BytesIO()
    pdf_canvas = canvas.Canvas(pdf_buffer, pageCompression=1)
    for path, (width, height) in zip(paths, page_sizes):
        # scale=2는 PDF 1pt당 2px이므로 원본 페이지 크기를 그대로 복원한다.
        page_width, page_height = width / 2.0, height / 2.0
        pdf_canvas.setPageSize((page_width, page_height))
        pdf_canvas.drawImage(str(path), 0, 0, page_width, page_height, preserveAspectRatio=True)
        pdf_canvas.showPage()
    pdf_canvas.save()

    ppt_work = work_dir / "dark_ppt"
    ppt_work.mkdir(parents=True, exist_ok=True)
    pptx_bytes = export_pptx(paths, ppt_work, dark_mode=True)
    return pdf_buffer.getvalue(), pptx_bytes
