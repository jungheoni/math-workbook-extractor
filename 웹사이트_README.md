# 문제만 웹사이트

교재별 규칙으로 문제 PDF를 처리하고 문제 PNG ZIP과 PowerPoint를 내려받는 Streamlit 웹사이트입니다.

## 가장 쉬운 실행 방법

`웹사이트_실행.bat`를 더블클릭합니다. 잠시 후 웹브라우저에서 `http://127.0.0.1:8501`이 열립니다.

## VS Code 터미널에서 실행

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

## 사용 순서

1. 교재를 선택합니다.
2. PDF를 업로드합니다.
3. `문제 이미지 만들기`를 누릅니다.
4. 처리가 끝나면 이미지 ZIP 또는 PowerPoint를 내려받습니다.

흰 배경의 이미지 기반 PowerPoint는 사이트 하단의 `PPT 다크 모드 변환`에서 별도로 변환할 수 있습니다.

미리보기는 생성하지 않으며 업로드 허용 크기는 1GB입니다.

## 현재 지원 범위

- 풍산자: 색상이 적용된 세 자리 번호
- 최고난도: 색상이 적용된 두 자리 번호
- 반복수학: 중학·고등·파워 샘플 규칙
- 개념완성: 전체 PDF의 페이지 구성을 자동 판별하여 예제·유제·유형·개념 확인·단원 마무리·단계형 서술 문제 추출
- 필수유형: 전체 PDF에서 회색·색상 혼합 세 자리 번호와 대표 서술형·서술형 실전 대비의 발문/step 추출
- 라이트유형: 혼합 색상 4자리, 색상 2·3자리 번호와 유형 문제·통합 발문 추출
- 테스트북: 혼합 색상 2자리 번호와 주관식·서술형 문제 추출

## Streamlit Community Cloud 배포

1. 이 폴더의 배포 파일을 GitHub 저장소에 올립니다.
2. [Streamlit Community Cloud](https://share.streamlit.io/)에서 GitHub 계정으로 로그인합니다.
3. `Create app`을 선택하고 저장소와 브랜치를 지정합니다.
4. Main file path는 `app.py`로 설정합니다.
5. Python 버전은 3.12를 선택하고 배포합니다.

`requirements.txt`와 `.streamlit/config.toml`은 저장소 루트에 그대로 두어야 합니다.
