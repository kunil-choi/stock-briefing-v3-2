# analyzer/report_update_analyzer.py
"""
report_update(STEP-2) 재설계 로직 — "각자 완결된 브리핑"이 아니라 "하루짜리
연속 시리즈의 2부"로 STEP-2를 만들기 위한 모듈.

STEP-1(V3-1)이 이미 만든 종목선정/시장요약/AI전략을 처음부터 다시 만들지
않고, 그 위에 "새 정보"만 얹는다:
  A. STEP-1 리캡 재료 (build_step1_recap)
  B. 오전장 반응 업데이트 (build_morning_reaction)
  C. 증권사 리포트 브리핑 — 섹터 테마 + 종목별 심화 분석 (build_analyst_briefing)
  D. AI전략 업데이트 (build_ai_strategy_update)
  E. 영상 길이 티어 결정 (decide_length_tier)
"""
import json
from typing import Optional

from .api_client import call_claude_with_retry
from .naver_finance import fetch_naver_stock_price

# ── E. 길이 티어 결정 ────────────────────────────────────────────────────
# report_decision.py(구 버전)의 이진(longform/shorts) 판단을 3단계로 대체.
# 리포트 핵심종목 수 기준 — 고정 15분 목표를 버리고 리포트 볼륨에 비례한
# 가변 길이를 쓴다는 설계 결정에 따른 것.
MID_MIN_STOCKS  = 5   # 이 미만이면 shorts
FULL_MIN_STOCKS = 15  # 이 이상이면 full


def _count_core_stocks(brokerage_reports: dict) -> int:
    if not brokerage_reports:
        return 0
    names = set()
    for bucket in ("simultaneous", "new_coverage", "single_significant"):
        for r in brokerage_reports.get(bucket, []) or []:
            name = (r.get("stock_name") or "").strip()
            if name:
                names.add(name)
    return len(names)


def decide_length_tier(brokerage_reports: dict) -> str:
    """"shorts" | "mid" | "full" 반환."""
    core_count = _count_core_stocks(brokerage_reports)
    if core_count < MID_MIN_STOCKS:
        tier = "shorts"
    elif core_count < FULL_MIN_STOCKS:
        tier = "mid"
    else:
        tier = "full"
    print(f"  [길이티어] 리포트 핵심종목 {core_count}개 → {tier}")
    return tier


# ── A. STEP-1 리캡 ────────────────────────────────────────────────────────

def build_step1_recap(step1_data: dict) -> dict:
    """STEP-1(V3-1)이 이미 선정한 종목명 + 시장 요약 한 줄을 뽑는다.
    STEP-1 내용을 다시 설명하지 않고 "이미 다뤘다"는 맥락만 짚어주기 위한
    최소한의 재료다."""
    def _names(bucket):
        return [s.get("name", "") for s in (step1_data.get(bucket) or []) if s.get("name")]

    market_summary = step1_data.get("market_summary", "") or ""
    gist = market_summary.split("\n\n")[0].strip() if market_summary else ""

    # HIDDEN-PICK-REMOVE: V3-1은 "오늘의 픽"을 더 이상 만들지 않는다(관심종목에
    # 후보 풀을 집중시키기 위해 제거됨). "오늘의 픽" 컨셉은 증권사 리포트
    # 데이터가 있는 이 레포로 이전되어 single_significant 카테고리가 담당한다
    # (build_analyst_briefing 참고).
    return {
        "market_leaders":      _names("market_leaders"),
        "stocks":              _names("stocks"),
        "market_summary_gist": gist,
        "generated_at":        step1_data.get("generated_at", ""),
    }


# ── B. 오전장 반응 업데이트 ─────────────────────────────────────────────

def build_morning_reaction(step1_data: dict) -> list:
    """STEP-1이 선정한 대형주도주+관심종목의 오전장 현재가를 재조회해
    STEP-1 시점 가격 대비 반응을 정리한다. STEP-1이 갖고 있지 않던 "진짜 새
    정보"이므로 STEP-2의 핵심 섹션 중 하나."""
    reaction = []
    for bucket in ("market_leaders", "stocks"):
        for s in step1_data.get(bucket) or []:
            name = s.get("name", "")
            code = s.get("code", "")
            if not name or not code:
                continue
            price_info = fetch_naver_stock_price(name, code_override=code)
            if not price_info or price_info.get("price", 0) <= 0:
                continue
            reaction.append({
                "name":               name,
                "code":               code,
                "bucket":             bucket,
                "step1_price":        s.get("price", 0),
                "step1_change_pct":   s.get("change_pct", 0.0),
                "morning_price":      price_info["price"],
                "morning_change_pct": price_info.get("change_pct", 0.0),
            })
    return reaction


# ── C. 증권사 리포트 브리핑 (섹터 테마 + 종목 심화분석) ─────────────────

