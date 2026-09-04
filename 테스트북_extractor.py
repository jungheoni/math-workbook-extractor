"""풍산자 테스트북 PDF에서 두 자리 문항 번호별 PNG를 추출한다."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image

from pungsanja_extractor import CAPTURE_TOP_PADDING, Marker, assign_columns
from 필수유형_extractor import crop, save, tight_box


def norm(value: object) -> str:
    return re.sub(r"\s+", "", str(value))


def markers(page) -> list[Marker]:
    """본문의 굵은 두 자리 번호를 찾고 수식·쪽수의 두 자리 숫자는 제외한다."""
    found: list[Marker] = []
    for word in page.extract_words(
        use_text_flow=True,
        x_tolerance=2,
        y_tolerance=2,
        extra_attrs=["fontname", "size", "non_stroking_color"],
    ):
        text = str(word["text"]).strip()
        if not re.fullmatch(r"\d{2}", text):
            continue
        if float(word.get("size", 0)) < 14:
            continue
        if not (45 < float(word["x0"]) < page.width - 45):
            continue
        found.append(
            Marker(
                text,
                float(word["x0"]),
                float(word["top"]),
                float(word["x1"]),
                float(word["bottom"]),
            )
        )
    # 회색/색상 경계에서 한 자리씩 분리된 번호도 다시 두 자리로 합친다.
    chars = [
        char for char in page.chars
        if re.fullmatch(r"\d", str(char.get("text", "")))
        and float(char.get("size", 0)) >= 14
    ]
    chars.sort(key=lambda char: (round(float(char["top"]), 1), float(char["x0"])))
    for first, second in zip(chars, chars[1:]):
        if abs(float(first["top"]) - float(second["top"])) > 1.5:
            continue
        if not (-1 <= float(second["x0"]) - float(first["x1"]) <= 3):
            continue
        text = str(first["text"]) + str(second["text"])
        found.append(Marker(text, float(first["x0"]), float(first["top"]),
                            float(second["x1"]), float(second["bottom"])))
    unique: list[Marker] = []
    for marker in found:
        if not any(abs(old.x0 - marker.x0) < 2 and abs(old.top - marker.top) < 2 for old in unique):
            unique.append(marker)
    return sorted(unique, key=lambda marker: (marker.top, marker.x0))


def extract_page(page, rendered: Image.Image, output: Path, page_no: int) -> list[Path]:
    page_markers, columns, split = assign_columns(markers(page), page.width)
    page_words = page.extract_words(use_text_flow=False, x_tolerance=2, y_tolerance=2)
    made: list[Path] = []
    serial = 0
    for column in range(columns):
        # 왼쪽 재단용 절취선과 가위 표시는 문제 콘텐츠가 아니다.
        left = 75 if column == 0 else split + 5
        right = page.width - 55 if columns == 1 or column == 1 else split - 5
        footer_tops = [
            float(word["top"])
            for word in page_words
            if left <= (float(word["x0"]) + float(word["x1"])) / 2 < right
            and float(word["top"]) > page.height * 0.80
            and "테스트" in norm(word["text"])
        ]
        column_end = min([page.height - 85, *(top - 5 for top in footer_tops)])
        same_column = sorted(
            (marker for marker in page_markers if marker.column == column),
            key=lambda marker: marker.top,
        )
        for index, marker in enumerate(same_column):
            bottom = (
                same_column[index + 1].top - 5
                if index + 1 < len(same_column)
                else column_end
            )
            # 서술형·주관식의 작성 공간에 인쇄된 풀이/답은 문제에서 제외한다.
            solution_tops = [
                float(word["top"])
                for word in page_words
                if left <= (float(word["x0"]) + float(word["x1"])) / 2 < right
                and marker.bottom < float(word["top"]) < bottom
                and norm(word["text"]) in {"풀이", "풀이과정", "답"}
            ]
            if solution_tops:
                bottom = min(solution_tops) - 5
            box = tight_box(
                page, left, right, marker.top - CAPTURE_TOP_PADDING, bottom
            )
            if box is None:
                continue
            serial += 1
            made.append(save(crop(page, rendered, box), output, page_no, serial))
    return made


def extract(source: Path, output: Path, scale: float = 3.0) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    renderer = pdfium.PdfDocument(str(source))
    made: list[Path] = []
    try:
        with pdfplumber.open(source) as document:
            for index, page in enumerate(document.pages):
                if not markers(page):
                    continue
                text = re.sub(r"\s+", "", page.extract_text() or "")
                if "정답과해설" in text or "정답과풀이" in text:
                    # 본문 상단의 쪽수 안내는 허용하되 실제 해설 페이지는 제외한다.
                    if "테스트[" not in text and "테스트［" not in text:
                        continue
                rendered = renderer[index].render(scale=scale).to_pil().convert("RGB")
                current = extract_page(page, rendered, output, index + 1)
                made.extend(current)
                print(f"page {index + 1}: {len(current)} image(s)")
    finally:
        renderer.close()
    return sorted(made)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=Path("output/테스트북"))
    parser.add_argument("--scale", type=float, default=3.0)
    args = parser.parse_args()
    results = extract(args.pdf, args.output, args.scale)
    print(f"done: {len(results)} image(s)")
