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

# SIMUL-CAP-1: 동시언급 종목이 20개를 넘는 날이 있어(가독성 저하) 동시
# 언급한 증권사 수가 많은 순으로 상위 N개만 노출한다.
_SIMULTANEOUS_LIMIT = 10
# TODAY-PICK-1: 캡에서 잘려나간 동시언급 종목 중 투자의견이 뚜렷하게
# 긍정적인 종목은 버리지 않고 "오늘의 픽"으로 승격시킨다 — ai_analyzer.py의
# _POSITIVE_OPINION_KEYWORDS와 동일한 기준.
_POSITIVE_OPINION_KEYWORDS = {
    "매수", "buy", "강력매수", "strong buy", "비중확대", "overweight",
    "outperform", "시장수익률상회", "적극매수",
}
_TODAY_PICK_LIMIT = 3


def _is_positive_opinion(opinion) -> bool:
    if not opinion:
        return False
    o = str(opinion).strip().lower()
    return any(k in o for k in _POSITIVE_OPINION_KEYWORDS)


def _pct_html(pct) -> str:
    try:
        p = float(pct)
    except (TypeError, ValueError):
        p = 0.0
    color = "#ff6b6b" if p > 0 else "#74c0fc" if p < 0 else "#adb5bd"
    arrow = "▲" if p > 0 else "▼" if p < 0 else "━"
    return f'<span style="color:{color};">{arrow} {p:+.2f}%</span>'


def _broker_opinions_html(details: list) -> str:
    """증권사별 투자의견·목표주가를 증권사명과 함께 한 줄씩 나열한다 —
    동시언급 종목은 증권사마다 의견/목표가가 다를 수 있어 하나로 합쳐
    보여주면 정보가 유실된다."""
    rows = ""
    for d in details:
        broker = d.get("broker", "")
        if not broker:
            continue
        opinion      = d.get("opinion", "") or "-"
        target_price = d.get("target_price", "")
        target_str   = f"{target_price}원" if target_price else "-"
        rows += (
            f'<div style="display:flex;justify-content:space-between;gap:.75rem;">'
            f'<span>{_he.escape(broker)}</span>'
            f'<span>{_he.escape(opinion)} · {_he.escape(target_str)}</span>'
            f'</div>'
        )
    return rows


def _stock_card_html(s: dict, cat_override: str = None) -> str:
    cat = cat_override or s.get("category", "single_significant")
    meta = _CATEGORY_META.get(cat, _CATEGORY_META["single_significant"])
    brokers = s.get("brokers", [])
    brokers_str = ", ".join(brokers) if isinstance(brokers, list) else str(brokers)
    badge = (f'<span class="cat-badge" style="background:{meta["color"]}22;'
             f'color:{meta["color"]};">{meta["badge"]}</span>') if meta["badge"] else ""

    broker_rows = _broker_opinions_html(s.get("broker_details") or [])
    opinions_html = (
        f'<div style="color:#adb5bd;font-size:.85rem;margin-top:.3rem;'
        f'display:flex;flex-direction:column;gap:.2rem;">{broker_rows}</div>'
        if broker_rows else
        f'<div style="color:#adb5bd;font-size:.85rem;margin-top:.3rem;">'
        f"투자의견: {_he.escape(s.get('opinion','') or '-')} · "
        f"목표주가: {_he.escape(s.get('target_price','') or '-')}원</div>"
    )

    return f"""
<div class="stock-card" style="border-left-color:{meta['color']};">
  <div class="stock-card-header">
    <b>{_he.escape(s.get('name',''))}</b>
    <span style="color:#868e96;">🏦 {_he.escape(brokers_str)}</span>
    {badge}
  </div>
  {opinions_html}
  <p style="color:#e6edf3;font-size:.9rem;margin-top:.5rem;">{_he.escape(s.get('analysis',''))}</p>
</div>"""


def _subsection_title(text: str, color: str) -> str:
    return (f'<div class="rpt-subsection-title" style="border-left-color:{color};'
            f'color:{color};">{text}</div>')


