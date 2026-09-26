# Owl Capital Briefing

## 아침 브리핑 전체 실행 (`briefing.py`)

```
python briefing.py            # data/ 브랜치 합치기 → 메시지 1~4·6 병렬 준비 → 1→2→3→4 전송 → charts.py(메시지 5) → 6 전송
python briefing.py --dry-run  # 전송 없이 out/에 이미지만 저장, 캡션 출력
```

| # | 스크립트 | 내용 | 실패 시 텍스트 |
|---|----------|------|----------------|
| 1 | `fear_greed.py` | Fear & Greed 반원 게이지 + 이전 값 2x2 표(가로형 약 1.4:1, 캡션 "36 → 37"), 이어서 1년 타임라인(1-2, 캡션 "1주 전 N / 1개월 전 N / 1년 전 N"). 값은 CNN 페이지처럼 소수점 버림 | 게이지 실패 시 캡션을 텍스트로, 타임라인 실패 시 "Fear & Greed 타임라인 확인 실패", 데이터 실패 시 "Fear & Greed 항목 확인 실패" |
| 2 | `blog_feed.py` | 블로그 RSS 최근 24시간 새 글, 첫 줄 "🌞 블로그 새 글 N건" | "🌞 블로그 새 글 없음" / 맨 아래 "확인 실패: 블로그명" |
| 3 | `finviz_heatmap.py` | 핀비즈 S&P 500 히트맵 1일·4주·연초 대비(캡션 없음) | "핀비즈 히트맵 확인 실패" |
| 4 | `natgas.py` | 헨리허브 천연가스 선물(NG=F) 2020-12~, 로그 눈금, 캡션 "$3.251/MMBtu (-1.4%)" | "미 천연가스 차트 확인 실패" |
| 5 | `charts.py` | 차트 6장 (아래) | "○○ 차트 생성 실패: 사유" |
| 6 | `memory.py` | 메모리: DRAM ETF 종가·좌수, SK하이닉스 2배(7709)·삼성전자 2배(7747) 종가(HKD)·좌수, DRAM 현물가 정품 3개 품목(세션 평균·변화율). 좌수가 20거래일 이상 쌓인 항목은 차트 (아래) | 부분 실패는 그 줄만 "○○ 확인 실패", 전부 실패면 "메모리 항목 확인 실패" |

- 각 스크립트는 단독 실행도 된다(`python natgas.py --dry-run`).
- 마지막에 메시지별 전송 결과(message_id 또는 실패 사유)와 실패 항목을 출력한다. 실패 항목이 있으면 종료 코드 1.
- 히트맵은 Playwright + `/opt/pw-browsers/chromium`(없으면 Playwright 기본, `CHROMIUM_PATH`로 변경 가능)을 쓴다.

## 텔레그램 전송 (`telegram_send.py`)

모든 스크립트가 이 모듈로 보낸다. 토큰은 `TELEGRAM_BOT_TOKEN`, 채팅 ID는 `TELEGRAM_CHAT_ID`(없거나 "chat not found"면 기본값 7164046356).
토큰이 없으면 dry-run으로 동작한다. 성공할 때마다 `[telegram] ok message_id=N` 을 출력한다.
전송은 항상 `parse_mode=HTML`(링크 미리보기 끔). `html=False`(기본)면 본문을 자동 이스케이프하고, dry-run은 텔레그램에 보일 글자 그대로 출력한다.

