# main.py
"""
stock-briefing-v3-2 — report_update 영상용 "장중 업데이트" 데이터 파이프라인

stock-briefing-v3-1이 만든 data/raw_YYYYMMDD.json(시장데이터/뉴스/유튜브/Gemini
분석 원본)을 raw.githubusercontent.com으로 재사용하고, 여기에 애널리스트 리포트
수집만 새로 추가해 09:20 KST 이후 report_update 영상이 참조할 "리포트+오전장 반영"
스냅샷을 만든다. v3 원본은 수정하지 않고 이 레포에 독립적으로 복사·유지한다.

애널리스트 재시도 루프(08:00 대기 ~ 08:30 강제진행, 최대 120분)는
stock-briefing-v3의 main.py 로직을 그대로 복사했다.

산출물:
- data/briefing_data.json : brokerage_reports가 포함된 버전
  (stock-briefing-step2가 raw.githubusercontent.com으로 직접 소비)

완료 후 GH_TOKEN으로 stock-briefing-step2(report_update.yml)를
workflow_dispatch로 트리거한다.
"""
import os
import json
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from config import (
    ANTHROPIC_API_KEY, GH_TOKEN, GITHUB_REPO, load_channels,
)
from collectors.analyst_collector import collect_analyst
from analyzer.ai_analyzer          import analyze_and_generate_html

KST = ZoneInfo("Asia/Seoul")

V3_1_RAW_URL_TMPL = (
    "https://raw.githubusercontent.com/kunil-choi/stock-briefing-v3-1/main/"
    "data/raw_{date}.json"
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


def fetch_v3_1_raw_data(today_str: str) -> list:
    """stock-briefing-v3-1이 발행한 raw_YYYYMMDD.json을 가져온다.
    V3_1이 workflow_dispatch로 이 레포를 트리거하는 구조라 정상적으로는
    거의 항상 이미 존재하지만, 전파 지연 등에 대비해 짧게 재시도한다."""
    url = V3_1_RAW_URL_TMPL.format(date=today_str)
    waited_min = 0
    while waited_min <= UPSTREAM_MAX_WAIT_MIN:
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            print(f"✅ V3_1 raw 데이터 로드 완료: {len(data)}건 ({url})")
            return data
        except Exception as e:
            print(f"⚠️  V3_1 raw 데이터 조회 실패 ({e}) — {url}")
            if waited_min >= UPSTREAM_MAX_WAIT_MIN:
                break
            print(f"  🔄 {UPSTREAM_RETRY_SEC // 60}분 후 재시도 "
                  f"(대기 누계: {waited_min}분)")
            time.sleep(UPSTREAM_RETRY_SEC)
            waited_min += UPSTREAM_RETRY_SEC // 60
    return []


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

    # ── 채널 로드 (ai_analyzer의 channel_mentions 매핑에 필요) ─────────────
    channels = load_channels()

    # ── 0. 시장 데이터 재조회 (오전장 반영 — V3_1의 개장 전 수치보다 최신) ──
    print("\n[시장 데이터 재조회] (오전장 반영)")
    try:
        from collectors.market_collector import collect_market_overview
        market_overview = collect_market_overview()
    except Exception as e:
        print(f"  [시장데이터 재조회 실패] {e}")
        market_overview = {}

    # ── 1. V3_1의 raw 데이터 재사용 (뉴스/유튜브/Gemini 재수집 안 함) ──────
    today_str = now_kst.strftime("%Y%m%d")
    all_data  = fetch_v3_1_raw_data(today_str)
    if not all_data:
        print(f"\n❌ V3_1 데이터를 {UPSTREAM_MAX_WAIT_MIN}분 내에 가져오지 못했습니다 "
              f"({today_str}) → 오늘 report_update 생성을 스킵합니다.")
        print("status: upstream_not_ready")
        return

    # ── 2. 애널리스트 리포트 수집 (v3 main.py의 재시도 루프와 100% 동일) ──
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

    all_data = all_data + analyst_data
    print(f"  → 최종 애널리스트 데이터: {len(analyst_data)}건")

    # ── 3. AI 분석 (Claude) — data/briefing_data.json은 이 호출 내부에서 저장됨 ──
    # V3_1의 raw all_data + 애널리스트 데이터를 합쳐 재분석하므로, 시장 리더/관심종목
    # 랭킹도 애널리스트 언급까지 반영해 다시 계산된다. 오전장 반영 현재가는
    # ai_analyzer._get_price_label()이 실행 시각(09:20+) 기준으로 자동 처리한다.
    print("\n[AI 분석] Claude 분석 + Gemini 검수 시작...")
    try:
        analyze_and_generate_html(
            all_data,
            channels_data=channels,
            gh_repo=GITHUB_REPO,
            gh_token=GH_TOKEN,
            market_overview=market_overview,
        )
    except Exception as e:
        print(f"[AI 분석 실패] {e}")
        raise

    elapsed = datetime.now(KST).timestamp() - start_time
    print(f"\n✅ V3_2 데이터 생성 완료 → data/briefing_data.json")
    print(f"=== 완료: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')} "
          f"(소요: {elapsed:.0f}초) ===")


if __name__ == "__main__":
    main()
