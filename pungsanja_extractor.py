"""풍산자 문제집 PDF에서 문제만 PNG로 추출한다.

적용 규칙
1. 검정/회색이 아닌 색상이 적용된 세 자리 번호를 문제 시작점으로 사용한다.
2. 1단/2단을 자동 판별하고 같은 단 안에서만 문제 내용을 모은다.
3. 문제 번호부터 이어지는 텍스트, 수식, 보기, 내부 박스, 표, 그래프, 도형을 보존한다.
4. 큰 공백 뒤의 풀이/해설과 다음 문제, 쪽수, 페이지 장식은 제외한다.
5. 문제 전체를 감싸는 장식 테두리는 제외하지만 내부 조건/보기 박스는 보존한다.

이 방식은 OCR이 아니라 PDF 내부의 텍스트, 색상, 벡터 좌표를 사용한다.
"""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image


PROFILES = {
    "pungsanja": {"digits": 3, "label": "풍산자"},
    # 구판은 색상 2자리, 신판은 회색 2자리+색상 1자리의 3자리 번호다.
    "최고난도": {"digits": (3, 2), "label": "최고난도"},
    # 이전 실행 명령과 진행 기록을 위한 호환 별칭
    "choegonado": {"digits": (3, 2), "label": "최고난도"},
}
STOP_WORDS = ("풍산자曰", "풀이", "해설", "정답")
SOLUTION_PATTERNS = (
    re.compile(r"^일단"),
    re.compile(r"\[1단계\]"),
    re.compile(r"나타낸후.*대입"),
)


@dataclass(frozen=True)
class Box:
    x0: float
    top: float
    x1: float
    bottom: float
    kind: str = "content"
    text: str = ""

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.bottom - self.top


@dataclass(frozen=True)
class Marker:
    number: str
    x0: float
    top: float
    x1: float
    bottom: float
    column: int = 0


@dataclass(frozen=True)
class Result:
    number: str
    page: int
    column: int
    box: tuple[float, float, float, float]
    filename: str


def is_colored_text(value: object, saturation_threshold: float = 0.08) -> bool:
    """Return True for chromatic RGB/CMYK colors, but not black/gray/white.

    pdfplumber reports DeviceGray as one component, RGB as three components,
    and CMYK as four components. CMYK is converted approximately to RGB before
    measuring chroma so blue, green, red, orange, and other colored numbers all
    work with the same rule.
    """
    if not isinstance(value, (tuple, list)):
        return False
    try:
        components = tuple(float(component) for component in value)
    except (TypeError, ValueError):
        return False

    if len(components) == 1:
        return False
    if len(components) == 3:
        red, green, blue = components
    elif len(components) == 4:
        cyan, magenta, yellow, black = components
        red = (1.0 - cyan) * (1.0 - black)
        green = (1.0 - magenta) * (1.0 - black)
        blue = (1.0 - yellow) * (1.0 - black)
    else:
        return False

    chroma = max(red, green, blue) - min(red, green, blue)
    return chroma >= saturation_threshold and max(red, green, blue) >= 0.12


