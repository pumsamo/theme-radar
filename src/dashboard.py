"""단일 HTML 대시보드 — 신문 브리핑 톤, 서버 없이 브라우저로 연다.

레이아웃 원칙(지시문 4-6): 최상단 결론이 3초 안에 읽혀야 한다. 테마 전체 나열이 아니라
필터를 통과한 '오늘의 픽'만 위에 올리고, 풀은 접어 둔다.
PC에서는 3컬럼, 모바일에서는 1컬럼으로 접힌다.
"""
from __future__ import annotations

import html
import shutil
from datetime import date as _date
from pathlib import Path

import boot  # noqa: F401
import viewdata

OUT = Path(__file__).resolve().parent.parent / "out"

CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --paper:#faf8f3; --ink:#16150f; --sub:#6b6558; --faint:#938c7d;
  --rule:#ddd6c8; --rule-strong:#2a2820;
  --up:#b3261e; --down:#1a4d8f; --flat:#6b6558;
  --mark:#f3ecd9; --warn:#8a5a00; --warnbg:#fbf1dc;
}
@media (prefers-color-scheme:dark){
  :root{--paper:#14140f; --ink:#ece7da; --sub:#a09a8b; --faint:#7d776a;
        --rule:#33322a; --rule-strong:#6b6558; --mark:#26241c;
        --up:#ff6b5e; --down:#7fb2ff; --warn:#e0b060; --warnbg:#2a2317;}
}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);
  font-family:'맑은 고딕','Malgun Gothic',system-ui,-apple-system,sans-serif;
  font-size:15px;line-height:1.65;}
.serif{font-family:'나눔명조','Nanum Myeongjo','바탕','Batang',Georgia,'Times New Roman',serif;}
.wrap{max-width:1180px;margin:0 auto;padding:22px 20px 56px;}
a{color:inherit;}

/* 제호 */
.masthead{border-bottom:3px double var(--rule-strong);padding-bottom:10px;
  display:flex;align-items:baseline;justify-content:space-between;gap:12px;flex-wrap:wrap;}
.title{font-size:34px;font-weight:700;letter-spacing:.18em;margin:0;}
.dateline{font-size:13px;color:var(--sub);text-align:right;}
.dateline b{color:var(--ink);font-size:15px;letter-spacing:.02em;}

/* 기준선 */
.baseline{display:flex;flex-wrap:wrap;gap:0;border-bottom:1px solid var(--rule);
  padding:9px 0;margin-bottom:20px;}
