"""수학 문제집 PDF를 교재별 규칙으로 이미지 ZIP으로 변환하는 웹 앱."""

from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path

import streamlit as st

from pungsanja_extractor import extract as extract_numbered
from 반복수학_extractor import extract as extract_repeat_math
from 개념완성_extractor import extract as extract_concept_complete
from 필수유형_extractor import extract as extract_essential_types
from 라이트유형_extractor import extract as extract_light_types
from 테스트북_extractor import extract as extract_test_book
from pptx_exporter import export_pptx
from ppt_dark_converter import convert_pptx_mode
from pdf_dark_converter import convert_pdf_outputs


BOOKS = {
    "풍산자": {
        "description": "색이 적용된 세 자리 번호를 기준으로 문제·도형·내부 박스를 보존합니다.",
        "badge": "3자리 색상 번호",
    },
    "최고난도": {
        "description": "색이 적용된 두 자리 번호를 기준으로 선택지·그래프·조건 박스를 포함합니다.",
        "badge": "2자리 색상 번호",
    },
    "반복수학": {
        "description": "소문항을 나누고 첫 소문항에만 공통 발문을 포함합니다.",
        "badge": "소문항 자동 분리",
    },
    "개념완성": {
        "description": "예제·유제·유형·단계형 서술 문제를 유형별 규칙으로 추출합니다.",
        "badge": "페이지 유형 자동 분석",
    },
    "필수유형": {
        "description": "회색·색상 혼합 세 자리 번호와 대표·실전 서술형의 발문/step을 분리합니다.",
        "badge": "혼합 색상 번호·서술형",
    },
    "라이트유형": {
        "description": "색상 혼합 번호를 찾아 1열·2열 문항을 구분하고 통합 발문을 각 문항에 포함합니다.",
        "badge": "4자리 혼합 번호·통합 발문",
    },
    "테스트북": {
        "description": "회색·색상 혼합 두 자리 번호를 기준으로 2단 문항과 도형·보기·선택지를 보존합니다.",
        "badge": "2자리 혼합 번호·2단 배치",
    },
}


def load_styles() -> None:
    st.markdown(
        """
        <style>
        :root { --ink:#172033; --muted:#657086; --line:#dce3ef; --accent:#2563eb; }
        .stApp { background: linear-gradient(180deg,#f7f9fd 0,#ffffff 42%); color:var(--ink); }
        .block-container { max-width: 980px; padding-top: 3.4rem; padding-bottom: 4rem; }
        .hero-kicker { color:#2563eb; font-size:.82rem; font-weight:800; letter-spacing:.12em; }
        .hero-title { font-size:clamp(2.2rem,6vw,4.4rem); font-weight:900; line-height:1.04; letter-spacing:-.055em; margin:.7rem 0 1rem; }
        .hero-copy { color:var(--muted); font-size:1.05rem; line-height:1.72; max-width:680px; margin-bottom:2.2rem; }
        .section-label { font-size:.82rem; font-weight:800; color:#536078; letter-spacing:.08em; margin-bottom:.8rem; }
        div[data-testid="stHorizontalBlock"] .stButton>button[kind="secondary"] { background:rgba(255,255,255,.9); color:var(--ink); border:1px solid var(--line); }
        div[data-testid="stHorizontalBlock"] .stButton>button { min-height:3.4rem; font-size:1rem; }
        .work-panel { margin-top:1.4rem; padding:1.35rem; border:1px solid var(--line); border-radius:22px; background:#fff; box-shadow:0 18px 50px rgba(36,55,88,.08); }
        .result-box { margin-top:1rem; padding:1rem 1.1rem; border-radius:14px; background:#effbf5; color:#17643d; font-weight:700; }
        .footer-note { color:#7b8497; font-size:.78rem; margin-top:2.2rem; text-align:center; }
        div[data-testid="stFileUploader"] { border:1px dashed #b9c6db; border-radius:15px; padding:.35rem .6rem; }
        .stButton>button, .stDownloadButton>button { border-radius:12px; min-height:3rem; font-weight:800; width:100%; }
        .stButton>button { background:#1d4ed8; color:white; border:0; }
        @media (max-width:640px) { .block-container{padding-top:2rem}.book-card{min-height:auto}.hero-copy{font-size:.96rem} }
        </style>
        """,
        unsafe_allow_html=True,
    )


