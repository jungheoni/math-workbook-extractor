"""풍산자 필수유형 PDF를 일반 문제와 서술형 구성에 맞춰 PNG로 추출한다."""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
import numpy as np
from PIL import Image

from pungsanja_extractor import (
    Marker,
    add_margin,
    assign_columns,
    determine_problem_box,
    is_colored_text,
    pdf_box_to_pixels,
)


def norm(value: object) -> str:
    return re.sub(r"\s+", "", str(value))


def mixed_markers(page) -> list[Marker]:
    """앞쪽 회색 숫자와 마지막 색상 숫자로 조판된 세 자리 번호를 찾는다."""
    chars = [char for char in page.chars if str(char.get("text", "")).isdigit()]
    chars.sort(key=lambda c: (round(float(c["top"]), 1), float(c["x0"])))
    found: list[Marker] = []
    for index in range(len(chars) - 2):
        run = chars[index:index + 3]
        if max(float(c["top"]) for c in run) - min(float(c["top"]) for c in run) > 2.2:
            continue
        if any(float(run[i + 1]["x0"]) - float(run[i]["x1"]) > 3.0 for i in range(2)):
            continue
        text = "".join(str(c["text"]) for c in run)
        colors = [is_colored_text(c.get("non_stroking_color")) for c in run]
        if not (re.fullmatch(r"\d{3}", text) and any(colors) and not all(colors)):
            continue
        candidate = Marker(
            text,
            min(float(c["x0"]) for c in run),
            min(float(c["top"]) for c in run),
            max(float(c["x1"]) for c in run),
            max(float(c["bottom"]) for c in run),
        )
        if not any(abs(m.x0 - candidate.x0) < 2 and abs(m.top - candidate.top) < 2 for m in found):
            found.append(candidate)
    # 문제 번호는 같은 페이지에서 거의 연속된다. 유형 번호·쪽수처럼 멀리
    # 떨어진 세 자리 숫자는 가장 긴 연속 번호군에서 제외한다.
    if not found:
        return []
    ordered = sorted(found, key=lambda m: int(m.number))
    groups: list[list[Marker]] = [[ordered[0]]]
    for marker in ordered[1:]:
        if int(marker.number) - int(groups[-1][-1].number) <= 2:
            groups[-1].append(marker)
        else:
            groups.append([marker])
    selected = max(groups, key=len)
    if len(selected) < 2:
        return []
    return sorted(selected, key=lambda m: (m.top, m.x0))


def words(page):
    return page.extract_words(use_text_flow=False, x_tolerance=2, y_tolerance=2,
                              extra_attrs=["non_stroking_color", "size"])


def tight_box(page, left: float, right: float, top: float, bottom: float):
    boxes = []
    for char in page.chars:
        cx = (float(char["x0"]) + float(char["x1"])) / 2
        cy = (float(char["top"]) + float(char["bottom"])) / 2
        if left <= cx < right and top <= cy < bottom:
            boxes.append((float(char["x0"]), float(char["top"]), float(char["x1"]), float(char["bottom"])))
    for collection in (page.rects, page.curves, page.lines, page.images):
        for obj in collection:
            if not all(key in obj for key in ("x0", "x1", "top", "bottom")):
                continue
            box = tuple(map(float, (obj["x0"], obj["top"], obj["x1"], obj["bottom"])))
            if box[3] - box[1] > (bottom - top) * .85 and box[2] - box[0] < 3:
                continue
            if box[2] - box[0] > (right - left) * .92 and box[3] - box[1] < 3:
                continue
            if box[2] > left and box[0] < right and box[3] > top and box[1] < bottom:
                boxes.append(box)
    if not boxes:
        return None
    return (max(left, min(b[0] for b in boxes) - 5), max(top, min(b[1] for b in boxes) - 4),
            min(right, max(b[2] for b in boxes) + 5), min(bottom, max(b[3] for b in boxes) + 4))


