# collectors/analyst_collector.py
"""
애널리스트 리포트 수집기 - v3

수정 이력:
- BUG-AC-4   : 종목당 1카테고리 보장 (classify_analyst_reports 개선)
- FIX-ANA-4  : summary 메타데이터 중복 제거, 제목 링크로 원문 접근
- FIX-RPT-1  : 리포트 본문 실제 크롤링 후 Claude 1문장 요약 → ai_summary 필드 저장
               근거 없는 메타 요약 완전 제거, 방송 정확성 기준 준수
- FIX-COST-1 : Claude 호출 순서 변경 — 분류 후 대표 리포트에만 요약 호출
               (기존: 수집 시 전건 호출 → 폐기 낭비 제거)
               불필요한 import(OrderedDict) 제거

네이버 금융 company_list.naver 컬럼 구조:
  cols[0]: 종목명
  cols[1]: 리포트 제목 (링크 포함)
  cols[2]: 증권사
  cols[3]: 첨부 (PDF 링크, 없으면 빈 td)
  cols[4]: 작성일 (YY.MM.DD 또는 YYYY.MM.DD)
  cols[5]: 조회수
"""
import re
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

KST = timezone(timedelta(hours=9))

REPORT_DAYS = 2  # 오늘 + 전날 리포트 포함 (당일 오전 데이터 부족 대응)

BROKERS = [
    "NH투자증권", "삼성증권", "KB증권", "미래에셋증권",
    "한국투자증권", "신한투자증권", "하나증권", "키움증권",
    "대신증권", "메리츠증권", "한화투자증권", "유진투자증권",
    "LS증권", "IBK투자증권", "DB금융투자", "SK증권",
    "현대차증권", "BNK투자증권", "iM증권", "교보증권",
    "다올투자증권", "한양증권", "흥국증권", "토스증권",
]

NAVER_FINANCE_BASE  = "https://finance.naver.com"
NAVER_RESEARCH_BASE = "https://finance.naver.com/research"

# 대형 증권사 (단독언급 유의미성 판단 기준)
_TOP_BROKERS = {
    "NH투자증권", "삼성증권", "KB증권", "미래에셋증권", "한국투자증권",
    "신한투자증권", "하나증권", "키움증권", "메리츠증권", "대신증권",
}

# 목표주가 상향 패턴
_TP_UP_PATTERN = re.compile(
    r'(목표주가|목표가|TP)[^\d]*?(\d[\d,]+)[^\d]*?(→|▶|↑|상향|올려|높여)[^\d]*?(\d[\d,]+)',
    re.IGNORECASE
)
_TP_UP_KEYWORDS = ["상향", "목표주가 ↑", "TP ↑", "올려", "높여", "목표가 상향"]
_OPINION_UP_MAP = {
    ("중립", "매수"), ("중립", "BUY"), ("HOLD", "매수"), ("HOLD", "BUY"),
    ("비중축소", "중립"), ("비중축소", "매수"), ("매도", "중립"), ("매도", "매수"),
}


def _is_significant_single(report: dict) -> tuple[bool, str]:
    """
    단독언급 리포트 중 유의미한 것을 규칙 기반으로 판별.
    Returns (is_significant, reason)
    """
    title  = report.get("report_title", "") or report.get("title", "")
    broker = report.get("source_name", "")

    # 조건 1: 대형 증권사 발행
    is_top = broker in _TOP_BROKERS

    # 조건 2: 목표주가 상향 키워드
    tp_up = any(kw in title for kw in _TP_UP_KEYWORDS) or bool(_TP_UP_PATTERN.search(title))

    # 조건 3: 투자의견 상향
    opinion = report.get("opinion", "")
    opinion_up = False  # 단독 리포트는 이전 의견 정보가 없어 제목 기반으로만 판단
    if any(kw in title for kw in ["투자의견 상향", "의견 상향", "Buy로 상향", "매수로 상향"]):
        opinion_up = True

    # 유의미 판단: (대형사 AND 목표주가상향) OR 투자의견상향
    if is_top and tp_up:
        return True, f"대형사({broker}) + 목표주가 상향"
    if opinion_up:
        return True, f"투자의견 상향"
    if is_top and opinion_up:
        return True, f"대형사({broker}) + 투자의견 상향"

    return False, ""

COVERAGE_KEYWORDS = ["커버리지", "신규", "개시", "Coverage Initiation", "Initiation", "NDR"]