def find_markers(page, digits: int = 3) -> list[Marker]:
    number_re = re.compile(rf"[0-9]{{{digits}}}")
    words = page.extract_words(
        use_text_flow=True,
        x_tolerance=2,
        y_tolerance=2,
        extra_attrs=["non_stroking_color"],
    )
    markers = []
    for word in words:
        text = str(word["text"]).strip()
        if number_re.fullmatch(text) and is_colored_text(word.get("non_stroking_color")):
            markers.append(
                Marker(
                    text,
                    float(word["x0"]),
                    float(word["top"]),
                    float(word["x1"]),
                    float(word["bottom"]),
                )
            )
    # Some publishers color each digit with a slightly different CMYK value.
    # pdfplumber then cannot expose the complete number as one uniformly
    # colored word, so also reconstruct markers directly from adjacent chars.
    digit_chars = [
        char for char in page.chars
        if re.fullmatch(r"[0-9]", str(char.get("text", "")))
    ]
    digit_chars.sort(key=lambda char: (round(float(char["top"]), 1), float(char["x0"])))
    runs: list[list[dict]] = []
    for char in digit_chars:
        if (
            not runs
            or abs(float(char["top"]) - float(runs[-1][-1]["top"])) > 2.0
            or float(char["x0"]) < float(runs[-1][-1]["x0"])
            or float(char["x0"]) - float(runs[-1][-1]["x1"]) > 2.5
        ):
            runs.append([char])
        else:
            runs[-1].append(char)

    for run in runs:
        if len(run) != digits:
            continue
        # 최고난도 신판처럼 회색 숫자와 색상 숫자가 섞인 번호도 허용하되,
        # 페이지 번호처럼 전부 무채색인 숫자는 제외한다.
        if not any(is_colored_text(char.get("non_stroking_color")) for char in run):
            continue
        text = "".join(str(char["text"]) for char in run)
        candidate = Marker(
            text,
            min(float(char["x0"]) for char in run),
            min(float(char["top"]) for char in run),
            max(float(char["x1"]) for char in run),
            max(float(char["bottom"]) for char in run),
        )
        if not any(
            existing.number == candidate.number
            and abs(existing.x0 - candidate.x0) < 2
            and abs(existing.top - candidate.top) < 2
            for existing in markers
        ):
            markers.append(candidate)
    return sorted(markers, key=lambda marker: (marker.top, marker.x0))


def assign_columns(markers: list[Marker], page_width: float):
    positions = sorted({marker.x0 for marker in markers})
    gaps = [(positions[i + 1] - positions[i], i) for i in range(len(positions) - 1)]
    largest_gap, gap_index = max(gaps, default=(0.0, 0))
    two_columns = largest_gap > page_width * 0.18
    split_x = (
        (positions[gap_index] + positions[gap_index + 1]) / 2
        if two_columns
        else page_width
    )
    assigned = [
        Marker(
            marker.number,
            marker.x0,
            marker.top,
            marker.x1,
            marker.bottom,
            int(two_columns and marker.x0 >= split_x),
        )
        for marker in markers
    ]
    # Marker clusters classify the markers, but the actual column divider is
    # the page center (marker indentation is not the column boundary).
    column_divider = page_width / 2
    return assigned, (2 if two_columns else 1), column_divider


def group_word_lines(page, left: float, right: float) -> list[Box]:
    words = [
        word
        for word in page.extract_words(use_text_flow=False, x_tolerance=2, y_tolerance=2)
        if left <= (float(word["x0"]) + float(word["x1"])) / 2 < right
    ]
    words.sort(key=lambda word: (float(word["top"]), float(word["x0"])))
    groups: list[list[dict]] = []
    for word in words:
        if not groups or abs(float(word["top"]) - min(float(w["top"]) for w in groups[-1])) > 3.0:
            groups.append([word])
        else:
            groups[-1].append(word)
    return [
        Box(
            min(float(word["x0"]) for word in group),
            min(float(word["top"]) for word in group),
            max(float(word["x1"]) for word in group),
            max(float(word["bottom"]) for word in group),
            "text",
            " ".join(str(word["text"]) for word in sorted(group, key=lambda word: float(word["x0"]))),
        )
        for group in groups
    ]


def graphic_boxes(page, left: float, right: float) -> list[Box]:
    boxes = []
    collections = (
        (getattr(page, "curves", []), "curve"),
        (getattr(page, "rects", []), "rect"),
        (getattr(page, "lines", []), "line"),
        (getattr(page, "images", []), "image"),
    )
    for objects, kind in collections:
        for obj in objects:
            if not all(key in obj for key in ("x0", "x1", "top", "bottom")):
                continue
            box = Box(
                float(obj["x0"]),
                float(obj["top"]),
                float(obj["x1"]),
                float(obj["bottom"]),
                kind,
            )
            center = (box.x0 + box.x1) / 2
            if left <= center < right and box.width > 0.5 and box.height > 0.5:
                boxes.append(box)
    return boxes