캡션에는 숫자만: 제목 줄 없이 시리즈마다 한 줄. 차트에는 큰 제목을 따로 두지 않고 범례로 알 수 있게 한다(국채금리·유가는 범례 위에 같은 크기로 "주요국 10년 국채금리"·"유가"). 히트맵은 제목·캡션 없이 보낸다.
가격 차트는 변화를 퍼센트만(`$92.41 (-2.3%)`), 금리·스프레드는 %p. 로그 눈금(`log_price_axis`, 눈금 글자는 일반 숫자)은 가격 차트(유가, 가솔린, 천연가스, DTCR·NVDA, IBB·SOX)에만 쓰고, 스프레드·국채금리·Fear & Greed는 일반 눈금.
출처는 캡션에 쓰지 않고 실행 로그(`[출처]`, 금리 차트는 나라별)에만 남긴다. 대체 출처를 쓰면 1순위 실패 사유를 `[경고]`로 남긴다. 날짜는 직전 영업일보다 오래된 값에만 `(9/22)`처럼 붙인다.

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
| 2 | 미·한·중·일 10년 국채금리 (2020-01~) | 미 FRED DGS10 / 한 ECOS 817Y002/010210000(시장금리 일별 국고채 10년, `ECOS_API_KEY`) / 일 재무성 jgbcme_all.csv + 당월 jgbcme.csv | 미 Yahoo ^TNX / 한 FRED IRLTLT01KRM156N(OECD 월평균) / 일 FRED IRLTLT01JPM156N · 중국은 출처 없음 |
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
실행이 끝나면 data/ 변경분을 커밋하고 원격 기본 브랜치(`main`, origin/HEAD 기준)로 푸시한다. 실패하면 현재 브랜치로 푸시한다.

## 메모리 (`memory.py`, 메시지 6)

```
DRAM ETF $61.91 (+2.0%)
· 좌수 4억 3,433만좌 (+1,250만좌)

SK하이닉스 2배(7709) HK$42.52 (-3.0%, 9/24)
· 좌수 8억 4,200만좌 (9/23)

삼성전자 2배(7747) HK$16.77 (-0.7%)
· 좌수 1억 2,150만좌 (+150만좌)

DRAM 현물가(세션 평균) 9/24
DDR5 16Gb $57.667 (+0.29%)
DDR4 16Gb $83.784 (-0.70%)
DDR4 8Gb $46.107 (+0.47%)
```

| 항목 | 1순위 출처 | 대체 출처 | data/ |
|------|-----------|-----------|-------|
| DRAM ETF | Roundhill 일별 CSV(`RU_DailyNAV.csv`): 종가(Market Price)·NAV·좌수·순자산, `RU_DRAM_Daily.csv`로 가격·NAV 과거분 | Yahoo DRAM(가격만) | `roundhill_DRAM_price/nav/shares/aum` |
| 7709·7747 가격 | Yahoo 7709.HK·7747.HK 종가(HKD) | CSOP 웹 API closePrice | `yahoo_7709_HK`·`yahoo_7747_HK`, `csop_7709_close`·`csop_7747_close` |
| 7709·7747 NAV·좌수·순자산 | CSOP 웹 API(`website-api.csopasset.com/cmsApi/NAV/product`, productName은 `memory.CSOP_PRODUCTS`). NAV·순자산은 저장만 | 없음 | `csop_7709_nav/units/aum`, `csop_7747_nav/units/aum` |
| DRAM 현물가 | DRAMeXchange 첫 화면 DRAM Spot 표(정품: DDR5 16Gb 4800/5600, DDR4 16Gb 3200, DDR4 8Gb 3200. eTT·DDR3 제외) | TrendForce 현물가 페이지 | `dramx_DDR5_16Gb_4800_5600` 등 |

- ETF 발행 단위는 "좌"로 쓴다(좌수 4억 3,433만좌, 변화도 +1,250만좌).
- 좌수 변화는 data/에 저장된 직전 날짜 값과 비교한다(첫 기록이면 값만). 좌수·순자산·NAV는 `float_format="%.15g"`로 반올림 없이 저장한다.
- 날짜는 다른 메시지처럼 직전 영업일보다 오래된 값에만 붙는다(현물가는 사이트의 Last Update 날짜).
- 차트 전환: 좌수가 20거래일(`CHART_MIN_DAYS`) 이상 쌓인 항목은 그 항목만 텍스트 대신 차트로 보낸다. 20일 전까지는 텍스트.
  - DRAM ETF·7709·7747: 좌축 좌수(빨간 선), 우축 가격(회색 선, 로그 눈금), 좌수 첫 기록일부터. 제목은 그림 안("SK하이닉스 2배(7709) 좌수 · 가격"), 캡션은 숫자만(`가격 HK$42.52 (-3.0%)` / `좌수 8억 4,200만좌 (+4,200만좌)`)
  - DRAM 현물가: 3개 품목 모두 20일 이상이면 로그 눈금 한 차트, 캡션은 품목별 한 줄
  - 텍스트로 남은 항목은 이어진 것끼리 한 통으로, 차트는 항목 순서(DRAM ETF → 7709 → 7747 → 현물가)대로 보낸다. 차트를 못 그리면 그 항목은 텍스트로
