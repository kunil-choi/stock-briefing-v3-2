# tests/test_analyst_collector.py
"""
collectors/analyst_collector.py의 collect_analyst() 날짜 라벨링 검증 스크립트.
pytest 미사용, 순수 assert 기반. 네트워크 불필요(collect_naver_research를
monkeypatch로 대체).

실제 사고 재현: collect_analyst()의 "오늘" 수집이 collect_naver_research()를
인자 없이 호출해 이 모듈 상단의 REPORT_DAYS=2(config.py의 REPORT_DAYS=1과는
다른, 이름만 같은 상수)를 기본값으로 썼다. is_within_days()는 ">=" 비교라
days=2면 오늘/어제/그제(최대 3일치)가 다 통과하는데, 그 결과를 무조건
report_day="today"로 라벨링해 실제 운영 데이터에서 이틀 전 리포트가
"오늘 리포트"로 노출되는 사고가 있었다(예: 07-16 수집인데 07-14자 리포트가
"오늘"로 표시).

실행: python tests/test_analyst_collector.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, "..")
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import collectors.analyst_collector as analyst_collector  # noqa: E402

KST = timezone(timedelta(hours=9))


def test_today_reports_exclude_older_dates():
    """collect_analyst()가 만드는 today_all에는 오늘 날짜 리포트만 있어야 하고,
    어제/그제 리포트가 report_day="today"로 잘못 라벨링되면 안 된다."""
    now = datetime.now(KST)
    today_str = now.strftime("%y.%m.%d")
    yesterday_str = (now - timedelta(days=1)).strftime("%y.%m.%d")
    day_before_str = (now - timedelta(days=2)).strftime("%y.%m.%d")

    fake_reports = {
        2: [
            {"stock_name": "오늘종목A", "source_name": "키움증권", "date": today_str,
             "report_title": "오늘종목A 실적 호조", "opinion": "", "new_coverage": False},
            {"stock_name": "오늘종목A", "source_name": "삼성증권", "date": today_str,
             "report_title": "오늘종목A 실적 호조", "opinion": "", "new_coverage": False},
            {"stock_name": "어제종목B", "source_name": "NH투자증권", "date": yesterday_str,
             "report_title": "어제종목B 커버리지 개시", "opinion": "", "new_coverage": False},
            {"stock_name": "그제종목C", "source_name": "대신증권", "date": day_before_str,
             "report_title": "그제종목C 목표주가 상향", "opinion": "", "new_coverage": False},
        ],
    }

    def fake_collect_naver_research(days=2):
        return fake_reports.get(days, [])

    original = analyst_collector.collect_naver_research
    analyst_collector.collect_naver_research = fake_collect_naver_research
    try:
        result = analyst_collector.collect_analyst(api_key="")
    finally:
        analyst_collector.collect_naver_research = original

    today_labeled_names = {r["stock_name"] for r in result if r.get("report_day") == "today"}
    assert "그제종목C" not in today_labeled_names, (
        f"그제(2일 전) 리포트가 report_day='today'로 잘못 라벨링됨: {today_labeled_names}"
    )
    assert "오늘종목A" in today_labeled_names, "오늘 리포트가 today_all에서 누락됨"
    print(f"✅ today_all에 라벨링된 종목: {today_labeled_names} (그제 리포트 미포함 확인)")


if __name__ == "__main__":
    test_today_reports_exclude_older_dates()
    print("\n✅ analyst_collector 테스트 전체 통과")