def is_stop_line(line: Box, marker: Marker) -> bool:
    if line.top <= marker.bottom + 8:
        return False
    normalized = re.sub(r"\s+", "", line.text)
    return any(word in normalized for word in STOP_WORDS) or any(
        pattern.search(normalized) for pattern in SOLUTION_PATTERNS
    )


def is_decorative_outer_box(box: Box, marker: Marker, column_width: float) -> bool:
    """Outer cards contain the number; inner expression/choice boxes do not."""
    contains_marker_y = box.top <= marker.top + 5 and box.bottom >= marker.bottom + 20
    very_wide = box.width >= column_width * 0.70
    return contains_marker_y and very_wide


def determine_problem_box(
    page,
    marker: Marker,
    next_marker: Marker | None,
    left: float,
    right: float,
    max_gap: float,
    source_padding: float,
) -> tuple[float, float, float, float] | None:
    footer_limit = page.height - 90
    hard_end = min(next_marker.top if next_marker else footer_limit, footer_limit)
    lines = group_word_lines(page, left, right)

    for line in lines:
        if marker.bottom < line.top < hard_end and is_stop_line(line, marker):
            hard_end = line.top
            break
        # 풍산자의 next-section captions are short, right-aligned labels
        # (often preceded by an orange dot drawn as a separate vector object).
        normalized = re.sub(r"\s+", "", line.text)
        has_korean_caption = bool(re.search(r"[가-힣]", normalized))
        right_aligned_caption = (
            line.top > marker.bottom + 8
            and line.x0 > left + (right - left) * 0.70
            # 도형의 꼭짓점명(A, B, C 등)을 다음 코너 제목으로 오인하면
            # 도형 한가운데에서 잘린다. 한글이 포함된 실제 캡션만 종료
            # 신호로 인정하고, 지나치게 짧은 표시는 제외한다.
            and has_korean_caption
            and 4 <= len(normalized) <= 16
        )
        if right_aligned_caption:
            # 렌더링 반올림으로 다음 캡션의 첫 픽셀이 섞이지 않도록 아주
            # 작은 간격만 둔다. 바로 위 도형 라벨은 그대로 보존된다.
            hard_end = line.top - 1.5
            break

    candidates: list[Box] = [
        line for line in lines if marker.top - 3 <= line.top < hard_end
    ]
    column_width = right - left
    decorative_bottoms: list[float] = []
    for box in graphic_boxes(page, left, right):
        if is_decorative_outer_box(box, marker, column_width):
            decorative_bottoms.append(box.bottom)
            continue
        # 도형이 여러 곡선 조각으로 분리된 PDF에서는 객체의 중심점이나
        # 끝점이 탐색 구간 밖에 있어도 실제 선은 문제 영역과 이어질 수 있다.
        # 탐색 구간과 조금이라도 겹치면 객체 전체를 후보로 유지한다.
        if box.bottom < marker.top - 3 or box.top >= hard_end:
            continue
        if box.x0 < left - 2 or box.x1 > right + 2:
            continue
        # Page/column rules are long, thin objects rather than problem content.
        if box.height > (hard_end - marker.top) * 0.85 and box.width < 2:
            continue
        candidates.append(box)

    candidates.sort(key=lambda box: (box.top, box.x0))
    connected: list[Box] = []
    cursor = marker.bottom
    started = False
    for box in candidates:
        if box.bottom < marker.top - 1:
            continue
        if not started:
            if box.top <= marker.bottom + 5:
                connected.append(box)
                cursor = max(cursor, box.bottom)
                started = True
            continue
        if box.top <= cursor + max_gap:
            connected.append(box)
            cursor = max(cursor, box.bottom)
        else:
            break

    if not connected:
        return None

    x0 = max(left, min(box.x0 for box in connected) - source_padding)
    top = max(marker.top - source_padding, min(box.top for box in connected) - source_padding)
    x1 = min(right, max(box.x1 for box in connected) + source_padding)
    bottom = min(hard_end, max(box.bottom for box in connected) + source_padding)
    # A PDF can contain invisible/stray text on a card border. Never let that
    # pull the crop far enough to reveal the decorative outer bottom line.
    if decorative_bottoms:
        bottom = min(bottom, min(decorative_bottoms) - 5.0)
    return (x0, top, x1, bottom)


