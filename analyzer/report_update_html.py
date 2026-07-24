# analyzer/report_update_html.py
"""
report_update(STEP-2) 재설계 스키마용 프리뷰 HTML 렌더러.

기존 analyzer/html_generator.py(V3/V3-1과 공유하는 "전체 재브리핑" 스키마용)는
이 레포의 새 스키마(step1_recap/morning_reaction/analyst_briefing/
ai_strategy_update)와 구조가 달라 그대로 못 쓴다. 시각 스타일(다크 테마,
색상)만 맞추고 섹션 구성은 새로 만든다.
"""
import html as _he
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

_TIER_LABEL = {
    "shorts": ("🩳 숏폼", "#ffa94d"),
    "mid":    ("📎 미드폼", "#74c0fc"),
    "full":   ("📖 풀버전", "#51cf66"),
}

_CATEGORY_META = {
    "simultaneous":        {"color": "#ff922b", "badge": "🔥 동시언급"},
    "new_coverage":        {"color": "#51cf66", "badge": "🆕 신규 커버리지"},
    "single_significant":  {"color": "#f06595", "badge": "💎 오늘의 픽"},
}


def _pct_html(pct) -> str:
    try:
        p = float(pct)
    except (TypeError, ValueError):
        p = 0.0
    color = "#ff6b6b" if p > 0 else "#74c0fc" if p < 0 else "#adb5bd"
    arrow = "▲" if p > 0 else "▼" if p < 0 else "━"
    return f'<span style="color:{color};">{arrow} {p:+.2f}%</span>'


def _render_recap(recap: dict) -> str:
    def _chips(names):
        if not names:
            return '<span style="color:#666;">없음</span>'
        return " ".join(
            f'<span class="chip">{_he.escape(n)}</span>' for n in names
        )
    gist = recap.get("market_summary_gist", "")
    gist_html = f'<p style="color:#adb5bd;margin-top:.5rem;">{_he.escape(gist)}</p>' if gist else ""
    return f"""
<div class="section">
  <div class="section-title">📌 유튜브 분석 브리핑 요약</div>
  <div class="recap-row"><b>대형주도주</b> {_chips(recap.get('market_leaders'))}</div>
  <div class="recap-row"><b>관심종목</b> {_chips(recap.get('stocks'))}</div>
  {gist_html}
</div>"""


def _render_reaction(reaction: list, before_time: str = "", after_time: str = "") -> str:
    before_label = f"개장 전 {before_time} 현재" if before_time else "개장 전 현재"
    after_label  = f"개장 후 {after_time} 현재" if after_time else "개장 후 현재"
    if not reaction:
        return """
<div class="section">
  <div class="section-title">📈 개장 전후 주요 종목 주가 변동</div>
  <p style="color:#666;">데이터 없음</p>
</div>"""
    rows = ""
    for r in reaction:
        rows += f"""
    <tr>
      <td>{_he.escape(r.get('name',''))}</td>
      <td>{r.get('step1_price',0):,}원 {_pct_html(r.get('step1_change_pct',0))}</td>
      <td>{r.get('morning_price',0):,}원 {_pct_html(r.get('morning_change_pct',0))}</td>
    </tr>"""
    return f"""
<div class="section">
  <div class="section-title">📈 개장 전후 주요 종목 주가 변동</div>
  <table class="reaction-table">
    <tr><th>종목</th><th>{_he.escape(before_label)}</th><th>{_he.escape(after_label)}</th></tr>
    {rows}
  </table>
</div>"""


def _render_briefing(briefing: dict) -> str:
    themes = briefing.get("sector_themes", [])
    stocks = briefing.get("stocks", [])

    themes_html = ""
    for t in themes:
        themes_html += (
            f'<div class="theme-badge">🎯 {_he.escape(t.get("sector",""))} '
            f'({t.get("report_count",0)}건) — {_he.escape(t.get("narrative",""))}</div>'
        )

    cards = ""
    for s in stocks:
        cat = s.get("category", "single_significant")
        meta = _CATEGORY_META.get(cat, _CATEGORY_META["single_significant"])
        brokers = s.get("brokers", [])
        brokers_str = ", ".join(brokers) if isinstance(brokers, list) else str(brokers)
        badge = (f'<span class="cat-badge" style="background:{meta["color"]}22;'
                 f'color:{meta["color"]};">{meta["badge"]}</span>') if meta["badge"] else ""
        cards += f"""
<div class="stock-card" style="border-left-color:{meta['color']};">
  <div class="stock-card-header">
    <b>{_he.escape(s.get('name',''))}</b>
    <span style="color:#868e96;">🏦 {_he.escape(brokers_str)}</span>
    {badge}
  </div>
  <div style="color:#adb5bd;font-size:.85rem;margin-top:.3rem;">
    투자의견: {_he.escape(s.get('opinion','') or '-')} ·
    목표주가: {_he.escape(s.get('target_price','') or '-')}원
  </div>
  <p style="color:#e6edf3;font-size:.9rem;margin-top:.5rem;">{_he.escape(s.get('analysis',''))}</p>
</div>"""

    if not cards:
        cards = '<p style="color:#666;">오늘 리포트 데이터 없음</p>'

    return f"""
<div class="section">
  <div class="section-title">📋 증권사 리포트 브리핑</div>
  {themes_html}
  {cards}
</div>"""


