#!/usr/bin/env python3
"""
data/briefing_data.json을 손으로 수정한 뒤 docs/index.html을 그 내용과
다시 맞추기 위한 스크립트. Claude/Gemini/수집기를 전혀 호출하지 않으므로
비용이 들지 않고 몇 초 안에 끝난다.

stock-briefing-v3-1의 scripts/regenerate_preview.py와 같은 목적 —
data/briefing_data.json은 step2(영상 생성)가 raw.githubusercontent.com으로
직접 읽어가는 "원본"이고, docs/index.html은 사람이 보는 프리뷰 렌더링일
뿐이다. 관리자 페이지에서 JSON을 고친 뒤 이 스크립트로 프리뷰도 맞춘다.

사용법:
    1. data/briefing_data.json 을 직접 수정한다.
    2. python scripts/regenerate_preview.py
    3. git diff로 docs/index.html 변경분을 확인하고 커밋한다.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzer.report_update_html import generate_report_update_html  # noqa: E402

DATA_FILE = "data/briefing_data.json"
OUT_FILE = "docs/index.html"


def main():
    if not os.path.exists(DATA_FILE):
        print(f"[재생성] {DATA_FILE} 없음 → 중단")
        sys.exit(1)

    with open(DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)

    # main.py의 정식 생성 흐름은 새 index.html을 쓰기 전에 기존 파일을
    # docs/archive/{날짜}.html로 백업하지만, 이 스크립트는 "같은 날 데이터를
    # 교정"하는 용도라 그 시점에 이미 아카이브가 끝나 있다 — 여기서 다시
    # 아카이브하면 교정 전 상태가 잘못 아카이브에 남는다. 그래서 아카이빙 없이
    # docs/index.html만 덮어쓴다.
    html = generate_report_update_html(data)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[재생성] {DATA_FILE} → {OUT_FILE} 완료 (AI 호출 없음)")


if __name__ == "__main__":
    main()
