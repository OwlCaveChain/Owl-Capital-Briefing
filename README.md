# Owl Capital Briefing

## 차트 브리핑 (`charts.py`)

6개 차트를 PNG로 그려 텔레그램 sendPhoto로 순서대로 보낸다.

| # | 차트 | 1순위 출처 | 대체 출처 |
|---|------|-----------|-----------|
| 1 | 미 10Y-2Y 스프레드 (2021-01~) | FRED T10Y2Y | Yahoo ^TNX − 2YY=F (근사, 2021-08~) |
| 2 | 미·한·중·일 10년 국채금리 (2020-01~) | 미 FRED DGS10 / 한 FRED IRLTLT01KRM156N(OECD 월평균) / 일 재무성 jgbcme_all.csv + 당월 jgbcme.csv | 미 Yahoo ^TNX / 일 FRED IRLTLT01JPM156N · 중국은 출처 없음 |
| 3 | WTI·브렌트·두바이 (올해 1월~) | FRED DCOILWTICO, DCOILBRENTEU (현물), 두바이 페트로넷 일일 현물 | Yahoo CL=F, BZ=F (선물), 두바이 FRED POILDUBUSDM(IMF 월평균) |
| 4 | 미 가솔린 소매가격 (2022-01~) | FRED GASREGW | 없음 |
| 5 | DTCR(좌)·NVDA(우) (2023-01~) | Yahoo(yfinance) | stooq |
| 6 | IBB·SOX 연초=100 | Yahoo(yfinance) | stooq |

### 실행

```
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python charts.py
```

- `--dry-run` 전송 없이 `out/`에 PNG만 저장, `--no-commit` git 커밋 생략, `--only 1,5` 일부만 실행
- 토큰·채팅ID가 없으면 자동으로 dry-run 처리
- 출처별 재시도 1회, 차트 하나가 실패하면 "○○ 차트 생성 실패: 사유"를 텍스트로 대신 보냄

### data/

받은 시계열은 `data/<출처>_<코드>.csv`(date,value)로 저장하고, 실행 때마다 기존 마지막 날짜 이후 값만 추가한다.
매일 한 줄씩 쌓는 지표는 `append_rows("키", pd.Series([값], index=[pd.Timestamp(날짜)]))`로 추가하면 된다.
실행이 끝나면 data/ 변경분을 커밋하고 원격 기본 브랜치(또는 `CHARTS_DATA_BRANCH`)로 푸시한다. 실패하면 현재 브랜치로 푸시한다.