_OPINION_PATTERN = re.compile(
    r'\b(BUY|SELL|HOLD|매수|매도|중립|시장수익률|비중확대|비중축소|Outperform|Underperform|Not Rated|NR)\b',
    re.IGNORECASE
)
_TARGET_PRICE_PATTERN = re.compile(
    r'(?:목표주가|목표가|TP|T\.P)[^\d]*?(\d[\d,]+)',
    re.IGNORECASE
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer":         "https://finance.naver.com/",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

_DATE_PATTERN = re.compile(r"^\d{2,4}\.\d{2}\.\d{2}$")

# FIX-RPT-1: 본문 크롤링 최대 길이 (Claude 토큰 절약)
_MAX_BODY_CHARS = 3000


def _build_link(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return NAVER_FINANCE_BASE + href
    return NAVER_RESEARCH_BASE + "/" + href


def is_within_days(date_str: str, days: int = REPORT_DAYS) -> bool:
    try:
        date_str = date_str.strip().replace(" ", "")
        if len(date_str) == 10 and date_str.count(".") == 2:
            report_date = datetime.strptime(date_str, "%Y.%m.%d")
        elif len(date_str) == 8 and date_str.count(".") == 2:
            report_date = datetime.strptime(date_str, "%y.%m.%d")
        elif len(date_str) == 8 and "." not in date_str:
            report_date = datetime.strptime(date_str, "%Y%m%d")
        elif len(date_str) == 6 and "." not in date_str:
            report_date = datetime.strptime(date_str, "%y%m%d")
        else:
            print(f"  [날짜 파싱 불가] '{date_str}' → 포함 처리")
            return True
        cutoff_date = (datetime.now(KST) - timedelta(days=days)).date()
        return report_date.date() >= cutoff_date
    except Exception as e:
        print(f"  [날짜 파싱 오류] '{date_str}': {e} → 포함 처리")
        return True


def is_new_coverage(title: str) -> bool:
    return any(k in title for k in COVERAGE_KEYWORDS)


def _extract_opinion(title: str) -> str:
    m = _OPINION_PATTERN.search(title)
    if m:
        raw = m.group(1)
        mapping = {
            "BUY": "매수", "SELL": "매도", "HOLD": "중립",
            "OUTPERFORM": "비중확대", "UNDERPERFORM": "비중축소",
            "NOT RATED": "NR", "NR": "NR",
        }
        return mapping.get(raw.upper(), raw)
    return ""


def _extract_target_price(title: str) -> str:
    m = _TARGET_PRICE_PATTERN.search(title)
    if m:
        return m.group(1).replace(",", "")
    return ""


def _find_date_col(cols: list) -> str:
    check_order = [4, 3, 5] if len(cols) > 4 else [3]
    for idx in check_order:
        if idx >= len(cols):
            continue
        text = cols[idx].get_text(strip=True).replace(" ", "")
        if _DATE_PATTERN.match(text):
            return text
    for col in cols:
        text = col.get_text(strip=True).replace(" ", "")
        if _DATE_PATTERN.match(text):
            return text
    return ""


# ── FIX-RPT-1: 리포트 본문 크롤링 ───────────────────────────────────────────

def _fetch_report_body(url: str) -> str:
    """
    네이버 금융 리포트 페이지에서 본문 텍스트 추출.
    실패 시 빈 문자열 반환.
    """
    if not url:
        return ""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=8)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        for selector in [
            "div.report_contents",
            "div#content",
            "div.view_cnt",
            "td.view_cnt",
            "div.research_cont",
            "div.cont",
        ]:
            tag = soup.select_one(selector)
            if tag:
                text = tag.get_text(separator=" ", strip=True)
                if len(text) > 100:
                    return text[:_MAX_BODY_CHARS]

        paragraphs = soup.select("p")
        combined = " ".join(
            p.get_text(strip=True) for p in paragraphs
            if len(p.get_text(strip=True)) > 20
        )
        return combined[:_MAX_BODY_CHARS] if combined else ""

    except Exception as e:
        print(f"  [본문 크롤링 실패] {url}: {e}")
        return ""


def _summarize_report_with_claude(
    stock_name: str,
    report_title: str,
    broker: str,
    body_text: str,
    opinion: str,
    target_price: str,
    api_key: str,
) -> str:
    """
    리포트 본문을 Claude에게 전달해 핵심 1문장을 추출.
    본문이 없거나 API 실패 시 빈 문자열 반환 (추정 문장 생성 금지).
    """
    if not body_text or len(body_text) < 50:
        return ""
    if not api_key:
        return ""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        context_parts = []
        if opinion:
            context_parts.append(f"투자의견: {opinion}")
        if target_price:
            context_parts.append(f"목표주가: {target_price}원")
        context_str = (" (" + ", ".join(context_parts) + ")") if context_parts else ""

        prompt = (
            f"다음은 {broker}가 발간한 [{stock_name}] 종목 리포트입니다{context_str}.\n"
            f"리포트 제목: {report_title}\n\n"
            f"리포트 본문:\n{body_text}\n\n"
            f"[지시]\n"
            f"위 리포트 본문에서 투자자에게 가장 중요한 핵심 내용을 정확히 1문장으로 요약하세요.\n"
            f"- 반드시 본문에 실제로 있는 내용만 사용하세요.\n"
            f"- 본문에 없는 수치, 전망, 의견을 추가하지 마세요.\n"
            f"- 문장은 50자 이내로 간결하게 작성하세요.\n"
            f"- 요약 문장만 출력하고, 다른 설명은 쓰지 마세요."
        )

        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        result = msg.content[0].text.strip().strip('"').strip("'").strip()
        return result if len(result) > 5 else ""

    except Exception as e:
        print(f"  [Claude 요약 실패] {stock_name} - {report_title}: {e}")
        return ""


# ── 수집 메인 (FIX-COST-1: Claude 호출 없이 메타데이터만 수집) ───────────────

def collect_naver_research(days: int = REPORT_DAYS) -> list:
    """
    네이버 금융 리서치 company_list.naver 수집.
    FIX-COST-1: 이 단계에서는 Claude 호출 없이 메타데이터만 수집.
                Claude 요약은 classify 후 대표 리포트에만 호출함.
    """
    results = []

    session = requests.Session()
    adapter = HTTPAdapter(max_retries=Retry(total=0))
    session.mount("https://", adapter)

    for page in range(1, 6):
        url = f"https://finance.naver.com/research/company_list.naver?&page={page}"
        try:
            resp = session.get(url, headers=_HEADERS, timeout=10)
            resp.encoding = "euc-kr"
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("table.type_1 tr")

            page_has_recent = False
            for row in rows:
                cols = row.select("td")
                if len(cols) < 5:
                    continue

                stock_name = cols[0].get_text(strip=True)
                if not stock_name or stock_name in ("종목명", "기업명"):
                    continue

                report_title = cols[1].get_text(strip=True)
                broker       = cols[2].get_text(strip=True)

                date_str = _find_date_col(cols)
                if not date_str:
                    continue
                if not is_within_days(date_str, days):
                    continue
                page_has_recent = True

                if not broker:
                    for known_broker in BROKERS:
                        if known_broker in report_title:
                            broker = known_broker
                            break
                    if not broker:
                        broker = "기타증권사"

                link_tag = cols[1].find("a")
                link     = _build_link(link_tag.get("href", "")) if link_tag else ""

                pdf_link = ""
                pdf_tag  = cols[3].find("a") if len(cols) > 3 else None
                if pdf_tag and pdf_tag.get("href", "").endswith(".pdf"):
                    pdf_link = _build_link(pdf_tag.get("href", ""))

                opinion      = _extract_opinion(report_title)
                target_price = _extract_target_price(report_title)
                new_cov      = is_new_coverage(report_title)

                results.append({
                    "source_type":      "애널리스트",
                    "source_name":      broker,
                    "stock_name":       stock_name,
                    "report_title":     report_title,
                    "analyst":          "",
                    "target_price":     target_price,
                    "opinion":          opinion,
                    "date":             date_str,
                    "new_coverage":     new_cov,
                    "analyst_category": "",
                    # FIX-COST-1: ai_summary는 classify 후 대표 리포트에만 채움
                    "ai_summary":       "",
                    "title":            f"[{broker}] {stock_name} - {report_title}",
                    "summary":          "",
                    "link":             link or pdf_link,
                    "section":          "section3",
                })

            if not page_has_recent and page > 1:
                print(f"  [리서치] 페이지 {page}: 최근 데이터 없음 → 종료")
                break

            time.sleep(0.5)

        except Exception as e:
            print(f"  [리서치 페이지 {page}] 오류: {e}")
            break

    return results


def classify_analyst_reports(reports: list) -> dict:
    """
    수집된 리포트를 3가지 카테고리로 분류.
    BUG-AC-4 유지 — 종목당 1카테고리 보장.
    """
    stock_groups: dict[str, list] = defaultdict(list)
    for r in reports:
        sn = r.get("stock_name", "")
        if sn:
            stock_groups[sn].append(r)

    simultaneous_out  = []
    new_coverage_out  = []
    single_broker_out = []

    for stock_name, stock_reports in stock_groups.items():
        unique_brokers = list(dict.fromkeys(
            r.get("source_name", "") for r in stock_reports
            if r.get("source_name", "")
        ))

        if len(unique_brokers) >= 2:
            primary = stock_reports[0].copy()
            brokers_str = " / ".join(unique_brokers)

            has_new_cov = any(r.get("new_coverage", False) for r in stock_reports)
            primary["new_coverage"]         = has_new_cov
            primary["source_name"]          = brokers_str
            primary["simultaneous_brokers"] = unique_brokers
            primary["broker_count"]         = len(unique_brokers)
            primary["all_reports"]          = stock_reports
            primary["title"] = (
                f"[동시언급 {len(unique_brokers)}사] {stock_name} - "
                f"{stock_reports[0].get('report_title', '')}"
            )
            # ai_summary는 아직 빈 문자열 — 이후 _enrich_with_summaries에서 채움
            primary["ai_summary"] = ""
            primary["summary"]    = ""

            opinions = [r.get("opinion", "") for r in stock_reports if r.get("opinion")]
            opinion_priority = ["매수", "BUY", "비중확대", "중립", "HOLD", "비중축소", "매도"]
            for op in opinion_priority:
                if op in opinions:
                    primary["opinion"] = op
                    break

            simultaneous_out.append(primary)
            continue

        has_new_cov = any(r.get("new_coverage", False) for r in stock_reports)
        if has_new_cov:
            representative = next(
                (r for r in stock_reports if r.get("new_coverage", False)),
                stock_reports[0]
            )
            new_coverage_out.append(representative)
        else:
            single_broker_out.append(stock_reports[0])

    return {
        "simultaneous":  simultaneous_out,
        "new_coverage":  new_coverage_out,
        "single_broker": single_broker_out,
    }


def _enrich_with_summaries(all_classified: list, api_key: str) -> None:
    """
    FIX-COST-1: 분류 완료된 대표 리포트에만 본문 크롤링 + Claude 요약 실행.
    all_classified 리스트를 직접 수정(in-place).
    Claude 호출 횟수 = 최종 표시 리포트 수와 동일 — 낭비 없음.
    """
    if not api_key:
        print("  [요약 스킵] api_key 없음")
        return

    total = len(all_classified)
    print(f"  [요약 시작] 대표 리포트 {total}건에 대해 Claude 요약 실행")

    for i, r in enumerate(all_classified, 1):
        link         = r.get("link", "")
        stock_name   = r.get("stock_name", "")
        report_title = r.get("report_title") or r.get("title", "")
        broker       = r.get("source_name", "")
        opinion      = r.get("opinion", "")
        target_price = r.get("target_price", "")

        if not link:
            print(f"  [{i}/{total}] {stock_name}: 링크 없음 → 스킵")
            continue

        print(f"  [{i}/{total}] 크롤링: {stock_name} - {report_title[:30]}...")
        body_text = _fetch_report_body(link)
        time.sleep(0.3)  # 과부하 방지

        if not body_text:
            print(f"  [{i}/{total}] {stock_name}: 본문 없음 → 스킵")
            continue

        ai_summary = _summarize_report_with_claude(
            stock_name, report_title, broker,
            body_text, opinion, target_price, api_key
        )

        if ai_summary:
            r["ai_summary"] = ai_summary
            r["summary"]    = ai_summary
            print(f"  [{i}/{total}] 완료: {stock_name} → {ai_summary}")
        else:
            print(f"  [{i}/{total}] {stock_name}: 요약 실패 → 빈 문자열 유지")


def _dedupe_and_classify(reports: list) -> dict:
    """중복 제거 후 분류 반환 (내부 헬퍼)"""
    seen_raw = set()
    deduped  = []
    for r in reports:
        key = f"{r.get('stock_name','')}_{r.get('source_name','')}_{r.get('date','')}"
        if key not in seen_raw:
            seen_raw.add(key)
            deduped.append(r)
    return classify_analyst_reports(deduped)


def collect_analyst(api_key: str = "") -> list:
    """애널리스트 리포트 수집 메인 함수

    수집 전략:
    - 오늘 리포트: 동시언급 + 신규커버리지 + 단독언급 전체 수집
    - 어제 리포트: 동시언급 + 신규커버리지만 선별 (단독언급 제외)
                   오늘 이미 포함된 종목은 중복 제외
    """
    print("\n=== 섹션 3: 애널리스트 리포트 수집 ===")

    KST           = timezone(timedelta(hours=9))
    today_str     = datetime.now(KST).strftime("%y.%m.%d")
    yesterday_str = (datetime.now(KST) - timedelta(days=1)).strftime("%y.%m.%d")

    # ── 오늘 리포트 수집 ──────────────────────────────────────────
    # ★ 버그 수정: 예전에는 collect_naver_research()를 인자 없이 호출해
    # 기본값(이 모듈 상단의 REPORT_DAYS=2, config.py의 REPORT_DAYS=1과는
    # 다른 상수임 — 이름이 같아 혼동되기 쉽다)을 그대로 썼다. is_within_days()는
    # ">=" 비교라 days=2면 오늘/어제/그제(최대 3일치)가 전부 통과하는데, 이
    # 결과를 그대로 today_classified에 넣고 무조건 report_day="today"로
    # 라벨링했다 — 실제로 이틀 전 리포트가 "오늘 리포트"로 영상 내레이션에
    # 노출되는 사고가 있었다(예: 07-16 수집인데 07-14자 리포트가 "오늘"로 표시).
    # "어제" 수집과 동일한 패턴(넉넉히 가져온 뒤 정확한 날짜로 필터링)으로 통일한다.
    print(f"  [오늘 {today_str}] 수집 중...")
    today_reports_raw = collect_naver_research(days=2)
    today_reports = [r for r in today_reports_raw if r.get("date", "") == today_str]
    today_classified = _dedupe_and_classify(today_reports)

    for r in today_classified["simultaneous"]:
        r["analyst_category"] = "simultaneous"
        r["report_day"] = "today"
    for r in today_classified["new_coverage"]:
        r["analyst_category"] = "new_coverage"
        r["report_day"] = "today"
    for r in today_classified["single_broker"]:
        r["analyst_category"] = "single_broker"
        r["report_day"] = "today"
        # 웹 브리핑의 "단독 언급" 섹션은 오늘자 전체를 그대로 노출하지만,
        # 영상 내레이션(build_brokerage_reports)은 유의미한 단독언급만 골라 써야
        # 하므로 제거하지 않고 significance_reason만 태깅해 둔다.
        significant, reason = _is_significant_single(r)
        if significant:
            r["significance_reason"] = reason

    today_all = (
        today_classified["simultaneous"]
        + today_classified["new_coverage"]
        + today_classified["single_broker"]
    )
    print(
        f"  → 오늘: 동시언급 {len(today_classified['simultaneous'])}건 / "
        f"신규커버리지 {len(today_classified['new_coverage'])}건 / "
        f"단독언급 {len(today_classified['single_broker'])}건"
    )

    # ── 어제 리포트 수집 (days=2 명시 전달) ──────────────────────
    print(f"  [어제 {yesterday_str}] 동시언급·신규커버리지 선별 중...")
    yest_reports = collect_naver_research(days=2)

    yest_classified = _dedupe_and_classify(yest_reports)

    # 오늘 이미 포함된 종목 집합
    today_names = {r.get("stock_name", "") for r in today_all}

    yest_selected = []

    # 동시언급 + 신규커버리지 (전체 포함)
    for category in ["simultaneous", "new_coverage"]:
        for r in yest_classified[category]:
            if (r.get("date", "") == yesterday_str
                    and r.get("stock_name", "") not in today_names):
                r["analyst_category"] = category
                r["report_day"] = "yesterday"
                yest_selected.append(r)

    # 단독언급 중 유의미한 것만 선별 (대형사+목표주가상향 or 투자의견상향)
    yest_single_significant = []
    for r in yest_classified["single_broker"]:
        if (r.get("date", "") == yesterday_str
                and r.get("stock_name", "") not in today_names):
            significant, reason = _is_significant_single(r)
            if significant:
                r["analyst_category"] = "single_broker"
                r["report_day"] = "yesterday"
                r["significance_reason"] = reason
                yest_single_significant.append(r)
                print(f"  → 어제 단독언급 선별: {r.get('stock_name','')} ({reason})")

    yest_selected.extend(yest_single_significant)

    print(
        f"  → 어제 선별: {len(yest_selected)}건 "
        f"(동시언급+신규커버리지 + 유의미 단독언급 {len(yest_single_significant)}건)"
    )

    # ── 최종 합산 ─────────────────────────────────────────────────
    all_classified = today_all + yest_selected
    print(f"  → 최종: {len(all_classified)}건 (오늘 {len(today_all)}건 + 어제 {len(yest_selected)}건)")

    if today_classified["simultaneous"]:
        names = [r.get("stock_name", "") for r in today_classified["simultaneous"]]
        print(f"  → 오늘 동시언급 종목: {', '.join(names)}")

    # 대표 리포트에만 Claude 요약 실행
    _enrich_with_summaries(all_classified, api_key)

    return all_classified