def _render_strategy_update(text: str) -> str:
    if not text:
        return ""
    return f"""
<div class="section">
  <div class="section-title">🤖 AI 전략 업데이트</div>
  <p style="color:#adb5bd;white-space:pre-wrap;">{_he.escape(text)}</p>
</div>"""


def generate_report_update_html(data: dict) -> str:
    briefing_date = data.get("briefing_date", "")
    generated_at  = data.get("generated_at", "")
    tier          = data.get("length_tier", "shorts")
    tier_label, tier_color = _TIER_LABEL.get(tier, _TIER_LABEL["shorts"])

    css = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { background:#0d1117; color:#e6edf3; font-family:'Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif; line-height:1.6; padding:0 0 4rem; }
.container { max-width: 900px; margin: 0 auto; padding: 0 1rem; }
.header { text-align:center; padding:2.5rem 1rem 1.5rem; border-bottom:1px solid #30363d; margin-bottom:2rem; }
.header h1 { font-size:1.8rem; font-weight:700; }
.subtitle { color:#8b949e; font-size:.9rem; margin-top:.4rem; }
.tier-badge { display:inline-block; margin-top:.6rem; padding:.3rem .9rem; border-radius:20px; font-weight:700; font-size:.85rem; }
.section { margin-bottom:2.2rem; }
.section-title { font-size:1.1rem; font-weight:700; border-left:4px solid #58a6ff; padding-left:.75rem; margin-bottom:1rem; }
.recap-row { margin-bottom:.5rem; font-size:.92rem; }
.chip { display:inline-block; background:#21262d; border:1px solid #30363d; border-radius:14px; padding:.15rem .7rem; margin:.15rem .2rem; font-size:.85rem; color:#58a6ff; }
.reaction-table { width:100%; border-collapse:collapse; font-size:.88rem; }
.reaction-table th, .reaction-table td { text-align:left; padding:.5rem .6rem; border-bottom:1px solid #30363d; }
.theme-badge { background:#21262d; border-radius:8px; padding:.5rem .8rem; margin-bottom:.5rem; font-size:.88rem; color:#ffd43b; }
.stock-card { background:#161b22; border:1px solid #30363d; border-left-width:3px; border-radius:8px; padding:.85rem 1rem; margin-bottom:.75rem; }
.stock-card-header { display:flex; align-items:center; gap:.6rem; flex-wrap:wrap; }
.cat-badge { font-size:.7rem; padding:.15rem .5rem; border-radius:4px; font-weight:700; }
.disclaimer { margin-top:2.5rem; padding:1rem 1.4rem; background:#161b22; border:1px solid #30363d; border-left:4px solid #58a6ff44; border-radius:8px; font-size:.78rem; color:#6e7681; line-height:1.8; text-align:center; }
"""

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>report_update — {briefing_date}</title>
<style>{css}</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>📡 증권사 리포트 핵심 브리핑</h1>
    <div class="subtitle">{briefing_date} · 생성 시각 {generated_at} KST</div>
    <div class="tier-badge" style="background:{tier_color}22;color:{tier_color};border:1px solid {tier_color}55;">{tier_label}</div>
  </div>
  {_render_recap(data.get('step1_recap', {}))}
  {_render_reaction(data.get('morning_reaction', []), data.get('step1_recap', {}).get('generated_at', ''), generated_at)}
  {_render_briefing(data.get('analyst_briefing', {}))}
  {_render_strategy_update(data.get('ai_strategy_update', ''))}
  <div class="disclaimer">
    <strong>📌 투자 유의사항</strong>
    본 브리핑은 증권사 리포트 등 공개 데이터를 AI가 수집·분석한 참고 자료입니다.
    투자 권유 또는 종목 추천이 아니며, 수익을 보장하지 않습니다.
    최종 투자 판단과 그에 따른 책임은 전적으로 본인에게 있습니다.
  </div>
</div>
</body>
</html>"""
