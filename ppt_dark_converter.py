"""이미지 기반 PPTX를 원본 구조를 보존한 채 다크 모드로 변환한다."""
from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree as ET

from PIL import Image, UnidentifiedImageError

from dark_mode import convert_to_dark_mode, convert_to_light_mode


PML = "http://schemas.openxmlformats.org/presentationml/2006/main"
DML = "http://schemas.openxmlformats.org/drawingml/2006/main"
MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
MAX_ENTRIES = 5000

ET.register_namespace("a", DML)
ET.register_namespace("p", PML)


def _convert_media(data: bytes, suffix: str, target_mode: str) -> bytes:
    try:
        with Image.open(io.BytesIO(data)) as source:
            converter = convert_to_dark_mode if target_mode == "dark" else convert_to_light_mode
            converted = converter(source, preserve_colors=True)
    except UnidentifiedImageError:
        return data

    output = io.BytesIO()
    if suffix in (".jpg", ".jpeg"):
        converted.save(output, "JPEG", quality=95, subsampling=0)
    else:
        converted.save(output, "PNG", optimize=True)
    return output.getvalue()


def _set_slide_background(xml_bytes: bytes, target_mode: str) -> bytes:
    root = ET.fromstring(xml_bytes)
    common_slide_data = root.find(f"{{{PML}}}cSld")
    if common_slide_data is None:
        return xml_bytes

    existing = common_slide_data.find(f"{{{PML}}}bg")
    if existing is not None:
        common_slide_data.remove(existing)

    background = ET.Element(f"{{{PML}}}bg")
    properties = ET.SubElement(background, f"{{{PML}}}bgPr")
    solid = ET.SubElement(properties, f"{{{DML}}}solidFill")
    color = "0A0A0A" if target_mode == "dark" else "FFFFFF"
    ET.SubElement(solid, f"{{{DML}}}srgbClr", {"val": color})
    ET.SubElement(properties, f"{{{DML}}}effectLst")
    common_slide_data.insert(0, background)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def convert_pptx_mode(pptx_bytes: bytes, target_mode: str) -> bytes:
    """PPTX의 래스터 이미지와 배경을 지정한 모드로 변환한다."""
    if target_mode not in {"dark", "light"}:
        raise ValueError("target_mode는 dark 또는 light여야 합니다.")
    source_buffer = io.BytesIO(pptx_bytes)
    if not zipfile.is_zipfile(source_buffer):
        raise ValueError("올바른 PowerPoint(.pptx) 파일이 아닙니다.")
    source_buffer.seek(0)

    output_buffer = io.BytesIO()
    converted_images = 0
    with zipfile.ZipFile(source_buffer, "r") as source:
        entries = source.infolist()
        if len(entries) > MAX_ENTRIES:
            raise ValueError("PPTX 내부 파일 수가 너무 많습니다.")
        if sum(entry.file_size for entry in entries) > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("압축 해제된 PPTX 크기가 500MB를 초과합니다.")
        names = {entry.filename for entry in entries}
        if "[Content_Types].xml" not in names or "ppt/presentation.xml" not in names:
            raise ValueError("PowerPoint 문서 구조를 찾을 수 없습니다.")

        with zipfile.ZipFile(output_buffer, "w", zipfile.ZIP_DEFLATED) as destination:
            for entry in entries:
                data = source.read(entry.filename)
                lower = entry.filename.lower()
                if lower.startswith("ppt/media/") and lower.endswith((".png", ".jpg", ".jpeg")):
                    data = _convert_media(data, "." + lower.rsplit(".", 1)[-1], target_mode)
                    converted_images += 1
                elif lower.startswith("ppt/slides/slide") and lower.endswith(".xml"):
                    data = _set_slide_background(data, target_mode)
                destination.writestr(entry, data)

    if converted_images == 0:
        raise ValueError("변환할 PNG/JPEG 문제 이미지를 PPT에서 찾지 못했습니다.")
    return output_buffer.getvalue()


def convert_pptx_to_dark_mode(pptx_bytes: bytes) -> bytes:
    return convert_pptx_mode(pptx_bytes, "dark")


def convert_pptx_to_light_mode(pptx_bytes: bytes) -> bytes:
    return convert_pptx_mode(pptx_bytes, "light")
