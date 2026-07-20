# main.py
"""
stock-briefing-v3-2 — report_update 영상용 "장중 업데이트" 데이터 파이프라인

설계 원칙(재설계, GEMINI-YT-6 이후 논의): STEP-1과 STEP-2는 "각자 완결된
브리핑"이 아니라 "하루짜리 연속 시리즈의 1부/2부"다. 그래서 이 레포는 더 이상
V3_1의 원본 수집 데이터를 갖고 처음부터 종목선정을 다시 하지 않는다. 대신
V3_1이 이미 만든 결과물(data/briefing_data.json — 종목선정·시장요약·AI전략
전부 끝난 상태)을 그대로 갖고 와서, 그 위에 "새 정보"만 얹는다:

  A. STEP-1 리캡 재료(어떤 종목을 다뤘는지)
  B. 오전장 반응 업데이트(STEP-1 시점 가격 대비 지금 가격)
  C. 증권사 리포트 브리핑(섹터 테마 + 종목별 심화 분석)
  D. AI전략 업데이트(처음부터 다시 만들지 않고 "무엇이 보강됐는지"로 작성)

애널리스트 재시도 루프(08:00 대기 ~ 08:30 강제진행, 최대 120분)는 기존과
동일하게 유지한다.

산출물:
- data/briefing_data.json : {length_tier, step1_recap, morning_reaction,
  analyst_briefing, ai_strategy_update, brokerage_reports, market_data}
  (stock-briefing-step2가 raw.githubusercontent.com으로 직접 소비)
- docs/index.html         : GitHub Pages 프리뷰 페이지(사람이 눈으로 데이터를
  확인하기 위한 용도).

완료 후 GH_TOKEN으로 stock-briefing-step2(report_update.yml)를
workflow_dispatch로 트리거한다.
"""
import os
import json
import time
import shutil
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from config import (
    ANTHROPIC_API_KEY, GH_TOKEN,
)
from collectors.analyst_collector          import collect_analyst
from analyzer.ai_analyzer                  import build_brokerage_reports
from analyzer.report_update_analyzer       import (
    decide_length_tier,
    build_step1_recap,
    build_morning_reaction,
    build_analyst_briefing,
    build_ai_strategy_update,
)
from analyzer.report_update_html           import generate_report_update_html

KST = ZoneInfo("Asia/Seoul")

V3_1_BRIEFING_URL = (
    "https://raw.githubusercontent.com/kunil-choi/stock-briefing-v3-1/main/"
    "data/briefing_data.json"
)
UPSTREAM_MAX_WAIT_MIN = 15
UPSTREAM_RETRY_SEC    = 5 * 60


def safe_collect(fn, *args, label="", **kwargs):
    try:
        result = fn(*args, **kwargs)
        return result if result else []
    except Exception as e:
        print(f"  [{label}] 수집 중 오류: {e}")
        return []


