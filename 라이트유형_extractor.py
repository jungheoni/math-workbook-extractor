"""풍산자 라이트유형 PDF에서 문제별 PNG를 추출한다."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
import numpy as np
from PIL import Image

from pungsanja_extractor import Marker, assign_columns, is_colored_text
from 필수유형_extractor import crop, save, stack, tight_box


def norm(value: object) -> str:
    return re.sub(r"\s+", "", str(value))


def remove_teacher_blue(image: Image.Image) -> Image.Image:
    """교사용 미리보기에 덧인쇄된 청록색 정답 표시만 흰색으로 지운다."""
    rgb = np.asarray(image.convert("RGB")).copy()
    red = rgb[..., 0].astype(np.int16)
    green = rgb[..., 1].astype(np.int16)
    blue = rgb[..., 2].astype(np.int16)
    mask = (blue > 100) & (green > 70) & (blue - red > 25) & (green - red > 15)
    rgb[mask] = 255
    return Image.fromarray(rgb, "RGB")


def _char_runs(
    page,
    length: int,
    require_mixed: bool = True,
    require_colored: bool = False,
) -> list[Marker]:
    chars = [c for c in page.chars if re.fullmatch(r"[0-9]", str(c.get("text", "")))]
    chars.sort(key=lambda c: (round(float(c["top"]), 1), float(c["x0"])))
    found = []
    for index in range(len(chars) - length + 1):
        run = chars[index:index + length]
        if max(float(c["top"]) for c in run) - min(float(c["top"]) for c in run) > 2.2:
            continue
        if any(float(run[i + 1]["x0"]) - float(run[i]["x1"]) > 3 for i in range(length - 1)):
            continue
        if length in (2, 3):
            # 0001 같은 긴 번호 안의 '00', '01'을 별도 두 자리 번호로
            # 중복 인식하지 않는다.
            previous = chars[index - 1] if index > 0 else None
            following = chars[index + length] if index + length < len(chars) else None
            joined_left = (
                previous is not None
                and abs(float(previous["top"]) - float(run[0]["top"])) <= 2.2
                and 0 <= float(run[0]["x0"]) - float(previous["x1"]) <= 3
            )
            joined_right = (
                following is not None
                and abs(float(following["top"]) - float(run[-1]["top"])) <= 2.2
                and 0 <= float(following["x0"]) - float(run[-1]["x1"]) <= 3
            )
            if joined_left or joined_right:
                continue
        text = "".join(str(c["text"]) for c in run)
        colors = [is_colored_text(c.get("non_stroking_color")) for c in run]
        if length == 4 and require_mixed and not (any(colors) and not all(colors)):
            continue
        if require_colored and not any(colors):
            continue
        candidate = Marker(text, min(float(c["x0"]) for c in run), min(float(c["top"]) for c in run),
                           max(float(c["x1"]) for c in run), max(float(c["bottom"]) for c in run))
        if not any(abs(m.x0 - candidate.x0) < 2 and abs(m.top - candidate.top) < 2 for m in found):
            found.append(candidate)
    return found


def markers(page) -> list[Marker]:
    # 중학판: 회색 세 자리 + 색상 한 자리. 고등판: 색상 세 자리.
    # 두 판본 모두 색상이 들어간 두 자리 번호도 문제 시작점으로 허용한다.
    found = _char_runs(page, 4)
    found.extend(_char_runs(page, 3, require_mixed=False, require_colored=True))
    found.extend(_char_runs(page, 2, require_mixed=False, require_colored=True))
    for word in page.extract_words(use_text_flow=True, x_tolerance=2, y_tolerance=2,
                                   extra_attrs=["non_stroking_color", "size"]):
        text = str(word["text"]).strip()
        if (re.fullmatch(r"\d{2,3}", text) and float(word.get("size", 0)) >= 12
                and is_colored_text(word.get("non_stroking_color"))):
            found.append(Marker(text, float(word["x0"]), float(word["top"]),
                                float(word["x1"]), float(word["bottom"])))
    # 쪽수·정답 쪽 안내 같은 고립 번호를 제외하고 가장 긴 근접 번호군을 선택한다.
    unique = []
    for marker in found:
        if not any(abs(m.x0 - marker.x0) < 2 and abs(m.top - marker.top) < 2 for m in unique):
            unique.append(marker)
    # 같은 인쇄 번호에서 만들어진 짧은 부분 번호를 제거한다.
    # 예: 001과 같은 위치에서 함께 잡힌 01은 001만 남긴다.
    unique = [
        marker for marker in unique
        if not any(
            len(other.number) > len(marker.number)
            and abs(other.top - marker.top) <= 2.2
            and other.x0 - 2 <= marker.x0
            and marker.x1 <= other.x1 + 2
            for other in unique
        )
    ]
    if not unique:
        return []
    ordered = sorted(unique, key=lambda m: int(m.number))
    groups = [[ordered[0]]]
    for marker in ordered[1:]:
        if int(marker.number) - int(groups[-1][-1].number) <= 3:
            groups[-1].append(marker)
        else:
            groups.append([marker])
    selected = max(groups, key=len)
    return sorted(selected, key=lambda m: (m.top, m.x0)) if len(selected) >= 2 else []


def words(page):
    return page.extract_words(use_text_flow=False, x_tolerance=2, y_tolerance=2)


def word_four_markers(page) -> list[Marker]:
    """색상 경계 때문에 '00'+'10'처럼 나뉜 네 자리 번호를 다시 합친다."""
    tokens = [w for w in words(page) if re.fullmatch(r"[0-9]{1,4}", str(w["text"]).strip())]
    tokens.sort(key=lambda w: (round(float(w["top"]), 1), float(w["x0"])))
    result = []
    for index, first in enumerate(tokens):
        text = ""
        run = []
        for token in tokens[index:index + 4]:
            if run and (abs(float(token["top"]) - float(run[-1]["top"])) > 1.2
                        or float(token["x0"]) - float(run[-1]["x1"]) > 2.0
                        or float(token["x0"]) < float(run[-1]["x0"])):
                break
            text += str(token["text"]).strip()
            run.append(token)
            if len(text) == 4:
                result.append(Marker(text, float(run[0]["x0"]), min(float(w["top"]) for w in run),
                                     float(run[-1]["x1"]), max(float(w["bottom"]) for w in run)))
                break
            if len(text) > 4:
                break
    return result


def regular(
    page,
    rendered: Image.Image,
    output: Path,
    page_no: int,
    serial_start: int = 0,
) -> list[Path]:
    ms, columns, split = assign_columns(markers(page), page.width)
    ws = words(page)
    made, serial = [], serial_start
    for column in range(columns):
        left = 55 if column == 0 else split + 5
        right = page.width - 55 if columns == 1 or column == 1 else split - 5
        cms = sorted((m for m in ms if m.column == column), key=lambda m: m.top)
        for index, marker in enumerate(cms):
            end = cms[index + 1].top - 5 if index + 1 < len(cms) else page.height - 90
            solution_tops = [float(w["top"]) for w in ws if left <= float(w["x0"]) < right
                             and marker.bottom < float(w["top"]) < end and norm(w["text"]) == "풀이"]
            if solution_tops:
                end = min(solution_tops) - 4
            box = tight_box(page, left, right, marker.top - 4, end)
            if box is None:
                continue
            serial += 1
            made.append(save(remove_teacher_blue(crop(page, rendered, box)), output, page_no, serial))
    return made


def type_examples(page, rendered: Image.Image, output: Path, page_no: int) -> list[Path]:
    """'유형 01'처럼 시작하는 대표 유형에서 풀이·답 전까지만 추출한다."""
    ws = words(page)
    headings = []
    for label in ws:
        if norm(label["text"]) != "유형":
            continue
        nearby_numbers = [
            word for word in ws
            if re.fullmatch(r"\d{2}", norm(word["text"]))
            and 0 <= float(word["x0"]) - float(label["x1"]) <= 12
            and abs(float(word["top"]) - float(label["top"])) <= 4
        ]
        if nearby_numbers:
            number = min(nearby_numbers, key=lambda word: float(word["x0"]))
            headings.append((label, norm(number["text"])))

    headings.sort(key=lambda item: (float(item[0]["x0"]) >= page.width / 2, float(item[0]["top"])))
    made = []
    for serial, (heading, number) in enumerate(headings, 1):
        column = int(float(heading["x0"]) >= page.width / 2)
        left = 55 if column == 0 else page.width / 2 + 5
        right = page.width / 2 - 5 if column == 0 else page.width - 55
        top = float(heading["top"]) - 4
        later_heading_tops = [
            float(other["top"]) - 5 for other, _number in headings
            if int(float(other["x0"]) >= page.width / 2) == column
            and float(other["top"]) > float(heading["top"]) + 4
        ]
        hard_end = min(later_heading_tops or [page.height - 90])
        stop_tops = [
            float(word["top"]) - 4 for word in ws
            if left <= float(word["x0"]) < right
            and float(heading["bottom"]) < float(word["top"]) < hard_end
            and norm(word["text"]) in ("풀이", "답")
        ]
        end = min(stop_tops or [hard_end])
        box = tight_box(page, left, right, top, end)
        if box is None:
            continue
        image = remove_teacher_blue(crop(page, rendered, box))
        made.append(save(image, output, page_no, serial, f"_유형{number}"))
    return made


def ranges(page):
    result = []
    for word in words(page):
        match = re.fullmatch(r"\[(\d{4})[~～-](\d{4})\]", norm(word["text"]))
        if match:
            result.append((word, int(match.group(1)), int(match.group(2))))
    return sorted(result, key=lambda item: (float(item[0]["x0"]) >= page.width / 2, float(item[0]["top"])))


def concept_practice(page, rendered: Image.Image, output: Path, page_no: int) -> list[Path]:
    groups = ranges(page)
    range_words = [item[0] for item in groups]
    # 단어 단위 복원을 우선해 수식 숫자가 끼어든 잘못된 문자 조합을 덮어쓴다.
    concept_candidates = word_four_markers(page) + markers(page) + _char_runs(page, 4, require_mixed=False)
    deduplicated = []
    for marker in concept_candidates:
        if not any(abs(m.x0 - marker.x0) < 2 and abs(m.top - marker.top) < 2 for m in deduplicated):
            deduplicated.append(marker)
    ms = [m for m in deduplicated if len(m.number) == 4 and not any(
        float(w["x0"]) - 2 <= m.x0 <= float(w["x1"]) + 2
        and float(w["top"]) - 2 <= m.top <= float(w["bottom"]) + 2 for w in range_words)]
    made, serial = [], 0
    for range_word, start, finish in groups:
        col = int(float(range_word["x0"]) >= page.width / 2)
        col_left = 55 if col == 0 else page.width / 2 + 5
        col_right = page.width / 2 - 5 if col == 0 else page.width - 55
        members = [m for m in ms if start <= int(m.number) <= finish]
        if not members:
            continue
        first_top = min(m.top for m in members)
        prompt_box = tight_box(page, col_left, col_right, float(range_word["top"]) - 3, first_top - 4)
        if prompt_box is None:
            continue
        header = crop(page, rendered, prompt_box)
        xs = sorted({round(m.x0, 1) for m in members})
        two_inner_columns = len(xs) >= 2 and max(xs) - min(xs) > (col_right - col_left) * .22
        split = (min(xs) + max(xs)) / 2 if two_inner_columns else col_right
        for marker in sorted(members, key=lambda m: int(m.number)):
            inner = int(two_inner_columns and marker.x0 >= split)
            left = col_left if inner == 0 else split
            right = split if two_inner_columns and inner == 0 else col_right
            same_lane = sorted([m for m in members if int(two_inner_columns and m.x0 >= split) == inner],
                               key=lambda m: m.top)
            position = same_lane.index(marker)
            end = (same_lane[position + 1].top - 5 if position + 1 < len(same_lane)
                   else _next_range_top(groups, page.width / 2, col, float(range_word["top"]), page.height - 90))
            body_box = tight_box(page, left, right, marker.top - 4, end)
            if body_box is None:
                continue
            serial += 1
            combined = stack(header, crop(page, rendered, body_box), left_align=True)
            combined = remove_teacher_blue(combined)
            made.append(save(combined, output, page_no, serial, "_통합발문"))
    return made


def _next_range_top(groups, mid: float, col: int, current_top: float, fallback: float) -> float:
    later = [float(word["top"]) - 5 for word, _s, _e in groups
             if int(float(word["x0"]) >= mid) == col and float(word["top"]) > current_top]
    return min(later or [fallback])


def extract(source: Path, output: Path, scale: float = 3.0) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    renderer = pdfium.PdfDocument(str(source))
    made = []
    try:
        with pdfplumber.open(source) as document:
            for index, page in enumerate(document.pages):
                # 전체 교재의 앞부속은 제외하되, 문제 페이지만 떼어 낸 발췌 PDF는 허용한다.
                if len(document.pages) > 7 and index < 7:
                    continue
                text = norm(page.extract_text() or "")
                # 해설부는 문제 번호를 반복하므로 본문 문제로 오인하지 않는다.
                # 본문 상단에도 '정답과 풀이 ○쪽' 안내가 있지만 본문에는 '유형 01'이
                # 함께 있다. 실제 해설부는 '본문 ○쪽'을 표시하고 유형 표제가 없다.
                if "정답과풀이해설" in text or ("정답과풀이" in text and "본문" in text and "유형" not in text):
                    continue
                current = []
                if ranges(page):
                    current = concept_practice(page, renderer[index].render(scale=scale).to_pil().convert("RGB"), output, index + 1)
                    kind = "concept"
                else:
                    page_markers = markers(page)
                    has_type_examples = bool(re.search(r"유형\s*\d{2}", page.extract_text() or ""))
                    if not page_markers and not has_type_examples:
                        continue
                    rendered = renderer[index].render(scale=scale).to_pil().convert("RGB")
                    examples = type_examples(page, rendered, output, index + 1) if has_type_examples else []
                    exercises = regular(page, rendered, output, index + 1, serial_start=len(examples)) if page_markers else []
                    current = examples + exercises
                    kind = "type+regular" if examples and exercises else "type" if examples else "regular"
                made.extend(current)
                print(f"page {index + 1}: {kind}, {len(current)} image(s)")
    finally:
        renderer.close()
    return sorted(made)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=Path("output/라이트유형"))
    parser.add_argument("--scale", type=float, default=3.0)
    args = parser.parse_args()
    results = extract(args.pdf, args.output, args.scale)
    print(f"done: {len(results)} image(s)")