def pdf_box_to_pixels(page, rendered: Image.Image, box):
    crop_x0, crop_y0, crop_x1, crop_y1 = page.cropbox or page.mediabox
    crop_top = page.height - crop_y1
    sx = rendered.width / (crop_x1 - crop_x0)
    sy = rendered.height / (crop_y1 - crop_y0)
    x0, top, x1, bottom = box
    return (
        max(0, round((x0 - crop_x0) * sx)),
        max(0, round((top - crop_top) * sy)),
        min(rendered.width, round((x1 - crop_x0) * sx)),
        min(rendered.height, round((bottom - crop_top) * sy)),
    )


def add_margin(image: Image.Image, margin: int) -> Image.Image:
    result = Image.new("RGB", (image.width + 2 * margin, image.height + 2 * margin), "white")
    result.paste(image.convert("RGB"), (margin, margin))
    return result


def atomic_json_write(path: Path, data: object) -> None:
    temporary = path.with_name(path.name + ".part")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
    temporary.replace(path)


def source_signature(pdf_path: Path) -> dict[str, object]:
    stat = pdf_path.stat()
    return {
        "path": str(pdf_path.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def result_key(page: int, column: int, number: str) -> str:
    return f"{page}:{column}:{number}"


def valid_png(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        with Image.open(path) as image:
            image.verify()
        return True
    except Exception:
        return False


def atomic_png_save(image: Image.Image, destination: Path) -> None:
    temporary = destination.with_name(destination.name + ".part")
    image.save(temporary, format="PNG", optimize=True)
    temporary.replace(destination)


def extract(
    pdf_path: Path,
    output_dir: Path,
    scale: float = 3.0,
    max_gap: float = 20.0,
    source_padding: float = 1.5,
    pixel_margin: int = 24,
    profile: str = "pungsanja",
) -> list[Result]:
    if profile not in PROFILES:
        raise ValueError(f"알 수 없는 교재 프로필: {profile}")
    profile_settings = PROFILES[profile]
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(pdf_path))
    progress_path = output_dir.parent / f"{output_dir.name}_progress.json"
    metadata_path = output_dir.parent / f"{output_dir.name}_metadata.json"
    zip_path = output_dir.parent / f"{output_dir.name}.zip"
    signature = source_signature(pdf_path)
    settings = {"profile": profile, "digits": profile_settings["digits"], "scale": scale, "max_gap": max_gap}
    records: dict[str, dict] = {}

    if progress_path.is_file():
        with progress_path.open("r", encoding="utf-8") as stream:
            previous = json.load(stream)
        if previous.get("source") != signature or previous.get("settings") != settings:
            raise RuntimeError(
                "출력 폴더에 다른 PDF의 진행 기록이 있습니다. "
                "새 출력 폴더를 지정해 주세요."
            )
        records = dict(previous.get("results", {}))

    def save_progress(complete: bool, last_page: int) -> None:
        atomic_json_write(
            progress_path,
            {
                "source": signature,
                "settings": settings,
                "complete": complete,
                "last_page": last_page,
                "results": records,
            },
        )

    seen_keys: set[str] = set()
    page_total = len(pdf)

    with pdfplumber.open(pdf_path) as document:
        for page_index, page in enumerate(document.pages):
            print(f"[page {page_index + 1}/{page_total}]")
            digit_options = profile_settings["digits"]
            if isinstance(digit_options, int):
                digit_options = (digit_options,)
            marker_candidates = [
                marker
                for digit_count in digit_options
                for marker in find_markers(page, int(digit_count))
            ]
            # 001과 같은 3자리 번호 내부에서 01을 별도 문제로 중복 인식하지 않는다.
            markers = [
                marker for marker in marker_candidates
                if not any(
                    len(other.number) > len(marker.number)
                    and abs(other.top - marker.top) <= 2.2
                    and other.x0 - 2 <= marker.x0
                    and marker.x1 <= other.x1 + 2
                    for other in marker_candidates
                )
            ]
            markers, column_count, split_x = assign_columns(markers, page.width)
            if not markers:
                save_progress(False, page_index + 1)
                continue
            crop_x0, _crop_y0, crop_x1, _crop_y1 = page.cropbox or page.mediabox
            rendered: Image.Image | None = None
            page_serial = 0

            for column in range(column_count):
                left = crop_x0 if column_count == 1 or column == 0 else split_x
                right = crop_x1 if column_count == 1 or column == 1 else split_x
                column_markers = sorted(
                    (marker for marker in markers if marker.column == column),
                    key=lambda marker: marker.top,
                )
                for index, marker in enumerate(column_markers):
                    next_marker = column_markers[index + 1] if index + 1 < len(column_markers) else None
                    box = determine_problem_box(
                        page,
                        marker,
                        next_marker,
                        left,
                        right,
                        max_gap,
                        source_padding,
                    )
                    if box is None:
                        print(f"warning: could not determine problem {marker.number} on page {page_index + 1}")
                        continue
                    page_serial += 1
                    filename = f"{page_index + 1:03d}p_{page_serial:03d}.png"
                    destination = output_dir / filename
                    result = Result(marker.number, page_index + 1, column, box, filename)
                    key = result_key(result.page, result.column, result.number)
                    seen_keys.add(key)

                    if valid_png(destination):
                        print(f"resumed: skipped {filename}")
                    else:
                        if rendered is None:
                            rendered = pdf[page_index].render(scale=scale).to_pil().convert("RGB")
                        image = rendered.crop(pdf_box_to_pixels(page, rendered, box))
                        image = add_margin(image, pixel_margin)
                        atomic_png_save(image, destination)
                        print(f"saved {filename}")

                    records[key] = asdict(result)

            # One atomic checkpoint per page keeps large 300-page books fast.
            # If a crash occurs mid-page, existing valid PNGs are still found
            # and skipped automatically on the next run.
            save_progress(False, page_index + 1)

    records = {key: value for key, value in records.items() if key in seen_keys}
    results = [Result(**record) for record in records.values()]
    results.sort(key=lambda result: (result.page, result.column, result.box[1], result.number))
    atomic_json_write(metadata_path, [asdict(result) for result in results])

    temporary_zip = zip_path.with_name(zip_path.name + ".part")
    with zipfile.ZipFile(temporary_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        for result in results:
            path = output_dir / result.filename
            if valid_png(path):
                archive.write(path, path.name)
    temporary_zip.replace(zip_path)
    save_progress(True, page_total)
    pdf.close()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="문제집 PDF 파일")
    parser.add_argument("-o", "--output", type=Path, default=Path("output/pungsanja"))
    parser.add_argument(
        "--profile",
        choices=tuple(PROFILES),
        default="pungsanja",
        help="교재별 문제 번호 규칙 (최고난도는 --profile 최고난도)",
    )
    parser.add_argument("--scale", type=float, default=3.0, help="렌더링 배율")
    parser.add_argument("--max-gap", type=float, default=20.0, help="같은 문제로 연결할 최대 세로 간격")
    args = parser.parse_args()
    if not args.pdf.is_file():
        parser.error(f"PDF를 찾을 수 없습니다: {args.pdf}")
    results = extract(
        args.pdf,
        args.output,
        scale=args.scale,
        max_gap=args.max_gap,
        profile=args.profile,
    )
    print(f"done: {len(results)} problem(s) extracted")


if __name__ == "__main__":
    main()
