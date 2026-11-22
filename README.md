# contest-radar

공모전·지원사업·연구과제 공고를 여러 사이트에서 모아 한 화면으로 보는 도구.

- 모으는 곳: 기업마당, K-Startup, 인천테크노파크, 창조경제혁신센터(전국), 인천 유유기지, 서울 미디어허브, 씽유, 대티즌, 위비티, 씽굿
- 공고 상세와 첨부(hwp·hwpx·pdf·이미지)를 Claude가 읽어 대상·나이·지역·업력·접수기간·분야를 컬럼으로 저장
- 여러 사이트에 올라온 같은 공고는 하나로 묶고 출처만 덧붙임

## 실행

```bash
docker compose up -d          # Postgres (localhost:15434)
uv run collect.py             # 목록 모으기 + 중복 묶기
uv run detail.py              # 상세·첨부 읽기 (claude CLI 필요)
uv run viewer.py              # http://127.0.0.1:8765
```

hwp 읽기에 `uv tool run --from pyhwp hwp5txt`, pdf 읽기에 `pdftotext`(poppler)를 쓴다.