def _render_briefing(briefing: dict) -> str:
    themes = briefing.get("sector_themes", [])
    stocks = briefing.get("stocks", [])

    themes_html = ""
    for t in themes:
        themes_html += (
            f'<div class="theme-badge">🎯 {_he.escape(t.get("sector",""))} '
            f'({t.get("report_count",0)}건) — {_he.escape(t.get("narrative",""))}</div>'
        )

    simultaneous = [s for s in stocks if s.get("category") == "simultaneous"]
    new_coverage = [s for s in stocks if s.get("category") == "new_coverage"]
    picks        = [s for s in stocks if s.get("category") not in ("simultaneous", "new_coverage")]

    # SIMUL-CAP-1: 동시언급 종목이 너무 많으면(예: 26개) 한눈에 보기 어려워
    # 동시 언급한 증권사 수가 많은 순으로 상위 _SIMULTANEOUS_LIMIT개만 보여준다.
    simultaneous_sorted = sorted(
        simultaneous, key=lambda s: len(s.get("brokers") or []), reverse=True
    )
    simultaneous_top      = simultaneous_sorted[:_SIMULTANEOUS_LIMIT]
    simultaneous_overflow = simultaneous_sorted[_SIMULTANEOUS_LIMIT:]

    # TODAY-PICK-1: 캡에서 빠진 동시언급 종목 중 투자의견이 긍정적인 종목은
    # "오늘의 픽" 후보로 승격 — 원래 있던 픽(single_significant)과 합쳐
    # 증권사 수가 많은(더 확신도 높은) 순으로 최종 _TODAY_PICK_LIMIT개만 노출.
    promoted = sorted(
        (s for s in simultaneous_overflow if _is_positive_opinion(s.get("opinion"))),
        key=lambda s: len(s.get("brokers") or []), reverse=True,
    )
    picks_final = (picks + promoted)[:_TODAY_PICK_LIMIT]

    cards = ""
    if simultaneous_top:
        cards += _subsection_title(
            f"🔥 동시언급 상위 {len(simultaneous_top)}개 (증권사 수 순)", "#ff922b"
        )
        for s in simultaneous_top:
            cards += _stock_card_html(s, "simultaneous")
    if new_coverage:
        cards += _subsection_title("🆕 신규 커버리지", "#51cf66")
        for s in new_coverage:
            cards += _stock_card_html(s, "new_coverage")
    if picks_final:
        cards += _subsection_title("💎 오늘의 픽", "#f06595")
        for s in picks_final:
            cards += _stock_card_html(s, "single_significant")

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


# ── STEP-2 영상 섹션 ─────────────────────────────────────────────────────
# 관리자 페이지에서 유튜브 링크를 등록하기 전에는 data.video가 없으므로
# 섹션 자체가 렌더링되지 않는다 (기본 숨김). STEP-2 영상은 브리핑 생성
# *이후*에 업로드되는 인과관계상, 생성 시점에는 항상 비어 있다가 관리자가
# 나중에 링크를 채워 넣으면 그때부터 표출된다.
def _render_video_section(video: dict) -> str:
    if not video or not video.get("video_id"):
        return ""
    url       = video.get("url", "")
    title     = video.get("title", "")
    thumbnail = video.get("thumbnail") or f'https://i.ytimg.com/vi/{video["video_id"]}/hqdefault.jpg'
    return f"""
  <div class="section">
    <a href="{_he.escape(url)}" target="_blank" rel="noopener" class="video-card">
      <img src="{_he.escape(thumbnail)}" alt="영상 썸네일" class="video-thumb" loading="lazy">
      <div class="video-info">
        <span class="video-badge">▶ 오늘의 영상</span>
        <span class="video-title">{_he.escape(title)}</span>
      </div>
    </a>
  </div>"""


def generate_report_update_html(data: dict) -> str:
    briefing_date = data.get("briefing_date", "")
    generated_at  = data.get("generated_at", "")
    tier          = data.get("length_tier", "shorts")
    tier_label, tier_color = _TIER_LABEL.get(tier, _TIER_LABEL["shorts"])
    video_html    = _render_video_section(data.get("video"))

    css = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { background:#0d1117; color:#e6edf3; font-family:'Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif; line-height:1.6; padding:0 0 4rem; }
.container { max-width: 900px; margin: 0 auto; padding: 0 1rem; }
.header { text-align:center; padding:2.5rem 1rem 1.5rem; border-bottom:1px solid #30363d; margin-bottom:2rem; }
.header h1 { font-size:1.8rem; font-weight:700; }
.subtitle { color:#8b949e; font-size:.9rem; margin-top:.4rem; }
.tier-badge { display:inline-block; margin-top:.6rem; padding:.3rem .9rem; border-radius:20px; font-weight:700; font-size:.85rem; }
.video-card {
  display:flex; align-items:center; gap:1rem; background:#161b22; border:1px solid #30363d;
  border-radius:12px; padding:.8rem; text-decoration:none; transition:border-color .15s;
}
.video-card:hover { border-color:#58a6ff; text-decoration:none; }
.video-thumb { width:160px; max-width:40vw; border-radius:8px; flex-shrink:0; display:block; }
.video-info { display:flex; flex-direction:column; gap:.4rem; min-width:0; }
.video-badge {
  align-self:flex-start; font-size:.72rem; font-weight:700; color:#000;
  background:#ff4d4d; padding:.15rem .6rem; border-radius:20px;
}
.video-title {
  font-size:1rem; font-weight:700; color:#e6edf3;
  overflow:hidden; text-overflow:ellipsis;
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
}
@media (max-width:600px) { .video-thumb{width:110px;} .video-title{font-size:.88rem;} }
.section { margin-bottom:2.2rem; }
.section-title { font-size:1.1rem; font-weight:700; border-left:4px solid #58a6ff; padding-left:.75rem; margin-bottom:1rem; }
.theme-badge { background:#21262d; border-radius:8px; padding:.5rem .8rem; margin-bottom:.5rem; font-size:.88rem; color:#ffd43b; }
.rpt-subsection-title { font-size:.85rem; font-weight:700; margin:1rem 0 .5rem; padding-left:.5rem; border-left:3px solid; }
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
  {video_html}
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
