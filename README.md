# stock-briefing-v3-2

`stock-briefing-video`의 **report_update**(장중 업데이트) 영상 파이프라인이 소비하는
데이터 전용 백엔드 레포입니다. `stock-briefing-v3-1`이 완료되면 자동으로 이 레포를
트리거하며, 완료 후 다시 `stock-briefing-step2`를 트리거합니다.

## 파이프라인 (재설계: "1부/2부 연속 시리즈")

**설계 원칙**: STEP-1(morning_core)과 STEP-2(report_update)는 "각자 완결된
브리핑"이 아니라 "하루짜리 연속 시리즈의 1부/2부"다. 그래서 이 레포는 더 이상
V3_1의 원본 수집 데이터를 갖고 종목선정을 처음부터 다시 하지 않는다(예전엔
`analyzer/ai_analyzer.py`로 전체 재분석했음 — 그 결과 STEP-2가 STEP-1과
상당 부분 겹치는 "재브리핑"이 되는 문제가 있었다). 대신 V3_1이 이미 만든
결과물(`data/briefing_data.json` — 종목선정·시장요약·AI전략 전부 끝난 상태)을
그대로 갖고 와서, 그 위에 "새 정보"만 얹는다.

`main.py`의 순서:

1. `stock-briefing-v3-1`이 발행한 `data/briefing_data.json`(원본 raw 아님 —
   이미 종목선정/AI전략까지 끝난 완성 브리핑)을 `raw.githubusercontent.com`으로
   가져옴 (`fetch_v3_1_briefing_data()`). `briefing_date`가 오늘과 일치할 때까지
   최대 15분(5분 간격) 재시도. 못 가져오면 그날 report_update 생성을
   스킵함(로그에 `status: upstream_not_ready` 출력).
2. 시장 데이터 재조회 (`collectors/market_collector.py`) — V3_1의 개장 전 수치보다
   최신인 "오전장 반영" 지표.
3. 애널리스트 리포트 수집 (`collectors/analyst_collector.py`) — **v3의
   `main.py`에 있는 08:00 KST 대기 ~ 08:30 강제진행(최대 120분) 재시도 루프를
   그대로 복사**했습니다. 20건 이상 확보되면 즉시 진행, 08:30을 넘기면 건수와
   무관하게 강제 진행합니다.
4. `analyzer/report_update_analyzer.py`가 V3_1 결과물 위에 새 정보를 얹음
   (재분석이 아니라 순수 추가):
   - **A. STEP-1 리캡 재료** (`build_step1_recap`) — 어떤 종목을 다뤘는지
   - **B. 오전장 반응 업데이트** (`build_morning_reaction`) — STEP-1 시점 가격
     대비 지금 가격 (네이버 재조회)
   - **C. 증권사 리포트 브리핑** (`build_analyst_briefing`) — 섹터 테마 +
     종목별 심화 분석(Claude 1회 호출, 종목당 3~4문장)
   - **D. AI전략 업데이트** (`build_ai_strategy_update`) — STEP-1 전략을
     처음부터 다시 쓰지 않고 "무엇이 보강됐는지" 3~5문장으로 작성
   - **E. 영상 길이 티어 결정** (`decide_length_tier`) — 리포트 핵심종목 수
     기준 `shorts`(<5개) / `mid`(5~14개) / `full`(15개+). 고정 15분 목표는 폐기.

## 산출물

- `data/briefing_data.json` — `{length_tier, step1_recap, morning_reaction,
  analyst_briefing, ai_strategy_update, brokerage_reports, market_data}`.
  `stock-briefing-step2`가
  `raw.githubusercontent.com/kunil-choi/stock-briefing-v3-2/main/data/briefing_data.json`
  으로 직접 소비.
- `docs/index.html` — GitHub Pages 프리뷰 페이지. 새 스키마 전용 렌더러
  (`analyzer/report_update_html.py`)로 생성 — V3/V3-1과 공유하는
  `html_generator.py`는 스키마가 달라 쓰지 않음. 길이 티어 배지 + 리캡 +
  오전장 반응 표 + 리포트 브리핑 카드 + 전략 업데이트를 한눈에 확인 가능.

## GitHub Pages 활성화 (최초 1회, 수동)

레포 Settings → Pages → Build and deployment → Source: **Deploy from a branch**
→ Branch: `main` / `/docs` 선택 후 Save. 이후 워크플로우가 `docs/index.html`을
커밋할 때마다 자동 반영되며, 다음 주소에서 확인할 수 있습니다:
`https://kunil-choi.github.io/stock-briefing-v3-2/`

## 트리거 체인

```
stock-briefing-v3-1 완료 → workflow_dispatch
  → main.py 실행 (08:00 대기~08:30 강제진행 포함, 목표 완료 ~08:35~09:00)
  → data/ 커밋·푸시
  → workflow_dispatch: stock-briefing-step2 (report_update.yml)
```

09:20 KST 이후 report_update 영상 생성 창을 목표로 하며, 이 체인이 정상 동작하면
그 전에 항상 데이터가 준비됩니다. 자체 cron은 두지 않습니다(`stock-briefing-video`의
`daily_broadcast.yml` 설계 원칙과 동일 — 상류가 끝나기 전에 실행되면 날짜 불일치
위험).

## 필요 Secrets (레포 Settings → Secrets and variables → Actions)

| Secret | 용도 |
|---|---|
| `ANTHROPIC_API_KEY` | 애널리스트 리포트 요약 + 최종 Claude 분석 |
| `GH_TOKEN` | `contents:write` + step2 워크플로우 dispatch 권한 필요 |

`YOUTUBE_API_KEY`/`GEMINI_API_KEY`는 이 레포에서 사용하지 않습니다(뉴스/유튜브
재수집이 없음).

## 로컬 실행

```bash
pip install -r requirements.txt
cp .env.example .env   # 값 채우기
python main.py
```

로컬 실행 시 `stock-briefing-v3-1`이 오늘 날짜로 `data/raw_YYYYMMDD.json`을 이미
발행해뒀어야 정상 동작합니다.

## 다음 단계 (이 레포 범위 아님)

- 개체명 추출/scene_plan.json, 미디어 검색, 방송형 렌더러, 내러티브 플롯 알고리즘,
  TTS 고도화는 `stock-briefing-step1`/`stock-briefing-step2`에서 후속 단계로 다룹니다.