def card_containing(page, word, left: float, right: float):
    wx0, wy0, wx1, wy1 = map(float, (word["x0"], word["top"], word["x1"], word["bottom"]))
    candidates = []
    for collection in (page.rects, page.curves):
        for obj in collection:
            if not all(k in obj for k in ("x0", "x1", "top", "bottom")):
                continue
            x0, y0, x1, y1 = map(float, (obj["x0"], obj["top"], obj["x1"], obj["bottom"]))
            if x0 <= wx0 and y0 <= wy0 and x1 >= wx1 and y1 >= wy1 and x1 - x0 > (right - left) * .45:
                candidates.append((x0, y0, x1, y1))
    if candidates:
        return min(candidates, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
    return tight_box(page, left, right, wy0 - 8, wy1 + 55)


def crop(
    page,
    rendered: Image.Image,
    box,
    margin: int = 24,
    trim_left: bool = False,
) -> Image.Image:
    image = rendered.crop(pdf_box_to_pixels(page, rendered, box)).convert("RGB")
    if trim_left:
        pixels = np.asarray(image)
        content = np.min(pixels, axis=2) < 245
        # 작은 안티앨리어싱 점 하나가 아니라 글자·수식처럼 실제 세로
        # 밀도가 있는 첫 열을 콘텐츠 시작점으로 사용한다.
        column_density = content.sum(axis=0)
        threshold = max(3, int(image.height * 0.006))
        columns = np.where(column_density >= threshold)[0]
        if len(columns):
            left = max(0, int(columns[0]) - 8)
            image = image.crop((left, 0, image.width, image.height))
    return add_margin(image, margin)


def stack(header: Image.Image, body: Image.Image, left_align: bool = False) -> Image.Image:
    width = max(header.width, body.width)
    result = Image.new("RGB", (width, header.height + body.height + 16), "white")
    header_x = 0 if left_align else (width - header.width) // 2
    body_x = 0 if left_align else (width - body.width) // 2
    result.paste(header, (header_x, 0))
    result.paste(body, (body_x, header.height + 16))
    return result


def save(image: Image.Image, output: Path, page_no: int, serial: int, suffix: str = "") -> Path:
    name = f"{page_no:03d}p_{serial:03d}{suffix}.png"
    path = output / name
    image.save(path, "PNG", optimize=True)
    return path


def regular_page(page, rendered, output: Path, page_no: int) -> list[Path]:
    markers, columns, split = assign_columns(mixed_markers(page), page.width)
    made, serial = [], 0
    for column in range(columns):
        left = 0 if column == 0 else split
        right = page.width if columns == 1 or column == 1 else split
        cms = sorted((m for m in markers if m.column == column), key=lambda m: m.top)
        for index, marker in enumerate(cms):
            nxt = cms[index + 1] if index + 1 < len(cms) else None
            # 그래프·보기·선택지가 본문과 멀리 떨어진 편집도 있으므로
            # 같은 단의 다음 번호 직전까지 존재하는 모든 콘텐츠를 보존한다.
            end = nxt.top - 5 if nxt else page.height - 90
            box = tight_box(page, left, right, marker.top - 4, end)
            if box is None:
                continue
            serial += 1
            made.append(save(crop(page, rendered, box, trim_left=True), output, page_no, serial))
    return made


def colored_problem_markers(page):
    result = []
    for word in words(page):
        text = str(word["text"]).strip()
        size = float(word.get("size", 0))
        if re.fullmatch(r"\d{1,2}", text) and 11 <= size <= 20 and is_colored_text(word.get("non_stroking_color")):
            result.append(word)
    return sorted(result, key=lambda w: (float(w["x0"]) >= page.width / 2, float(w["top"])))


def step_outputs(page, rendered, output: Path, page_no: int, prompt_box, steps, left, right, serial, suffix,
                 left_align: bool = False):
    made = []
    header = crop(page, rendered, prompt_box, trim_left=True)
    for idx, step in enumerate(steps):
        top = float(step["top"]) - 4
        bottom = float(steps[idx + 1]["top"]) - 6 if idx + 1 < len(steps) else min(page.height - 95, top + 145)
        body_box = tight_box(page, left, right, top, bottom)
        if body_box is None:
            continue
        made.append(save(stack(header, crop(page, rendered, body_box, trim_left=True), left_align=left_align), output, page_no, serial,
                         f"_{suffix}_{idx + 1}단계"))
    return made


def representative_page(page, rendered, output: Path, page_no: int) -> list[Path]:
    ws = words(page)
    examples = [w for w in ws if norm(w["text"]) == "예제"]
    ujes = [w for w in ws if norm(w["text"]) == "유제"]
    made, serial = [], 0
    for word in examples:
        left, right = (55, page.width - 55)
        box = card_containing(page, word, left, right)
        if box:
            serial += 1
            made.append(save(crop(page, rendered, box, trim_left=True), output, page_no, serial, "_예제"))
    for word in ujes:
        col = int(float(word["x0"]) >= page.width / 2)
        left = 55 if col == 0 else page.width / 2 + 3
        right = page.width / 2 - 3 if col == 0 else page.width - 55
        prompt = card_containing(page, word, left, right)
        if prompt is None:
            continue
        steps = sorted([w for w in ws if left <= float(w["x0"]) < right and float(w["top"]) > prompt[3]
                        and norm(w["text"]) == "step"], key=lambda w: float(w["top"]))
        serial += 1
        if steps:
            made.extend(step_outputs(page, rendered, output, page_no, prompt, steps, left, right, serial, "유제"))
        else:
            made.append(save(crop(page, rendered, prompt, trim_left=True), output, page_no, serial, "_유제"))
    return made


def practice_page(page, rendered, output: Path, page_no: int) -> list[Path]:
    ws = words(page)
    markers = colored_problem_markers(page)
    made, serial = [], 0
    for col in (0, 1):
        left = 55 if col == 0 else page.width / 2 + 3
        right = page.width / 2 - 3 if col == 0 else page.width - 55
        cms = [w for w in markers if int(float(w["x0"]) >= page.width / 2) == col]
        for index, marker in enumerate(cms):
            top = float(marker["top"]) - 5
            end = float(cms[index + 1]["top"]) - 8 if index + 1 < len(cms) else page.height - 90
            region = [w for w in ws if left <= float(w["x0"]) < right and top < float(w["top"]) < end]
            solution = [float(w["top"]) for w in region if norm(w["text"]) == "풀이"]
            prompt_end = min(solution or [end]) - 4
            prompt = tight_box(page, left, right, top, prompt_end)
            if prompt is None:
                continue
            steps = sorted([w for w in region if norm(w["text"]) == "step"], key=lambda w: float(w["top"]))
            serial += 1
            if steps:
                made.extend(step_outputs(page, rendered, output, page_no, prompt, steps, left, right, serial,
                                         "서술형", left_align=True))
            else:
                made.append(save(crop(page, rendered, prompt, trim_left=True), output, page_no, serial, "_서술형"))
    return made


def extract(source: Path, output: Path, scale: float = 3.0) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    renderer = pdfium.PdfDocument(str(source))
    made: list[Path] = []
    try:
        with pdfplumber.open(source) as document:
            for index, page in enumerate(document.pages):
                text = norm(page.extract_text() or "")
                kind = "skip"
                if "예제" in text and "유제" in text and "step" in text:
                    kind = "representative"
                elif (("step" in text and ("주어진단계" in text or "서술형" in text))
                      or "풀이과정을자세히써라" in text or "도전!창의서술" in text):
                    kind = "practice"
                elif index >= 7 and mixed_markers(page):
                    kind = "regular"
                if kind == "skip":
                    continue
                rendered = renderer[index].render(scale=scale).to_pil().convert("RGB")
                fn = {"regular": regular_page, "representative": representative_page,
                      "practice": practice_page}[kind]
                current = fn(page, rendered, output, index + 1)
                made.extend(current)
                print(f"page {index + 1}: {kind}, {len(current)} image(s)")
    finally:
        renderer.close()
    return sorted(made)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=Path("output/필수유형"))
    parser.add_argument("--scale", type=float, default=3.0)
    args = parser.parse_args()
    results = extract(args.pdf, args.output, args.scale)
    print(f"done: {len(results)} image(s)")