.bl{flex:1 1 120px;padding:2px 12px;border-right:1px solid var(--rule);min-width:0;}
.bl:last-child{border-right:none;}
.bl .k{font-size:11.5px;color:var(--faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.bl .v{font-size:16px;font-variant-numeric:tabular-nums;letter-spacing:-.01em;}
.up{color:var(--up)}.down{color:var(--down)}.flat{color:var(--flat)}

/* 결론 */
.lede{border-bottom:1px solid var(--rule);padding-bottom:18px;margin-bottom:22px;}
.lede-grid{display:grid;grid-template-columns:1fr 1fr;gap:26px;}
.lede h2{font-size:12px;letter-spacing:.22em;color:var(--sub);margin:0 0 6px;font-weight:600;}
.lede .big{font-size:25px;line-height:1.35;font-weight:700;margin:0 0 8px;letter-spacing:-.01em;}
.lede .why{font-size:13.5px;color:var(--sub);margin:0 0 10px;}
.names{display:flex;flex-wrap:wrap;gap:6px;}
.nm{font-size:13.5px;background:var(--mark);border-radius:3px;padding:2px 8px;
  font-weight:600;white-space:nowrap;}
.nm.pick{background:none;border-bottom:2px solid var(--up);border-radius:0;padding:2px 2px;}

/* 섹션 */
h3.sec{font-size:12px;letter-spacing:.2em;color:var(--sub);font-weight:600;
  border-bottom:1px solid var(--rule-strong);padding-bottom:5px;margin:30px 0 14px;}

/* 픽 카드 */
.picks{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:16px;}
.pick{border:1px solid var(--rule);border-top:3px solid var(--up);padding:14px 16px;
  background:color-mix(in srgb,var(--paper) 92%,#fff);}
.pick .ph{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;margin-bottom:2px;}
.pick .pn{font-size:19px;font-weight:700;}
.pick .pc{font-size:12px;color:var(--faint);font-variant-numeric:tabular-nums;}
.pick .pt{font-size:12px;color:var(--sub);margin-left:auto;}
.setup{font-size:13.5px;color:var(--sub);margin:4px 0 10px;}
.plan{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--rule);
  border:1px solid var(--rule);margin-bottom:9px;}
.plan div{background:var(--paper);padding:6px 8px;}
.plan .k{font-size:10.5px;color:var(--faint);letter-spacing:.04em;}
.plan .v{font-size:15px;font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:-.02em;}
.line{font-size:12.5px;color:var(--sub);margin:3px 0;}
.line b{color:var(--ink);font-weight:600;}
.badge{display:inline-block;font-size:11px;font-weight:600;padding:1px 7px;border-radius:2px;
  background:var(--warnbg);color:var(--warn);margin-right:4px;}

/* 3컬럼 */
.cols{display:grid;grid-template-columns:1fr 1fr 1fr;gap:26px;}
.card{border-bottom:1px solid var(--rule);padding:11px 0;}
.card:last-child{border-bottom:none;}
.ch{display:flex;align-items:baseline;gap:7px;flex-wrap:wrap;}
.ct{font-size:16px;font-weight:700;}
.cm{font-size:12px;color:var(--faint);margin-left:auto;font-variant-numeric:tabular-nums;}
.pool{font-size:12.5px;color:var(--sub);margin-top:5px;line-height:1.6;}
details>summary{cursor:pointer;font-size:12px;color:var(--faint);margin-top:5px;
  list-style:none;user-select:none;}
details>summary::-webkit-details-marker{display:none}
details>summary::before{content:"▸ ";}
details[open]>summary::before{content:"▾ ";}
.src{font-size:12px;color:var(--faint);margin-top:5px;line-height:1.55;}
.src a{color:var(--down);text-decoration:none;border-bottom:1px dotted var(--rule-strong);}
.mono{font-variant-numeric:tabular-nums;}

.avoid .ct{color:var(--down);}

/* 미국장 지도 — 인포그래픽 스타일 (이 섹션만 컬러 카드) */
.usmap{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px;}
.um{border-radius:10px;overflow:hidden;background:color-mix(in srgb,var(--paper) 92%,#fff);
  box-shadow:0 1px 4px rgba(0,0,0,.08);border:1px solid var(--rule);}
.umh{display:flex;align-items:center;gap:8px;padding:9px 12px;color:#fff;}
.umno{font-size:12px;font-weight:800;opacity:.85;}
.umn{font-size:15px;font-weight:800;letter-spacing:-.01em;}
.umc{margin-left:auto;font-size:15px;font-weight:800;font-variant-numeric:tabular-nums;
  background:rgba(255,255,255,.18);border-radius:6px;padding:1px 8px;}
.umb{padding:7px 12px 4px;}
.umr{display:flex;align-items:baseline;padding:3px 0;font-size:13px;
  border-bottom:1px dotted var(--rule);}
.umr:last-child{border-bottom:none;}
.umr .tk{font-weight:700;}
.umr .pc{margin-left:auto;font-weight:700;font-variant-numeric:tabular-nums;}
.ums{font-size:11.5px;color:var(--sub);padding:2px 12px 7px;line-height:1.6;
  border-top:1px dotted var(--rule);}
.umi{font-size:12px;padding:7px 12px 9px;font-weight:600;}
.umi.hot{background:color-mix(in srgb,var(--up) 10%,transparent);color:var(--up);}
.umi.cold{background:color-mix(in srgb,var(--down) 10%,transparent);color:var(--down);}
.umi.mid{color:var(--faint);font-weight:400;}
/* 핵심 포인트 박스 */
.uskey{border:1px solid var(--rule);border-radius:10px;margin-top:14px;overflow:hidden;}
.uskey-h{background:var(--rule-strong);color:var(--paper);font-weight:800;font-size:13.5px;
  padding:8px 14px;letter-spacing:.06em;text-align:center;}
.uskey-r{display:flex;gap:10px;align-items:baseline;padding:8px 14px;
  border-bottom:1px solid var(--rule);font-size:13.5px;}
.uskey-r:last-child{border-bottom:none;}
.uskey-n{font-weight:800;min-width:18px;color:var(--sub);}
.uskey-t{font-weight:700;min-width:110px;}
.usfinal{margin-top:12px;border-radius:10px;padding:11px 16px;font-size:14px;font-weight:700;
  background:color-mix(in srgb,var(--mark) 80%,var(--paper));border:1px solid var(--rule);}
.foot{margin-top:34px;border-top:3px double var(--rule-strong);padding-top:12px;
  font-size:12px;color:var(--faint);line-height:1.75;}
.foot b{color:var(--sub);}
.notes{font-size:11.5px;color:var(--faint);margin-top:8px;}
.empty{font-size:13.5px;color:var(--sub);padding:12px 0;}

@media (max-width:980px){
  .cols{grid-template-columns:1fr 1fr;}
}
@media (max-width:720px){
  .wrap{padding:16px 14px 40px;}
  .title{font-size:26px;letter-spacing:.12em;}
  .masthead{flex-direction:column;align-items:flex-start;}
  .dateline{text-align:left;}
  .lede-grid{grid-template-columns:1fr;gap:20px;}
  .lede .big{font-size:21px;}
  .cols{grid-template-columns:1fr;gap:8px;}
  .bl{flex:1 1 45%;border-right:none;padding:4px 0;}
  .plan{grid-template-columns:repeat(2,1fr);}
}
"""


def e(v) -> str:
    return html.escape(str(v)) if v is not None else ""


def num(v, digits: int = 0) -> str:
    return f"{v:,.{digits}f}" if isinstance(v, (int, float)) else "—"


def signed(v) -> str:
    if not isinstance(v, (int, float)):
        return '<span class="flat">확인 필요</span>'
    cls = "up" if v > 0 else ("down" if v < 0 else "flat")
    return f'<span class="{cls}">{v:+.2f}%</span>'


def _names(items: list[dict], pick: bool = False) -> str:
    cls = "nm pick" if pick else "nm"
    return "".join(f'<span class="{cls}">{e(i["name"])}</span>' for i in items)


def _theme_card(card: dict, kind: str) -> str:
    picks, pool = card["picks"], card["pool"]
    meta = []
    if card.get("us_change") is not None:
        meta.append(f'美 {card["us_change"]:+.1f}%')
    if card.get("momentum_days"):
        meta.append(f'{card["momentum_days"]}일 등장')

    badges = "".join(f'<span class="badge">{e(f)}</span>' for f in card.get("flags", []))
    lead = ""
    if card.get("leaders"):
        lead = " · ".join(f'{e(l["ticker"])} {l["chg"]:+.1f}%' for l in card["leaders"][:3])
        lead = f'<div class="src">美 주도주 {lead}</div>'

    ev = ""
    for n in card.get("evidence", []):
        link = f' <a href="{e(n["url"])}" target="_blank" rel="noopener">↗</a>' if n["url"] else ""
        ev += (f'<div class="src">“{e(n["title"][:60])}” — {e(n["source"])} '
               f'{e(n["date"])}{link}</div>')

    pick_html = ""
    if picks:
        pick_html = f'<div class="names" style="margin-top:6px">{_names(picks, True)}</div>'

    pool_html = ""
    if pool:
        shown = ", ".join(e(p["name"]) for p in pool[:24])
        nochart = sum(1 for p in pool if p["data_status"] != "ok")
        tail = f' · 차트 확인 필요 {nochart}종목' if nochart else ""
        pool_html = (f'<details><summary>후보 풀 {len(pool)}종목{tail}</summary>'
                     f'<div class="pool">{shown}</div></details>')

    return (f'<div class="card"><div class="ch"><span class="ct serif">{e(card["kr_theme"])}</span>'
            f'{badges}<span class="cm">{" · ".join(meta)}</span></div>'
            f'{pick_html}{lead}{ev}{pool_html}</div>')


def _pick_card(p: dict) -> str:
    flags = [f for f in (p["risk_flags"] or "").split(",") if f]
    badges = "".join(f'<span class="badge">{e(f)}</span>' for f in flags)
    rr = f'{p["rr"]:.1f}' if p["rr"] else "—"
    return f"""
<div class="pick">
  <div class="ph"><span class="pn serif">{e(p['name'])}</span>
    <span class="pc">{e(p['code'])}</span>
    <span class="pt">{e(p['kr_theme'])}</span></div>
  <div class="setup">· 위치: {e(p['setup'])}</div>
  <div class="plan">
    <div><div class="k">진입 트리거</div><div class="v">{num(p['entry'])}</div></div>
    <div><div class="k">손절선</div><div class="v down">{num(p['stop'])}</div></div>
    <div><div class="k">1차 목표</div><div class="v up">{num(p['target1'])}</div></div>
    <div><div class="k">2차 목표</div><div class="v up">{num(p['target2'])}</div></div>
  </div>
  <div class="line">· 손익비 <b>{rr}</b> · 통과 필터: {e(p['reason'])}</div>
  <div class="line">· 리스크: {badges if badges else '특이사항 없음'}</div>
  <div class="line">· 진입 판단: 트리거 넘으면 분할 진입 검토 — <b>실행은 사용자</b></div>
  <div class="line">· 청산 기준: {num(p['target1'])} 도달 시 분할 익절 ·
    <b>{num(p['stop'])} 이탈 시 정리</b> · 거래량 감소·장대음봉이면 모멘텀 약화로 본다</div>
</div>"""


HEADER_COLORS = ["#2f4b7c", "#1d7a63", "#6a4c93", "#8a5a00", "#a23b3b",
                 "#33607d", "#5b6218", "#7d3b6b", "#3d6b35", "#845d2f",
                 "#4a4a8f", "#207878", "#77413d"]


def _us_map(v: dict) -> str:
    if not v.get("us_map"):
        return ""
    cards = []
    for idx, m in enumerate(v["us_map"]):
        chg = m["chg"]
        color = HEADER_COLORS[idx % len(HEADER_COLORS)]
        chg_txt = f"{chg:+.2f}%" if chg is not None else "—"
        rows = "".join(
            f'<div class="umr"><span class="tk">{e(t["ticker"])}</span>'
            f'<span class="pc {"up" if t["chg"] > 0 else "down" if t["chg"] < 0 else "flat"}">'
            f'{t["chg"]:+.2f}%</span></div>' for t in m["tickers"])
        cls = "hot" if (chg or 0) >= 3 else ("cold" if (chg or 0) <= -3 else "mid")
        arrow = "➜ " if cls != "mid" else "· "
        insights = "".join(f'<div>· {e(s)}</div>' for s in m.get("insights", []))
        insights_html = f'<div class="ums">{insights}</div>' if insights else ""
        cards.append(
            f'<div class="um"><div class="umh" style="background:{color}">'
            f'<span class="umno">{idx + 1}</span><span class="umn">{e(m["kr_theme"])}</span>'
            f'<span class="umc">{chg_txt}</span></div>'
            f'<div class="umb">{rows}</div>{insights_html}'
            f'<div class="umi {cls}">{arrow}{e(m["note"])}</div></div>')

    # 핵심 포인트: ±3% 신호가 뜬 테마만 번호 붙여 요약
    hot = [m for m in v["us_map"] if (m["chg"] or 0) >= 3]
    cold = [m for m in v["us_map"] if (m["chg"] or 0) <= -3]
    keys = ""
    if hot or cold:
        rows = ""
        for i, m in enumerate(hot + cold, 1):
            up = (m["chg"] or 0) >= 3
            lead = " · ".join(t["ticker"] for t in m["tickers"][:3])
            rows += (f'<div class="uskey-r"><span class="uskey-n">{i}</span>'
                     f'<span class="uskey-t {"up" if up else "down"}">{e(m["kr_theme"])}</span>'
                     f'<span>{e(lead)} {"강세" if up else "약세"} — '
                     f'{"관련주 유입 여부 확인" if up else "국내 관련주 추격 금지"}</span></div>')
        keys = ('<div class="uskey"><div class="uskey-h">오늘 국내장에서 체크할 핵심 포인트</div>'
                + rows + '</div>')

    if hot and cold:
        final = (f'오늘은 "{cold[0]["kr_theme"]}에서 빠진 돈이 '
                 f'{hot[0]["kr_theme"]} 쪽으로 옮겨가는지"를 보는 것이 핵심.')
    elif hot:
        final = f'오늘은 "{" · ".join(m["kr_theme"] for m in hot[:2])} 수급 유입 여부"가 핵심.'
    elif cold:
        final = (f'오늘은 신규 매수보다 "{" · ".join(m["kr_theme"] for m in cold[:2])} '
                 f'약세가 이어지는지" 관찰이 핵심 — 해당 테마 추격 금지.')
    else:
        final = "오늘은 미국발 신호 없음 — 관망이 기본, 국내 재료만 개별 확인."

    return (f'<h3 class="sec">미국장 지도 — 간밤 미국 증시 주요 특징</h3>'
            f'<div class="usmap">{"".join(cards)}</div>{keys}'
            f'<div class="usfinal">💡 {e(final)}</div>')


def render(view: dict) -> str:
    v = view
    bl = "".join(
        f'<div class="bl"><div class="k">{e(b["label"])}</div>'
        f'<div class="v">{num(b["close"], 2)} {signed(b["change_pct"])}</div></div>'
        for b in v["baseline"]) or '<div class="bl"><div class="k">기준선</div><div class="v">데이터 부재 — 확인 필요</div></div>'

    # 결론
    os_top = v["overseas"][0] if v["overseas"] else None
    kr_top = v["domestic"][0] if v["domestic"] else None

    def lede_col(label: str, card: dict | None, fallback: str) -> str:
        if not card:
            return f'<div><h2>{label}</h2><p class="big serif">{fallback}</p></div>'
        heads = [c["kr_theme"] for c in
                 (v["overseas"] if label.startswith("해외") else v["domestic"])[:2]]
        picks = card["picks"][:6]
        show = picks or card["pool"][:6]
        why = ""
        if label.startswith("해외") and card.get("leaders"):
            why = (f'美 {card["leaders"][0]["ticker"]} {card["leaders"][0]["chg"]:+.1f}% '
                   f'· 바스켓 {card["us_change"]:+.1f}%')
        elif card.get("evidence"):
            why = f'“{card["evidence"][0]["title"][:44]}”'
        badges = "".join(f'<span class="badge">{e(f)}</span>' for f in card.get("flags", []))
        return (f'<div><h2>{label}</h2>'
                f'<p class="big serif">{e(" · ".join(heads))} {badges}</p>'
                f'<p class="why">{e(why)}</p>'
                f'<div class="names">{_names(show, bool(picks))}</div></div>')

    lede = (f'<div class="lede"><div class="lede-grid">'
            + lede_col("해외발 (미국 → 한국)", os_top, "오른 테마 없음")
            + lede_col("국내발 (뉴스·공시)", kr_top, "탐지된 테마 없음")
            + '</div></div>')

    picks_html = ("".join(_pick_card(p) for p in v["picks"]) if v["picks"]
                  else '<p class="empty">필터를 통과한 자리가 없다. '
                       '진입가·손절선이 성립하지 않는 종목은 픽으로 올리지 않는다 — '
                       '오늘은 관망이 결론이다.</p>')

    overseas = "".join(_theme_card(c, "us") for c in v["overseas"]) or \
        '<p class="empty">해당 없음</p>'
    domestic = "".join(_theme_card(c, "kr") for c in v["domestic"]) or \
        '<p class="empty">해당 없음</p>'

    surge = "".join(
        f'<div class="card"><div class="ch"><span class="ct serif">{e(s["name"])}</span>'
        f'<span class="cm">최근 20일 중 {s["surge_days"]}일 급등</span></div>'
        f'<div class="pool">{e((s["themes"] or "").replace(",", " · "))}</div></div>'
        for s in v["surge"]) or '<p class="empty">반복 급등 종목 없음</p>'

    avoid = "".join(
        f'<div class="card avoid"><div class="ch"><span class="ct serif">{e(a["kr_theme"])}</span>'
        f'<span class="cm">{a["score"]:+.1f}</span></div>'
        + "".join(
            f'<div class="src">“{e(n["title"][:58])}” — {e(n["source"])}'
            + (f' <a href="{e(n["url"])}" target="_blank" rel="noopener">↗</a>' if n["url"] else "")
            + '</div>' for n in a["items"])
        + '</div>'
        for a in v["avoid"]) or '<p class="empty">회피 신호 없음</p>'

    notes = ""
    if v["notes"]:
        notes = '<div class="notes">수집 경고 — ' + " / ".join(e(n) for n in v["notes"][:6]) + '</div>'

    fwd = "다음 거래일" if v["is_forward"] else "오늘"

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>장전 브리핑 {e(v['target_label'])}</title>
<style>{CSS}</style></head><body><div class="wrap">

<div class="masthead">
  <h1 class="title serif">장전 브리핑</h1>
  <div class="dateline"><b>{e(v['target_label'])} {fwd} 장전</b><br>
    생성 {e(v['generated_at'])} · 픽 {v['counts']['picks']} / 풀 {v['counts']['pool']}종목 ·
    테마 {v['counts']['themes']}</div>
</div>

<div class="baseline">{bl}</div>

{f'<div style="border:1px solid var(--warn);background:var(--warnbg);color:var(--warn);padding:9px 14px;margin-bottom:18px;font-size:13.5px;font-weight:600">⚠ 시장 급락 국면 — 코스피 최근 5일 {v["regime"]["ret5"]:+.1f}%. 과거 2년 이 국면에서 픽 기대값이 3분의 1로 줄었다 (+0.154R→+0.053R). 오늘은 쉬거나 비중 축소를 검토할 것. (정보 표시일 뿐 픽 선정에는 반영 안 됨)</div>' if v.get("regime") and v["regime"]["caution"] else ''}
{lede}

{_us_map(v)}

<h3 class="sec">오늘의 픽 — 필터 통과 종목만</h3>
<div class="picks">{picks_html}</div>

<div class="cols">
  <div><h3 class="sec">해외발 테마</h3>{overseas}</div>
  <div><h3 class="sec">국내발 테마 (뉴스·공시)</h3>{domestic}</div>
  <div>
    <h3 class="sec">반복 급등 (확인 층)</h3>{surge}
    <h3 class="sec">회피 — 죽은 테마·악재</h3>{avoid}
  </div>
</div>

<div class="foot">
  <b>매매 추천이 아니다.</b> 이 화면은 후보를 좁히고 기준선을 제시하는 판단 보조 도구다.
  진입가·손절선·목표가는 일봉 기준 계산값이며, 장중 실시간 호가·체결은 반영돼 있지 않다.
  최종 매수·매도 판단과 주문 실행은 본인이 직접 한다. 이 도구에는 주문 기능이 없다.<br>
  풀에 있는 종목은 필터를 통과하지 못했거나 시세 데이터가 없어 <b>차트 확인 필요</b> 상태다 —
  근거 없이 진입가를 만들어 붙이지 않는다.
  {notes}
</div>

</div></body></html>"""


def write(view: dict, out_dir: Path = OUT) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    dated = out_dir / f"{view['target_date']}.html"
    dated.write_text(render(view), encoding="utf-8")
    shutil.copyfile(dated, out_dir / "index.html")
    return dated


if __name__ == "__main__":
    v = viewdata.build(_date.today().isoformat())
    path = write(v)
    print(f"대시보드 생성: {path}")
    print(f"          최신: {path.parent / 'index.html'}")