def zip_images(paths: list[Path]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            if path.is_file() and path.suffix.lower() == ".png":
                archive.write(path, path.name)
    return buffer.getvalue()


def run_extractor(book: str, pdf_path: Path, output_dir: Path) -> list[Path]:
    if book == "풍산자":
        extract_numbered(pdf_path, output_dir, profile="pungsanja")
    elif book == "최고난도":
        extract_numbered(pdf_path, output_dir, profile="최고난도")
    elif book == "반복수학":
        extract_repeat_math(pdf_path, output_dir)
    elif book == "개념완성":
        extract_concept_complete(pdf_path, output_dir)
    elif book == "필수유형":
        extract_essential_types(pdf_path, output_dir)
    elif book == "라이트유형":
        extract_light_types(pdf_path, output_dir)
    elif book == "테스트북":
        extract_test_book(pdf_path, output_dir)
    return sorted(output_dir.glob("*.png"))


st.set_page_config(page_title="문제만", page_icon="✦", layout="centered")
load_styles()

st.markdown('<div class="hero-kicker">MATH WORKBOOK EXTRACTOR</div>', unsafe_allow_html=True)
st.markdown('<div class="hero-title">문제만,<br>깔끔하게 꺼내세요.</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="hero-copy">PDF를 올리고 교재를 선택하면 문제 단위 PNG를 자동으로 만들고 ZIP과 PowerPoint로 정리합니다. 미리보기 없이 처리해 250~300쪽 교재도 안정적으로 다루는 구성을 목표로 합니다.</div>',
    unsafe_allow_html=True,
)

st.markdown('<div class="section-label">지원 교재</div>', unsafe_allow_html=True)
if "selected_book" not in st.session_state:
    st.session_state["selected_book"] = "풍산자"
columns = st.columns(len(BOOKS))
for column, (name, info) in zip(columns, BOOKS.items()):
    with column:
        if st.button(
            name,
            key=f"select_{name}",
            type="primary" if st.session_state["selected_book"] == name else "secondary",
            use_container_width=True,
        ):
            st.session_state["selected_book"] = name
            st.rerun()

st.markdown('<div class="work-panel">', unsafe_allow_html=True)
st.markdown('<div class="section-label">PDF 추출하기</div>', unsafe_allow_html=True)
book = st.session_state["selected_book"]
st.caption(f"선택한 교재 · {book}")
uploaded = st.file_uploader("문제집 PDF", type=("pdf",), accept_multiple_files=False)

# 교재 또는 업로드 파일이 바뀌면 이전 실행의 ZIP을 절대 다시 보여주지 않는다.
input_signature = (book, uploaded.name, uploaded.size) if uploaded is not None else (book, None, 0)
if st.session_state.get("input_signature") != input_signature:
    for key in (
        "result_zip", "result_pptx",
        "result_count", "result_name", "result_pptx_name",
        "result_source",
    ):
        st.session_state.pop(key, None)
    st.session_state["input_signature"] = input_signature

if st.button("문제 이미지 만들기", type="primary", disabled=uploaded is None):
    if uploaded is not None:
        try:
            with st.status("PDF를 분석하고 있습니다…", expanded=True) as status:
                with tempfile.TemporaryDirectory(prefix="math-extractor-") as temporary:
                    work = Path(temporary)
                    source = work / "source.pdf"
                    source.write_bytes(uploaded.getvalue())
                    output = work / "images"
                    status.write("교재 규칙으로 문제 영역을 찾는 중입니다.")
                    paths = run_extractor(book, source, output)
                    if not paths:
                        raise RuntimeError("인식된 문제가 없습니다. 선택한 교재가 맞는지 확인해 주세요.")
                    status.write("일반 이미지를 ZIP으로 정리하는 중입니다.")
                    st.session_state["result_zip"] = zip_images(paths)
                    status.write("일반 16:9 PowerPoint를 만드는 중입니다.")
                    normal_ppt_work = work / "ppt_normal"
                    normal_ppt_work.mkdir()
                    st.session_state["result_pptx"] = export_pptx(paths, normal_ppt_work, dark_mode=False)

                    st.session_state["result_count"] = len(paths)
                    st.session_state["result_name"] = f"{book}_문제이미지.zip"
                    st.session_state["result_pptx_name"] = f"{book}_문제이미지.pptx"
                    st.session_state["result_source"] = uploaded.name
                    status.update(label=f"{len(paths)}개 문제 이미지가 준비됐습니다.", state="complete", expanded=False)
        except Exception as error:
            for key in ("result_zip", "result_pptx"):
                st.session_state.pop(key, None)
            st.error(f"추출 중 문제가 생겼습니다: {error}")

if "result_zip" in st.session_state:
    st.markdown(
        f'<div class="result-box">완료 · {st.session_state["result_count"]}개 PNG<br><small>{st.session_state.get("result_source", "")}</small></div>',
        unsafe_allow_html=True,
    )
    zip_column, ppt_column = st.columns(2)
    with zip_column:
        st.download_button(
            "이미지 ZIP 다운로드",
            data=st.session_state["result_zip"],
            file_name=st.session_state["result_name"],
            mime="application/zip",
            use_container_width=True,
        )
    with ppt_column:
        st.download_button(
            "PowerPoint 다운로드",
            data=st.session_state["result_pptx"],
            file_name=st.session_state["result_pptx_name"],
            mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            use_container_width=True,
        )
st.markdown("</div>", unsafe_allow_html=True)

st.markdown('<div class="work-panel">', unsafe_allow_html=True)
st.markdown('<div class="section-label">PDF · PPT 다크 모드 변환</div>', unsafe_allow_html=True)
st.caption("PDF 또는 이미지 기반 PowerPoint를 올리고 변환 방향을 선택하세요. PDF는 변환된 PDF와 PPT를 모두 제공합니다.")
dark_ppt_upload = st.file_uploader(
    "PDF 또는 PowerPoint",
    type=("pdf", "pptx"),
    accept_multiple_files=False,
    key="dark_ppt_upload",
)
dark_ppt_signature = (
    dark_ppt_upload.name,
    dark_ppt_upload.size,
) if dark_ppt_upload is not None else (None, 0)
if st.session_state.get("dark_ppt_signature") != dark_ppt_signature:
    for key in ("converted_dark_pptx", "converted_dark_pptx_name", "converted_dark_pdf", "converted_dark_pdf_name", "converted_mode_name"):
        st.session_state.pop(key, None)
    st.session_state["dark_ppt_signature"] = dark_ppt_signature

dark_button, light_button = st.columns(2)
with dark_button:
    make_dark = st.button("다크 모드로 변환", disabled=dark_ppt_upload is None, type="primary")
with light_button:
    make_light = st.button("라이트 모드로 변환", disabled=dark_ppt_upload is None, type="secondary")

if make_dark or make_light:
    if dark_ppt_upload is not None:
        try:
            with st.spinner("페이지 이미지와 배경을 변환하고 있습니다…"):
                stem = Path(dark_ppt_upload.name).stem
                target_mode = "dark" if make_dark else "light"
                mode_name = "다크모드" if target_mode == "dark" else "라이트모드"
                if Path(dark_ppt_upload.name).suffix.lower() == ".pdf":
                    with tempfile.TemporaryDirectory(prefix=f"{target_mode}-pdf-") as temporary:
                        converted_pdf, converted_pptx = convert_pdf_outputs(
                            dark_ppt_upload.getvalue(), Path(temporary), target_mode
                        )
                    st.session_state["converted_dark_pdf"] = converted_pdf
                    st.session_state["converted_dark_pdf_name"] = f"{stem}_{mode_name}.pdf"
                    st.session_state["converted_dark_pptx"] = converted_pptx
                else:
                    st.session_state.pop("converted_dark_pdf", None)
                    st.session_state["converted_dark_pptx"] = convert_pptx_mode(dark_ppt_upload.getvalue(), target_mode)
                st.session_state["converted_dark_pptx_name"] = f"{stem}_{mode_name}.pptx"
                st.session_state["converted_mode_name"] = mode_name
        except Exception as error:
            for key in ("converted_dark_pptx", "converted_dark_pdf"):
                st.session_state.pop(key, None)
            st.error(f"다크 모드 변환 중 문제가 생겼습니다: {error}")

if "converted_dark_pptx" in st.session_state:
    st.success(f'{st.session_state.get("converted_mode_name", "변환")} 파일이 준비됐습니다.')
    download_columns = st.columns(2) if "converted_dark_pdf" in st.session_state else [st.container()]
    if "converted_dark_pdf" in st.session_state:
        with download_columns[0]:
            st.download_button(
                f'{st.session_state.get("converted_mode_name", "변환")} PDF 다운로드',
                data=st.session_state["converted_dark_pdf"],
                file_name=st.session_state["converted_dark_pdf_name"],
                mime="application/pdf",
                use_container_width=True,
            )
    with download_columns[-1]:
        st.download_button(
            f'{st.session_state.get("converted_mode_name", "변환")} PowerPoint 다운로드',
            data=st.session_state["converted_dark_pptx"],
            file_name=st.session_state["converted_dark_pptx_name"],
            mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            use_container_width=True,
        )
st.markdown("</div>", unsafe_allow_html=True)
st.markdown('<div class="footer-note">업로드 파일과 추출 결과는 처리 중에만 임시로 사용됩니다.</div>', unsafe_allow_html=True)
