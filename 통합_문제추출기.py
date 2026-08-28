# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import threading
import traceback
import zipfile
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from pungsanja_extractor import extract as extract_numbered
    from 반복수학_extractor import extract as extract_repeat_math
    from 개념완성_extractor import extract as extract_concept_complete
    from 필수유형_extractor import extract as extract_essential_types
    from 라이트유형_extractor import extract as extract_light_types
    from 테스트북_extractor import extract as extract_test_book
    from pptx_exporter import export_pptx
except ImportError as exc:
    raise SystemExit(
        "필요한 추출기 파일을 찾지 못했습니다. "
        "이 파일을 app.py와 같은 폴더에 넣고 실행해 주세요.\n"
        f"원인: {exc}"
    ) from exc

BOOKS = (
    "풍산자", "최고난도", "반복수학", "개념완성",
    "필수유형", "라이트유형", "테스트북"
)

def run_extractor(book: str, pdf_path: Path, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
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
    else:
        raise ValueError(f"지원하지 않는 교재입니다: {book}")
    return sorted(output_dir.glob("*.png"))

def make_zip(image_paths: list[Path], destination: Path) -> None:
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(image_paths):
            if path.is_file() and path.suffix.lower() == ".png":
                archive.write(path, arcname=path.name)

def make_pptx(image_paths: list[Path], work_dir: Path, destination: Path) -> None:
    destination.write_bytes(export_pptx(image_paths, work_dir))

class ExtractorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("수학 문제집 통합 추출기")
        self.geometry("680x500")
        self.pdf_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str(Path.cwd() / "output"))
        self.book_var = tk.StringVar(value="풍산자")
        self.zip_var = tk.BooleanVar(value=True)
        self.ppt_var = tk.BooleanVar(value=True)
        self.build_ui()

    def build_ui(self):
        c = ttk.Frame(self, padding=20)
        c.pack(fill="both", expand=True)

        ttk.Label(c, text="수학 문제집 통합 추출기", font=("", 18, "bold")).pack(anchor="w")
        ttk.Label(c, text="교재를 선택하고 PDF를 넣으면 문제별 PNG·ZIP·PPT를 생성합니다.").pack(anchor="w", pady=(4, 18))

        f = ttk.Frame(c); f.pack(fill="x", pady=6)
        ttk.Label(f, text="교재", width=12).pack(side="left")
        ttk.Combobox(f, textvariable=self.book_var, values=BOOKS, state="readonly").pack(side="left", fill="x", expand=True)

        f = ttk.Frame(c); f.pack(fill="x", pady=6)
        ttk.Label(f, text="PDF 파일", width=12).pack(side="left")
        ttk.Entry(f, textvariable=self.pdf_var).pack(side="left", fill="x", expand=True)
        ttk.Button(f, text="찾기", command=self.choose_pdf).pack(side="left", padx=(8,0))

        f = ttk.Frame(c); f.pack(fill="x", pady=6)
        ttk.Label(f, text="출력 폴더", width=12).pack(side="left")
        ttk.Entry(f, textvariable=self.output_var).pack(side="left", fill="x", expand=True)
        ttk.Button(f, text="찾기", command=self.choose_output).pack(side="left", padx=(8,0))

        opts = ttk.LabelFrame(c, text="생성할 파일", padding=12)
        opts.pack(fill="x", pady=(14,10))
        ttk.Checkbutton(opts, text="문제 이미지 ZIP", variable=self.zip_var).pack(side="left", padx=(0,20))
        ttk.Checkbutton(opts, text="PowerPoint (.pptx)", variable=self.ppt_var).pack(side="left")

        self.run_button = ttk.Button(c, text="문제 추출 시작", command=self.start)
        self.run_button.pack(fill="x", pady=(10,12), ipady=8)

        self.status = tk.Text(c, height=12, wrap="word")
        self.status.pack(fill="both", expand=True)
        self.log("준비되었습니다.")

    def choose_pdf(self):
        p = filedialog.askopenfilename(title="PDF 선택", filetypes=[("PDF", "*.pdf")])
        if p: self.pdf_var.set(p)

    def choose_output(self):
        p = filedialog.askdirectory(title="출력 폴더 선택")
        if p: self.output_var.set(p)

    def log(self, msg: str):
        self.status.insert("end", msg + "\n")
        self.status.see("end")

    def start(self):
        pdf = Path(self.pdf_var.get().strip())
        if not pdf.is_file():
            messagebox.showerror("오류", "PDF 파일을 선택해 주세요.")
            return
        self.run_button.configure(state="disabled")
        threading.Thread(target=self.worker, args=(pdf,), daemon=True).start()

    def worker(self, pdf: Path):
        try:
            book = self.book_var.get()
            base = Path(self.output_var.get().strip())
            stem = "".join(c if c not in '<>:"/\\|?*' else "_" for c in pdf.stem)
            job_dir = base / book / stem
            image_dir = job_dir / "images"
            job_dir.mkdir(parents=True, exist_ok=True)

            self.after(0, self.log, f"교재: {book}")
            self.after(0, self.log, "문제 추출 중...")
            images = run_extractor(book, pdf, image_dir)
            if not images:
                raise RuntimeError("추출된 문제가 없습니다.")

            self.after(0, self.log, f"PNG {len(images)}개 생성 완료")

            if self.zip_var.get():
                make_zip(images, job_dir / "문제이미지.zip")
                self.after(0, self.log, "ZIP 생성 완료")

            if self.ppt_var.get():
                make_pptx(images, job_dir, job_dir / "문제모음.pptx")
                self.after(0, self.log, "PPT 생성 완료")

            self.after(0, self.log, f"완료: {job_dir}")
            self.after(0, self.done, str(job_dir))
        except Exception as exc:
            print(traceback.format_exc(), file=sys.stderr)
            self.after(0, self.failed, str(exc))

    def done(self, folder: str):
        self.run_button.configure(state="normal")
        messagebox.showinfo("완료", f"문제 추출이 완료되었습니다.\n\n{folder}")

    def failed(self, msg: str):
        self.run_button.configure(state="normal")
        messagebox.showerror("추출 실패", msg)

if __name__ == "__main__":
    ExtractorApp().mainloop()