- 단독 실행 `python memory.py --dry-run`. `--dry-run`이 아니면 끝나고 data/를 커밋·푸시한다.

## 브리핑 사이트 (`site_build.py`, GitHub Pages `docs/`)

```
python site_build.py          # 텔레그램 전송 없이 메시지 1~6 이미지·캡션과 블로그 목록을 다시 만들어 docs/<오늘>.html 생성,
                              # index.html 을 그 페이지로, archive.html 에 날짜 추가, 마지막에 비밀값 검사
python site_build.py --check  # docs/ 비밀값 검사만
```

- 이미지는 `docs/charts/<날짜>-<이름>.png`로 복사한다(같은 날짜로 다시 만들면 안 쓰는 옛 이미지는 지운다).
- 블로그 한 줄 요약은 자동으로 만들지 않는다. 같은 날짜 페이지에 이미 있던 요약만 같은 글 URL에 이어 붙인다.
- 비밀값 검사: 이름에 TOKEN·KEY·SECRET·PASS·AUTH·CHAT_ID 가 들어간 환경변수 값이 docs/ 파일(이미지 포함)에 있는지,
  텔레그램·GitHub·AWS·Anthropic 토큰 형식과 `api.telegram.org/bot` 주소가 HTML에 있는지 본다. 찾으면 종료 코드 1(값은 출력하지 않음).
- 모든 HTML `<head>`에 `<meta name="robots" content="noindex, nofollow, noarchive">`를 넣어 검색 결과에 나오지 않게 한다(링크를 아는 사람만).
  `--check`는 이 표시가 빠진 HTML이 있어도 실패한다. robots.txt로 막으면 검색엔진이 이 표시를 못 읽으므로 막지 않는다.
- git 커밋·푸시는 하지 않는다.

## data/ 보존: 모든 브랜치에서 합치기 (`data_merge.py`, `cleanup_data.py`)

예약 실행은 세션 브랜치(claude/*)로만 푸시되는 경우가 있어 data/ 누적분이 브랜치마다 흩어진다.
그래서 `briefing.py` 시작 때와 `charts.py`의 data/ 커밋 직전에 원격 모든 브랜치의 data/를 날짜 기준 합집합으로 합친다
(같은 날짜는 현재 작업 폴더 → 기본 브랜치 → 최근 커밋 브랜치 순으로 먼저 있는 줄을 그대로 쓴다).

쌓인 브랜치 정리는 대화형 세션에서:

```
python cleanup_data.py --dry-run    # 합친 결과와 지울 브랜치 목록만 보기
python cleanup_data.py              # 합쳐서 기본 브랜치에 커밋·푸시 → 목록 확인 → y 입력 시 삭제
python cleanup_data.py --no-delete  # 합치기·커밋·푸시까지만
```

지우는 브랜치는 claude/*이면서, 기본 브랜치와 갈라진 뒤 바뀐 파일이 data/뿐이고, 그 data/ 행이 합친 기본 브랜치에 모두 있는 것만이다.
`main`·`master`는 어떤 경우에도 지우지 않는다. 예전 기본 브랜치 `claude/bold-lamport-70e83u`는 모든 커밋이 원격 main 이력에 있고 data/ 행도 (합치기 전) 원격 main에 모두 있을 때만 지운다.
지금 체크아웃한 브랜치, 코드·문서 변경이 있는 브랜치, 공통 이력이 없는 브랜치는 이유와 함께 남긴다. 기본 브랜치 푸시가 실패하면 아무것도 지우지 않는다.