_ANALYST_BRIEFING_PROMPT = """
아래는 오늘/어제 수집된 증권사 리포트 데이터입니다(카테고리별로 이미 분류되어 있음).
이 데이터를 바탕으로 "장중 업데이트" 영상에 쓸 증권사 리포트 브리핑을 작성하세요.

[작성 규칙]
1. sector_themes: 리포트가 여러 종목·섹터에 몰려있으면 섹터별 테마로 묶어
   2~4개 작성(예: "반도체 섹터에 리포트 5건 집중"). 뚜렷한 테마가 없으면
   빈 배열로 두세요.
2. stocks: [리포트 데이터]에 있는 종목은 하나도 빠짐없이 전부 stocks 배열에
   포함하세요. 이 브리핑은 최소 10개 종목(또는 섹터)을 노출해야 하므로
   분량을 줄이려고 임의로 종목을 생략하지 마세요. 각 종목마다 3~4문장
   분량의 심화 분석을 작성하세요. 증권사명·투자의견·목표주가를 자연스럽게
   문장에 녹이되, 제공된 ai_summary/title 외의 수치나 전망을 새로 지어내지
   마세요.
3. category가 "simultaneous"(여러 증권사 동시언급)인 종목은 특히 강조해서
   작성하세요.
4. category가 "single_significant"(단독 리포트)인 종목은, 목표주가 상향
   등으로 특히 주목할 만하면 "오늘의 픽"으로 소개하고, 그 정도는 아니어도
   리포트 내용 중 실질적으로 유의미한 포인트(투자의견·목표주가·핵심 논리)를
   짚어 소개하는 어조로 작성하세요.
5. 순수 JSON만 출력하고 설명문·마크다운 코드블록은 넣지 마세요.

[리포트 데이터]
{reports_json}

[출력 JSON 구조]
{{
  "sector_themes": [
    {{"sector": "섹터명", "report_count": 0, "narrative": "1~2문장"}}
  ],
  "stocks": [
    {{
      "name": "종목명", "category": "simultaneous|new_coverage|single_significant",
      "brokers": ["증권사1"], "opinion": "매수", "target_price": "100000",
      "analysis": "3~4문장 심화 분석"
    }}
  ]
}}
"""


def _flatten_reports(brokerage_reports: dict) -> list:
    all_reports = []
    for bucket in ("simultaneous", "new_coverage", "single_significant"):
        for r in brokerage_reports.get(bucket, []) or []:
            all_reports.append({
                "stock_name":   r.get("stock_name", ""),
                "category":     bucket,
                "brokers":      r.get("brokers", []),
                "title":        r.get("title", ""),
                "opinion":      r.get("opinion", ""),
                "target_price": r.get("target_price", ""),
                "ai_summary":   r.get("ai_summary", ""),
            })
    return all_reports


def _fallback_briefing(all_reports: list) -> dict:
    """Claude 호출 실패/키 없음 시 원본 리포트 데이터를 그대로 노출."""
    return {
        "sector_themes": [],
        "stocks": [
            {
                "name":         r["stock_name"],
                "category":     r["category"],
                "brokers":      r["brokers"],
                "opinion":      r["opinion"],
                "target_price": r["target_price"],
                "analysis":     r["ai_summary"] or r["title"],
            }
            for r in all_reports
        ],
    }


def build_analyst_briefing(brokerage_reports: dict, api_key: str) -> dict:
    from .ai_analyzer import _try_parse_json

    all_reports = _flatten_reports(brokerage_reports)
    if not all_reports:
        return {"sector_themes": [], "stocks": []}
    if not api_key:
        print("  [리포트브리핑] API 키 없음 → 원본 데이터로 대체")
        return _fallback_briefing(all_reports)

    prompt = _ANALYST_BRIEFING_PROMPT.format(
        reports_json=json.dumps(all_reports, ensure_ascii=False, indent=2)
    )
    try:
        response_text = call_claude_with_retry(prompt, api_key=api_key, max_tokens=8000)
        result = _try_parse_json(response_text)
    except Exception as e:
        print(f"  [리포트브리핑] Claude 호출 실패: {e}")
        result = None

    if not result or not result.get("stocks"):
        print("  [리포트브리핑] 파싱 실패/빈 응답 → 원본 데이터로 대체")
        return _fallback_briefing(all_reports)

    result.setdefault("sector_themes", [])
    return result


# ── D. AI전략 업데이트 ───────────────────────────────────────────────────

_STRATEGY_UPDATE_PROMPT = """
오늘 아침 제시했던 AI 투자 전략입니다:
{step1_strategy}

이후 증권사 리포트에서 아래 내용이 새로 나왔습니다:
{reports_summary}

[지시]
아침 전략을 처음부터 다시 쓰지 말고, 리포트 내용을 반영해 "무엇이
보강·변경됐는지"를 중심으로 3~5문장으로 업데이트하세요. "오늘 아침 제시한
전략에 더해" 같은 표현으로 자연스럽게 이어지게 작성하세요. 리포트에 없는
내용은 지어내지 마세요.

순수 텍스트만 출력하세요(따옴표·마크다운 없이).
"""


def build_ai_strategy_update(step1_strategy: str, brokerage_reports: dict,
                             api_key: str) -> str:
    all_reports = _flatten_reports(brokerage_reports)

    if not api_key:
        return step1_strategy or ""
    if not all_reports:
        return "오늘 아침 제시한 전략을 그대로 유지합니다. 장중 특별한 리포트 이슈는 확인되지 않았습니다."

    lines = []
    for r in all_reports:
        parts = [r["stock_name"]]
        if r["opinion"]:
            parts.append(r["opinion"])
        if r["target_price"]:
            parts.append(f"목표가 {r['target_price']}원")
        if r["ai_summary"]:
            parts.append(f"({r['ai_summary']})")
        lines.append("- " + " ".join(parts))

    prompt = _STRATEGY_UPDATE_PROMPT.format(
        step1_strategy=step1_strategy or "(오늘 아침 전략 데이터 없음)",
        reports_summary="\n".join(lines),
    )
    try:
        text = call_claude_with_retry(prompt, api_key=api_key, max_tokens=1000)
        return text.strip()
    except Exception as e:
        print(f"  [전략업데이트] Claude 호출 실패: {e}")
        return step1_strategy or ""
