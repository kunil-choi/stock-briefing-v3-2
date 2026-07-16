# stock-briefing-v3-2

`stock-briefing-video`의 **report_update**(장중 업데이트) 영상 파이프라인이 소비하는
데이터 전용 백엔드 레포입니다. `stock-briefing-v3-1`이 완료되면 자동으로 이 레포를
트리거하며, 완료 후 다시 `stock-briefing-step2`를 트리거합니다.

## 파이프라인

`main.py`는 뉴스/유튜브/Gemini 수집을 **반복하지 않습니다**. 대신:

1. `stock-briefing-v3-1`이 발행한 `data/raw_YYYYMMDD.json`을
   `raw.githubusercontent.com`으로 가져와 재사용 (`fetch_v3_1_raw_data()`).
   최대 15분(5분 간격) 재시도 — V3_1이 dispatch로 이 레포를 트리거하는 구조라
   정상적으로는 거의 항상 즉시 존재함. 못 가져오면 그날 report_update 생성을
   스킵함(로그에 `status: upstream_not_ready` 출력).
2. 시장 데이터 재조회 (`collectors/market_collector.py`) — V3_1의 개장 전 수치보다
   최신인 "오전장 반영" 지표.
3. 애널리스트 리포트 수집 (`collectors/analyst_collector.py`) — **v3의
   `main.py`에 있는 08:00 KST 대기 ~ 08:30 강제진행(최대 120분) 재시도 루프를
   그대로 복사**했습니다. 20건 이상 확보되면 즉시 진행, 08:30을 넘기면 건수와
   무관하게 강제 진행합니다.
4. V3_1의 raw 데이터 + 새로 수집한 애널리스트 데이터를 합쳐
   `analyzer/ai_analyzer.py`(v3와 동일 모듈)로 재분석.

## 산출물

- `data/briefing_data.json` — `brokerage_reports`가 포함된 버전.
  `stock-briefing-step2`가
  `raw.githubusercontent.com/kunil-choi/stock-briefing-v3-2/main/data/briefing_data.json`
  으로 직접 소비.
- `docs/index.html` — GitHub Pages 프리뷰 페이지. `stock-briefing-v3-1`과 동일한
  방식(`analyzer/html_generator.py`는 무수정, 반환된 HTML에 프리뷰 배너만 얹음)이며
  step2 영상 제작에 쓰인 리포트 포함 최종 데이터를 확인하는 용도입니다. 정식
  `stock-briefing-v3` 공개 사이트를 대체하지 않습니다(v3는 자동 실행 중단 상태로
  그대로 유지, step1/step2 영상 업로드가 시작되면 v3가 다시 정식 공개 사이트 역할을
  맡을 계획).

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
