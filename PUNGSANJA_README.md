# 풍산자 문제 추출기

`pungsanja_extractor.py`는 교재별 프로필에 맞는 색상 문제 번호를 찾아 문제별 PNG로 저장합니다.

## 설치

```powershell
python -m pip install -r requirements.txt
```

## 실행

```powershell
python pungsanja_extractor.py "문제집.pdf" -o "output/pungsanja"
```

최고난도(색이 적용된 두 자리 번호)는 다음처럼 실행합니다.

```powershell
python pungsanja_extractor.py "최고난도 샘플.pdf" --profile 최고난도 -o "output/최고난도"
```

실행 결과:

- `output/pungsanja/`: 문제별 PNG
- `output/pungsanja.zip`: PNG 묶음
- `output/pungsanja_metadata.json`: 문제 번호, 페이지, 추출 좌표
- `output/pungsanja_progress.json`: 자동 재개용 진행 기록

문제 이미지의 파일명은 `쪽p_연번.png` 형식입니다. 각 숫자는 세 자리로 맞춰지므로 파일 탐색기에서 페이지 순서대로 정렬됩니다.

```text
001p_001.png  # PDF 1쪽의 첫 번째 문제
001p_002.png  # PDF 1쪽의 두 번째 문제
002p_001.png  # PDF 2쪽의 첫 번째 문제
```

PDF에 인쇄된 원래 문제 번호는 파일명 대신 `metadata.json`에 저장됩니다.

## 중단 후 재개

처리 도중 오류가 발생하거나 프로그램을 종료했다면 같은 명령을 다시 실행합니다.

```powershell
python pungsanja_extractor.py "문제집.pdf" -o "output/pungsanja"
```

이미 정상적으로 저장된 문제 PNG는 검사 후 건너뛰고, 저장되지 않았거나 손상된 파일부터 다시 처리합니다.
진행 기록에는 원본 PDF의 경로, 크기, 수정 시간이 함께 저장되므로 다른 PDF를 같은 출력 폴더에 실수로 이어 쓰는 것도 방지합니다.

## 적용 규칙

- `pungsanja`: 검정·회색이 아닌 색상이 적용된 세 자리 번호
- `최고난도`: 검정·회색이 아닌 색상이 적용된 두 자리 번호
- CMYK와 RGB 색상 PDF를 모두 지원
- 1단/2단 페이지 자동 구분
- 문제 본문, 수식, 선택지, 내부 검정 박스, 표, 그래프, 도형 보존
- 문제 전체를 감싸는 장식 테두리, 페이지 번호, 단원명, 단 구분선 제외
- `풍산자曰`, 풀이, 해설, 정답, 단계별 풀이가 시작되면 문제 종료
- 콘텐츠 주변에 일정한 흰색 여백 추가

## 조정 옵션

문제 내부의 요소 사이가 매우 넓은 PDF라면 `--max-gap`을 조금 키울 수 있습니다.

```powershell
python pungsanja_extractor.py "문제집.pdf" -o "output/pungsanja" --max-gap 22
```

기본값은 `20`입니다. 값을 너무 크게 지정하면 풀이 영역까지 포함될 수 있습니다.