def fetch_v3_1_briefing_data(expected_date: str) -> dict:
    """stock-briefing-v3-1이 발행한 data/briefing_data.json(이미 종목선정·
    AI전략까지 끝난 결과물)을 가져온다. briefing_date가 오늘과 다르면(V3_1이
    아직 오늘자를 못 올렸으면) 재시도한다 — V3_1이 workflow_dispatch로 이
    레포를 트리거하는 구조라 정상적으로는 거의 항상 즉시 일치한다."""
    waited_min = 0
    while waited_min <= UPSTREAM_MAX_WAIT_MIN:
        try:
            with urllib.request.urlopen(V3_1_BRIEFING_URL, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("briefing_date") == expected_date:
                print(f"✅ V3_1 briefing_data.json 로드 완료 (날짜 일치: {expected_date})")
                return data
            print(f"⚠️  V3_1 briefing_data.json 날짜 불일치 "
                  f"(기대:{expected_date}, 실제:{data.get('briefing_date')})")
        except Exception as e:
            print(f"⚠️  V3_1 briefing_data.json 조회 실패 ({e})")
        if waited_min >= UPSTREAM_MAX_WAIT_MIN:
            break
        print(f"  🔄 {UPSTREAM_RETRY_SEC // 60}분 후 재시도 (대기 누계: {waited_min}분)")
        time.sleep(UPSTREAM_RETRY_SEC)
        waited_min += UPSTREAM_RETRY_SEC // 60
    return {}


def main():
    now_kst    = datetime.now(KST)
    print(f"=== V3_2 report_update 데이터 생성 시작: {now_kst.strftime('%Y-%m-%d %H:%M:%S KST')} ===")
    start_time = now_kst.timestamp()

    # ── API 키 확인 ────────────────────────────────────────────────────────
    print("\n[API 키 확인]")
    keys = {"ANTHROPIC_API_KEY": ANTHROPIC_API_KEY, "GH_TOKEN": GH_TOKEN}
    for name, val in keys.items():
        print(f"  {name}: {'✅' if val else '❌ 없음'}")
    if not ANTHROPIC_API_KEY:
        print("❌ ANTHROPIC_API_KEY 없음 → 실행 중단")
        raise SystemExit(1)

    # ── 1. STEP-1(V3_1)의 완성된 브리핑 재사용 ─────────────────────────────
    expected_date = now_kst.strftime("%Y년 %m월 %d일")
    step1_data = fetch_v3_1_briefing_data(expected_date)
    if not step1_data:
        print(f"\n❌ V3_1의 오늘자 briefing_data.json을 {UPSTREAM_MAX_WAIT_MIN}분 내에 "
              f"가져오지 못했습니다 → 오늘 report_update 생성을 스킵합니다.")
        print("status: upstream_not_ready")
        return

    # ── 2. 시장 데이터 재조회 (오전장 반영) ─────────────────────────────────
    print("\n[시장 데이터 재조회] (오전장 반영)")
    try:
        from collectors.market_collector import collect_market_overview
        market_overview = collect_market_overview()
    except Exception as e:
        print(f"  [시장데이터 재조회 실패] {e}")
        market_overview = {}

    # ── 3. 애널리스트 리포트 수집 (기존 재시도 루프와 100% 동일) ───────────
    print("\n[애널리스트 리포트 수집] (본문 크롤링 + Claude 요약 포함)...")
    _TARGET_HOUR   = 8
    _MIN_REPORTS   = 20
    _MAX_WAIT_MIN  = 120
    _RETRY_SEC     = 5 * 60

    analyst_data = []
    waited_min   = 0

    while waited_min <= _MAX_WAIT_MIN:
        _now = datetime.now(KST)

        if _now.hour < _TARGET_HOUR:
            _secs_to_target = (
                (_TARGET_HOUR - _now.hour) * 3600
                - _now.minute * 60
                - _now.second
            )
            _wait = min(_secs_to_target, _RETRY_SEC)
            print(f"  ⏳ 08:00 KST 대기 중 (현재 {_now.strftime('%H:%M')}, {int(_wait // 60)}분 후 재확인)...")
            time.sleep(_wait)
            waited_min += _wait // 60
            continue

        _candidate  = safe_collect(collect_analyst, api_key=ANTHROPIC_API_KEY, label="애널리스트")
        _today_str2 = datetime.now(KST).strftime("%y.%m.%d")
        _today_data = [d for d in _candidate if d.get("date", "") == _today_str2]

        print(f"  → 오늘({_today_str2}) 리포트: {len(_today_data)}건 / 전체 수집: {len(_candidate)}건")

        if len(_today_data) >= _MIN_REPORTS:
            analyst_data = _candidate
            print(f"  ✅ 기준({_MIN_REPORTS}건) 충족 → 진행")
            break

        _now2 = datetime.now(KST)
        if _now2.hour > 8 or (_now2.hour == 8 and _now2.minute >= 30):
            print(f"  ⚠️  08:30 KST 초과 → 건수 무관 강제 진행 ({len(_today_data)}건)")
            analyst_data = _candidate
            break

        if waited_min >= _MAX_WAIT_MIN:
            print(f"  ⚠️  최대 대기({_MAX_WAIT_MIN}분) 초과 → 수집된 데이터로 강제 진행")
            analyst_data = _candidate
            break

        print(f"  🔄 {_MIN_REPORTS}건 미달 → {_RETRY_SEC // 60}분 후 재시도 (대기 누계: {waited_min}분)")
        time.sleep(_RETRY_SEC)
        waited_min += _RETRY_SEC // 60

    print(f"  → 최종 애널리스트 데이터: {len(analyst_data)}건")

    # ── 4. STEP-1 결과물 위에 새 정보를 얹는다 (재분석 아님) ────────────────
    print("\n[분석] STEP-1 위에 오전장 반응/리포트 브리핑/전략 업데이트 얹기...")
    brokerage_reports = build_brokerage_reports(analyst_data)

    length_tier = decide_length_tier(brokerage_reports)

    step1_recap = build_step1_recap(step1_data)
    print(f"  [리캡] 대형주도주:{step1_recap['market_leaders']} "
          f"관심종목:{len(step1_recap['stocks'])}개")

    print("  [오전장 반응] 조회 중...")
    morning_reaction = build_morning_reaction(step1_data)
    print(f"  → {len(morning_reaction)}개 종목 반응 확보")

    print("  [리포트 브리핑] Claude 심화 분석 중...")
    analyst_briefing = build_analyst_briefing(brokerage_reports, ANTHROPIC_API_KEY)
    print(f"  → 섹터테마 {len(analyst_briefing.get('sector_themes', []))}개, "
          f"종목분석 {len(analyst_briefing.get('stocks', []))}개")

    print("  [전략 업데이트] Claude 호출 중...")
    ai_strategy_update = build_ai_strategy_update(
        step1_data.get("ai_strategy", ""), brokerage_reports, ANTHROPIC_API_KEY
    )

    result = {
        "briefing_date":      step1_data.get("briefing_date", expected_date),
        "generated_at":       now_kst.strftime("%H:%M"),
        "length_tier":        length_tier,
        "step1_recap":        step1_recap,
        "morning_reaction":   morning_reaction,
        "analyst_briefing":   analyst_briefing,
        "ai_strategy_update": ai_strategy_update,
        "brokerage_reports":  brokerage_reports,
        "market_data":        market_overview,
    }

    os.makedirs("data", exist_ok=True)
    with open("data/briefing_data.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("\n[저장] data/briefing_data.json 저장 완료")

    # ── 아카이브 + HTML 저장 (docs/index.html) ─────────────────────────────
    html = generate_report_update_html(result)
    os.makedirs("docs/archive", exist_ok=True)
    existing_index = "docs/index.html"
    if os.path.exists(existing_index):
        archive_date = datetime.now(KST).strftime("%Y-%m-%d")
        archive_path = f"docs/archive/{archive_date}.html"
        if not os.path.exists(archive_path):
            shutil.copy2(existing_index, archive_path)
            print(f"[아카이브] 저장: {archive_path}")
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("[저장] docs/index.html 저장 (GitHub Pages 프리뷰)")

    elapsed = datetime.now(KST).timestamp() - start_time
    print(f"\n✅ V3_2 데이터 생성 완료 (길이티어: {length_tier}) → "
          f"data/briefing_data.json, docs/index.html")
    print(f"=== 완료: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')} "
          f"(소요: {elapsed:.0f}초) ===")


if __name__ == "__main__":
    main()
