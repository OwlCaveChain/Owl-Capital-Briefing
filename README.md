# Owl Capital Briefing

## 아침 브리핑 전체 실행 (`briefing.py`)

```
python briefing.py            # 메시지 1~4 병렬 준비 → 1→2→3→4 전송 → charts.py(메시지 5)
python briefing.py --dry-run  # 전송 없이 out/에 이미지만 저장, 캡션 출력
```

| # | 스크립트 | 내용 | 실패 시 텍스트 |
|---|----------|------|----------------|
| 1 | `fear_greed.py` | Fear & Greed 반원 게이지 + 이전 값 2x2 표, 이어서 1년 타임라인(1-2). 값은 CNN 페이지처럼 소수점 버림 | 게이지 실패 시 캡션을 텍스트로, 타임라인 실패 시 "Fear & Greed 타임라인 확인 실패", 데이터 실패 시 "Fear & Greed 항목 확인 실패" |
| 2 | `blog_feed.py` | 블로그 RSS 최근 24시간 새 글 | "블로그 새 글 없음" / 맨 아래 "확인 실패: 블로그명" |
| 3 | `finviz_heatmap.py` | 핀비즈 S&P 500 히트맵 1일·4주·연초 대비 | "핀비즈 히트맵 확인 실패" |
| 4 | `natgas.py` | 헨리허브 천연가스 선물(NG=F) 2020-12~ | "미 천연가스 차트 확인 실패" |
| 5 | `charts.py` | 차트 6장 (아래) | "○○ 차트 생성 실패: 사유" |

- 각 스크립트는 단독 실행도 된다(`python natgas.py --dry-run`).
- 마지막에 메시지별 전송 결과(message_id 또는 실패 사유)와 실패 항목을 출력한다. 실패 항목이 있으면 종료 코드 1.
- 히트맵은 Playwright + `/opt/pw-browsers/chromium`(없으면 Playwright 기본, `CHROMIUM_PATH`로 변경 가능)을 쓴다.

## 텔레그램 전송 (`telegram_send.py`)

모든 스크립트가 이 모듈로 보낸다. 토큰은 `TELEGRAM_BOT_TOKEN`, 채팅 ID는 `TELEGRAM_CHAT_ID`(없거나 "chat not found"면 기본값 7164046356).
토큰이 없으면 dry-run으로 동작한다. 성공할 때마다 `[telegram] ok message_id=N` 을 출력한다.
전송은 항상 `parse_mode=HTML`(링크 미리보기 끔). `html=False`(기본)면 본문을 자동 이스케이프하고, dry-run은 텔레그램에 보일 글자 그대로 출력한다.

캡션 규칙: 첫 줄 제목, 시리즈마다 한 줄, 출처는 캡션에 쓰지 않고 실행 로그(`[출처]`)에만 남긴다. 날짜는 직전 영업일보다 오래된 값에만 `(9/22)`처럼 붙인다.

```
python telegram_send.py check
python telegram_send.py text "본문"      # 여러 줄: printf '%s' "본문" | python telegram_send.py text -
python telegram_send.py photo 파일.png "캡션"
```

## 차트 브리핑 (`charts.py`)

6개 차트를 PNG로 그려 텔레그램 sendPhoto로 순서대로 보낸다.

| # | 차트 | 1순위 출처 | 대체 출처 |
|---|------|-----------|-----------|
| 1 | 미 10Y-2Y 스프레드 (2021-01~) | FRED T10Y2Y | Yahoo ^TNX − 2YY=F (근사, 2021-08~) |
| 2 | 미·한·중·일 10년 국채금리 (2020-01~) | 미 FRED DGS10 / 한 FRED IRLTLT01KRM156N(OECD 월평균) / 일 재무성 jgbcme_all.csv + 당월 jgbcme.csv | 미 Yahoo ^TNX / 일 FRED IRLTLT01JPM156N · 중국은 출처 없음 |
| 3 | WTI·브렌트·두바이 (올해 1월~) | 페트로넷 일일국제원유가격: 두바이 현물, 브렌트 ICE 선물, WTI NYMEX 선물. 캡션에 브렌트 현물 프리미엄(FRED DCOILBRENTEU − 브렌트 선물, 공통 최근일) | WTI Yahoo CL=F, 브렌트 Yahoo BZ=F, 두바이 FRED POILDUBUSDM(IMF 월평균) |
| 4 | 미 가솔린 소매가격 (2022-01~) | FRED GASREGW | 없음 |
| 5 | DTCR(좌)·NVDA(우) (2023-01~) | Yahoo(yfinance) | stooq |
| 6 | IBB·SOX 연초=100 | Yahoo(yfinance) | stooq |

### 실행

```
TELEGRAM_BOT_TOKEN=... python charts.py
```

- `--dry-run` 전송 없이 `out/`에 PNG만 저장, `--no-commit` git 커밋 생략, `--only 1,5` 일부만 실행
- 토큰이 없으면 자동으로 dry-run 처리, 전송은 `telegram_send.py`로 하며 message_id를 출력
- 출처별 재시도 1회, 차트 하나가 실패하면 "○○ 차트 생성 실패: 사유"를 텍스트로 대신 보냄

### data/

받은 시계열은 `data/<출처>_<코드>.csv`(date,value)로 저장하고, 실행 때마다 기존 마지막 날짜 이후 값만 추가한다.
매일 한 줄씩 쌓는 지표는 `append_rows("키", pd.Series([값], index=[pd.Timestamp(날짜)]))`로 추가하면 된다.
실행이 끝나면 data/ 변경분을 커밋하고 원격 기본 브랜치(또는 `CHARTS_DATA_BRANCH`)로 푸시한다. 실패하면 현재 브랜치로 푸시한다.
