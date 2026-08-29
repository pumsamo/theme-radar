"""검증 현황판 — 시트(탭) 구조 (2026-08-29 사용자 확정 구성).

시트: 요약 | 계좌① | 계좌② | 저녁 스캔 | 관찰 448 | 채널·연구
  계좌①·②는 각자의 종결 거래·보유 포지션을 따로 보여준다 (사용자: "거래 계좌 2개 따로").
  저녁 스캔 = 최신 저녁 A급 목록 (DB tier='escan' 최신일).
  관찰 448 = 448일선 ±3% + 거래대금 30억 + 위에서 접근 — 매수 규칙으론 기각된 셋업이라
  '관찰용' 명시 (사용자 가설 추적용). 최신 연간 영업이익 흑자 여부 병기.

저녁 루틴 ⑧단계 → docs/status.html → https://pumsamo.github.io/theme-radar/status.html
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date
from pathlib import Path

import boot  # noqa: F401
import ledger
import replay
from db import connect
from net import fetch
from prices_kr import fetch_ohlc

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_START = "2026-08-11"
CONTRACT_DAYS = 60
MIDCHECK = "2026-09-08"
VERDICT = "2026-11-04"

CHANNELS_ASOF = "2026-08-29"
CHANNELS = [
    ("초하쌤 콜 (방송·글·톡)", "10콜 · 4✅ 1❌ 1△ 4대기", "up"),
    ("초하쌤 방 언급 따라사기", "5일 +1.49% (126건)", "up"),
    ("수강생 반 언급", "5일 −2.20% (224건)", "down"),
    ("소식방 언급", "5일 −6.0% (2,407건)", "down"),
]
RESEARCH = [
    ("채택", "read-across (미국 테마 → 다음날 한국, 64.5% vs 44.3%)"),
    ("채택", "A급 자리 3/3 (낙폭 −15~−3 · 이격 · RSI) — 계약 동결 중"),
    ("채택", "악재 뉴스 회피 · 적자기업 지지선 매수 회피"),
    ("유망", "6개월 좁은 박스 돌파 +0.104R (A급 다음 2위, 계약 후 결합 테스트)"),
    ("기각", "삼박자 · 기준봉 눌림 · 소식방 따라사기 · 스프링 재진입"),
    ("기각", "448선 단독 (−0.075R) · 448+실적 YoY (판정선 미달)"),
    ("보류", "대장주 우위 (소급 편향 → 저녁 스캔 전방 데이터로 재검증 중)"),
]

FIN_URL = "https://m.stock.naver.com/api/stock/{code}/finance/annual"


def won(x: float) -> str:
    return f"{x:+,.0f}"


def pct_cls(x: float) -> str:
    return "up" if x > 0 else ("down" if x < 0 else "")


def acct_sheet(a, label):
    """한 계좌의 종결 거래 + 보유 포지션 시트 본문."""
    closed = [c for c in a["closed"] if c["label"] != "미체결 소멸"]
    lapsed = sum(1 for c in a["closed"] if c["label"] == "미체결 소멸")
    ret = (a["equity"] / a["seed"] - 1) * 100
    closed_rows = "".join(
        f"<tr><td>{c['date'][4:6]}/{c['date'][6:8]}</td><td>{c['name']}</td>"
        f"<td class='{('up' if c['label'] == '목표' else 'down')}'>{c['label']}</td>"
        f"<td class='{pct_cls(c['pnl'])}'>{won(c['pnl'])}원</td></tr>"
        for c in closed) or "<tr><td colspan=4>아직 없음</td></tr>"
    pos_rows = "".join(
        f"<tr><td>{p['name']}</td><td>{p['shares']}주</td><td>{p['fill']:,.0f}</td>"
        f"<td>{p['cur']:,.0f}</td><td class='{pct_cls(p['pnl'])}'>{won(p['pnl'])}원</td></tr>"
        for p in sorted(a["open"], key=lambda x: -x["pnl"])) or "<tr><td colspan=5>없음</td></tr>"
    skip_rows = "".join(f"<div class='row'>⚠ {dt} {nm}: {why}</div>"
                        for nm, dt, why in a["skipped"])
    return f"""
<h2>{label}</h2>
<div class="cards"><div class="card">
  <div class="big {pct_cls(ret)}">{a['equity']:,.0f}<span class="unit">원</span></div>
  <div class="sub {pct_cls(ret)}">{ret:+.2f}%</div>
  <div class="row">실현 {won(a['realized'])} · 미실현 {won(a['unreal'])} · 현금 {a['cash']:,.0f}원</div>
  <div class="row">종결 {len(closed)}건 · 보유 {len(a['open'])}종목 · 미체결 소멸 {lapsed}건</div>
</div></div>
<h2>종결 거래</h2>
<div class="twrap"><table><tr><th>일자</th><th>종목</th><th>결과</th><th>손익</th></tr>{closed_rows}</table></div>
<h2>보유 포지션</h2>
<div class="twrap"><table><tr><th>종목</th><th>수량</th><th>진입</th><th>현재</th><th>평가손익</th></tr>{pos_rows}</table></div>
{skip_rows}"""


def picks_sheet(db):
    latest = db.execute("select max(date) from candidates where tier='pick'").fetchone()[0]
    if not latest:
        return "<h2>아침 픽</h2><p>기록 없음</p>"
    today = _date.today().isoformat()
    note = "" if latest == today else f"<div class='row'>오늘 결번 (지각·휴장) — 최근 픽 {latest}</div>"
    rows = db.execute(
        """select name, kr_theme, entry, stop, target1, target2, setup, risk_flags
           from candidates where tier='pick' and date=? group by code""", (latest,)).fetchall()
    watch = db.execute(
        """select name, entry, stop from candidates
           where tier='watch' and date=? and entry is not null group by code""", (latest,)).fetchall()
    body = "".join(
        f"<tr><td>{n}</td><td class='opt'>{t or ''}</td><td>{e:,.0f}</td><td>{s:,.0f}</td>"
        f"<td>{t1:,.0f}</td><td class='opt'>{t2:,.0f}</td>"
        f"<td class='row'>{(st or '') + ((' ⚠' + rf) if rf else '')}</td></tr>"
        for n, t, e, s, t1, t2, st, rf in rows) or "<tr><td colspan=7>픽 없음</td></tr>"
    wbody = "".join(f"<tr><td>{n}</td><td>{e:,.0f}</td><td>{s:,.0f}</td></tr>"
                    for n, e, s in watch)
    wsec = (f"<h2>자리 완성 (테마 신호 대기)</h2><div class='twrap'><table>"
            f"<tr><th>종목</th><th>진입</th><th>손절</th></tr>{wbody}</table></div>") if wbody else ""
    return f"""
<h2>아침 계약 픽 — {latest} ({len(rows)}종목)</h2>
{note}
<div class="row">진입 = 전일 고가 돌파 확인가. 3거래일 내 미돌파 시 소멸. 매수 추천 아님.</div>
<div class="twrap"><table><tr><th>종목</th><th class="opt">테마</th><th>진입</th><th>손절</th>
<th>목표1</th><th class="opt">목표2</th><th>비고</th></tr>{body}</table></div>
{wsec}"""


def calls_sheet():
    path = ROOT / "config" / "choha_calls.json"
    if not path.exists():
        return "<h2>초하쌤 콜</h2><p>데이터 없음</p>"
    calls = json.loads(path.read_text(encoding="utf-8"))["calls"]
    pending = [c for c in calls if c["verdict"] == "대기" and c.get("code") and c.get("basis")]
    cur = {}
    for c in pending:
        try:
            bars = fetch_ohlc(c["code"], "20260820", _date.today().strftime("%Y%m%d"))
            if bars:
                cur[c["no"]] = bars[-1]["close"]
        except Exception:  # noqa: BLE001
            pass
    n_ok = sum(1 for c in calls if c["verdict"] == "✅")
    n_no = sum(1 for c in calls if c["verdict"] == "❌")
    n_amb = sum(1 for c in calls if c["verdict"] == "△")
    n_wait = sum(1 for c in calls if c["verdict"] == "대기")
    rows = []
    for c in calls:
        prog = ""
        if c["no"] in cur and c.get("basis"):
            pct = (cur[c["no"]] / c["basis"] - 1) * 100
            prog = f"<span class='{pct_cls(pct)}'>현재 {cur[c['no']]:,.0f} ({pct:+.1f}%)</span>"
        v = c["verdict"]
        vcls = "up" if v == "✅" else ("down" if v == "❌" else "")
        rows.append(
            f"<tr><td>{c['no']}</td><td class='opt'>{c['date'][5:]}</td><td>{c['name']}</td>"
            f"<td>{f"{c['basis']:,.0f}" if c.get('basis') else '—'}</td>"
            f"<td class='opt'>{f"{c['stop']:,.0f}" if c.get('stop') else '—'}</td>"
            f"<td class='{vcls}'>{v}</td><td class='row'>{c['result']} {prog}</td></tr>")
    return f"""
<h2>초하쌤 콜 추적 — {len(calls)}콜 ({n_ok}✅ {n_no}❌ {n_amb}△ {n_wait}대기)</h2>
<div class="row">방송·글·톡방에서 나온 종목 콜을 접수 즉시 기록하고 기한 내 성적으로 판정.
채널 신뢰도의 근거 데이터 (config/choha_calls.json).</div>
<div class="twrap"><table><tr><th>#</th><th class="opt">일자</th><th>종목</th><th>기준가</th>
<th class="opt">손절</th><th>판정</th><th>결과·진행</th></tr>{"".join(rows)}</table></div>"""


def equity_svg(s10, s30):
    """계좌①·② 수익률(%) 추이 — 외부 라이브러리 없는 인라인 SVG."""
    if len(s10) < 2:
        return ""
    days = sorted({d for d, _ in s10} | {d for d, _ in s30})
    def pcts(series, seed):
        m = dict(series)
        out, last = [], seed
        for d in days:
            last = m.get(d, last)
            out.append((last / seed - 1) * 100)
        return out
    p10, p30 = pcts(s10, s10[0][1]), pcts(s30, s30[0][1])
    lo = min(min(p10), min(p30), 0) - 0.3
    hi = max(max(p10), max(p30), 0) + 0.3
    W, H, L = 640, 180, 44
    def xy(i, v):
        x = L + (W - L - 8) * i / max(1, len(days) - 1)
        y = 8 + (H - 28) * (hi - v) / (hi - lo)
        return f"{x:.1f},{y:.1f}"
    y0 = 8 + (H - 28) * hi / (hi - lo)
    grid = "".join(
        f'<text x="2" y="{8 + (H-28)*(hi-v)/(hi-lo)+4:.0f}" font-size="10" fill="#888">{v:+.0f}%</text>'
        for v in {round(lo), 0, round(hi)})
    labels = (f'<text x="{L}" y="{H-4}" font-size="10" fill="#888">{days[0][4:6]}/{days[0][6:]}</text>'
              f'<text x="{W-52}" y="{H-4}" font-size="10" fill="#888">{days[-1][4:6]}/{days[-1][6:]}</text>')
    return f"""
<h2>잔고 추이 (수익률 %)</h2>
<div class="twrap"><svg viewBox="0 0 {W} {H}" style="width:100%;max-width:{W}px">
<line x1="{L}" y1="{y0:.1f}" x2="{W-8}" y2="{y0:.1f}" stroke="#bbb" stroke-dasharray="3,3"/>
{grid}{labels}
<polyline fill="none" stroke="#c0392b" stroke-width="2" points="{' '.join(xy(i, v) for i, v in enumerate(p10))}"/>
<polyline fill="none" stroke="#1a4f9c" stroke-width="2" points="{' '.join(xy(i, v) for i, v in enumerate(p30))}"/>
<text x="{L}" y="16" font-size="11" fill="#c0392b">— 계좌① 1,000만</text>
<text x="{L+130}" y="16" font-size="11" fill="#1a4f9c">— 계좌② 3,000만</text>
</svg></div>"""


def evenscan_sheet(db):
    latest = db.execute("select max(date) from candidates where tier='escan'").fetchone()[0]
    if not latest:
        return "<h2>저녁 스캔</h2><p>기록 없음</p>"
    rows = db.execute(
        """select name, kr_theme, entry, stop, score, reason from candidates
           where tier='escan' and date=? order by score desc""", (latest,)).fetchall()
    total = db.execute("select count(*) from candidates where tier='escan'").fetchone()[0]
    body = "".join(
        f"<tr><td>{n}</td><td>{t or ''}</td><td>{e:,.0f}</td><td>{s:,.0f}</td>"
        f"<td>{v:,.0f}억</td><td class='row'>{r or ''}</td></tr>"
        for n, t, e, s, v, r in rows)
    return f"""
<h2>저녁 A급 스캔 — {latest} ({len(rows)}종목)</h2>
<div class="row">매일 종가 기준 자리 좋은 지도 종목 전부. 다음날 진입가(전일 고가 돌파) 미달 시 소멸.
누적 기록 {total}건 — 그림자 트랙으로 자동 채점 중 (매수 추천 아님).</div>
<div class="twrap"><table><tr><th>종목</th><th class="opt">테마</th><th>진입</th><th>손절</th><th>거래대금</th><th class="opt">자리</th></tr>{body}</table></div>"""


def ma448_sheet(db):
    codes = {c: n for c, n in db.execute("select code, name from stocks where themes != ''")}
    cache = replay.load_bars()
    today = _date.today().strftime("%Y%m%d")

    def tail(code):
        try:
            return code, fetch_ohlc(code, "20260601", today)
        except Exception:  # noqa: BLE001
            return code, []
    with ThreadPoolExecutor(8) as ex:
        fresh = dict(ex.map(tail, codes))

    hits = []
    for code, name in codes.items():
        old, new = cache.get(code, []), fresh.get(code, [])
        if not new:
            continue
        seen = {b["date"] for b in old}
        bars = sorted(old + [b for b in new if b["date"] not in seen],
                      key=lambda b: b["date"])
        if len(bars) < 448:
            continue
        closes = [b["close"] for b in bars]
        ma = sum(closes[-448:]) / 448
        c = closes[-1]
        v20 = sum(b["close"] * b["volume"] for b in bars[-20:]) / 20
        if abs(c / ma - 1) > 0.03 or v20 < 30e8:
            continue
        if not any(x > ma * 1.03 for x in closes[-60:]):
            continue  # 위에서 접근한 경우만
        hits.append({"name": name, "code": code, "close": c, "ma": ma,
                     "pct": (c / ma - 1) * 100, "val": v20})
    hits.sort(key=lambda h: -h["val"])

    def op_black(code):
        try:
            d = json.loads(fetch(FIN_URL.format(code=code), timeout=15).decode())
            fi = d["financeInfo"]
            firm = {t["key"] for t in fi["trTitleList"] if t.get("isConsensus") != "Y"}
            for row in fi["rowList"]:
                if row["title"] == "영업이익":
                    ys = {int(k[:4]): float(v["value"].replace(",", ""))
                          for k, v in row["columns"].items()
                          if k in firm and v and v.get("value") not in (None, "", "-")}
                    if ys:
                        y = max(ys)
                        return code, ("흑자" if ys[y] > 0 else "적자", y)
        except Exception:  # noqa: BLE001
            pass
        return code, ("?", 0)
    with ThreadPoolExecutor(8) as ex:
        fin = dict(ex.map(op_black, [h["code"] for h in hits]))

    body = "".join(
        f"<tr><td>{h['name']}</td><td>{h['close']:,.0f}</td><td>{h['ma']:,.0f}</td>"
        f"<td class='{pct_cls(h['pct'])}'>{h['pct']:+.1f}%</td><td>{h['val']/1e8:,.0f}억</td>"
        f"<td class='{('up' if fin[h['code']][0]=='흑자' else 'down' if fin[h['code']][0]=='적자' else '')}'>"
        f"{fin[h['code']][0]}({fin[h['code']][1]})</td></tr>"
        for h in hits) or "<tr><td colspan=6>해당 없음</td></tr>"
    return f"""
<h2>관찰 — 448일선 ±3% ({len(hits)}종목)</h2>
<div class="row">⚠ 백테스트 기각된 셋업 (단독 −0.075R). 매수 목록이 아니라 사용자 가설 추적용 관찰
시트다. 조건: 448일선 ±3% · 최근 60일 내 위에서 접근 · 거래대금 30억+. 흑자/적자는 최신 확정
연간 영업이익 (참고: 적자 기업 지지선 매수는 −0.086R로 특히 나쁨 — 회피 원칙 채택됨).</div>
<div class="twrap"><table><tr><th>종목</th><th>종가</th><th class="opt">448선</th><th>이격</th><th>거래대금</th><th>실적</th></tr>{body}</table></div>"""


def main() -> None:
    today = _date.today()
    db = connect()
    run_days = db.execute(
        "select count(distinct date) from candidates where date >= ? and tier in ('pick','pool')",
        (CONTRACT_START,)).fetchone()[0]
    picks_n = db.execute(
        "select count(*) from (select distinct date, code from candidates where date >= ? and tier='pick')",
        (CONTRACT_START,)).fetchone()[0]

    big = ledger.compute(1_000_000_000)
    a10 = ledger.compute(10_000_000)
    a30 = ledger.compute(30_000_000)

    def r_of(c, risk):
        return 2.0 if c["label"] == "목표" else (-1.0 if c["label"] == "손절"
                                                else c["pnl"] / risk)
    closed_r = [r_of(c, big["risk"]) for c in big["closed"] if c["label"] != "미체결 소멸"]
    open_r = [p["pnl"] / big["risk"] for p in big["open"]]
    lapsed = sum(1 for c in big["closed"] if c["label"] == "미체결 소멸")
    prog = min(100, int(run_days / CONTRACT_DAYS * 100))

    def acct_card(a, label):
        ret = (a["equity"] / a["seed"] - 1) * 100
        return f"""
      <div class="card"><h3>{label}</h3>
        <div class="big {pct_cls(ret)}">{a['equity']:,.0f}<span class="unit">원</span></div>
        <div class="sub {pct_cls(ret)}">{ret:+.2f}%</div>
        <div class="row">실현 {won(a['realized'])} · 미실현 {won(a['unreal'])}</div>
      </div>"""

    ch_rows = "".join(f"<tr><td>{n}</td><td class='{c}'>{v}</td></tr>" for n, v, c in CHANNELS)
    rs_rows = "".join(
        f"<tr><td class='{('up' if k in ('채택', '유망') else 'down' if k == '기각' else '')}'>{k}</td><td>{v}</td></tr>"
        for k, v in RESEARCH)

    sheets = {
        "요약": f"""
<h2>계약 진행</h2>
<div class="bar"><div style="width:{prog}%"></div></div>
<div class="row">D+{run_days}/{CONTRACT_DAYS} 거래일 ({prog}%) · 중간점검 {MIDCHECK} · 판정 기준: 체결 평균 R ≥ +0.10</div>
<h2>스코어보드</h2>
<div class="cards">
  <div class="card"><h3>R 트랙 (자금 무제약 · 판정 기준)</h3>
    <div class="big {pct_cls(sum(closed_r) + sum(open_r))}">{sum(closed_r) + sum(open_r):+.2f}<span class="unit">R</span></div>
    <div class="sub">종결 {sum(closed_r):+.2f}R · 진행 {sum(open_r):+.2f}R</div>
    <div class="row">픽 {picks_n} · 종결 {len(closed_r)} · 보유 {len(open_r)} · 미체결 소멸 {lapsed}</div>
  </div>
  {acct_card(a10, "가상계좌 ① 1,000만")}
  {acct_card(a30, "가상계좌 ② 3,000만")}
</div>
{equity_svg(a10["equity_series"], a30["equity_series"])}""",
        "아침 픽": picks_sheet(db),
        "계좌①": acct_sheet(a10, "가상계좌 ① — 종자돈 1,000만 (리스크 10만/건)"),
        "계좌②": acct_sheet(a30, "가상계좌 ② — 종자돈 3,000만 (리스크 30만/건)"),
        "저녁 스캔": evenscan_sheet(db),
        "관찰 448": ma448_sheet(db),
        "콜": calls_sheet(),
        "채널·연구": f"""
<h2>채널 성적 <span class="row">({CHANNELS_ASOF} 기준)</span></h2>
<div class="twrap"><table>{ch_rows}</table></div>
<h2>연구 판정 <span class="row">({CHANNELS_ASOF} 기준)</span></h2>
<div class="twrap"><table>{rs_rows}</table></div>""",
    }

    tab_btns = "".join(
        f'<button{" class=\"on\"" if i == 0 else ""} data-s="s{i}">{name}</button>'
        for i, name in enumerate(sheets))
    tab_divs = "".join(
        f'<div class="sheet{" on" if i == 0 else ""}" id="s{i}">{body}</div>'
        for i, (name, body) in enumerate(sheets.items()))

    html = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>테마 레이더 — 검증 현황판</title>
<style>
  body{{font-family:'Noto Serif KR','Nanum Myeongjo',Batang,serif;background:#fff;color:#1a1a1a;
       max-width:60rem;margin:0 auto;padding:1rem 1.2rem;line-height:1.6}}
  h1{{font-size:1.6rem;border-bottom:3px double #1a1a1a;padding-bottom:.4rem;margin-bottom:.2rem}}
  .date{{color:#666;font-size:.85rem;margin-bottom:.6rem}}
  h2{{font-size:1.05rem;border-bottom:1px solid #999;padding-bottom:.2rem;margin:1.4rem 0 .6rem}}
  .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(15rem,1fr));gap:.8rem}}
  .card{{border:1px solid #ddd;padding:.8rem 1rem}}
  .card h3{{font-size:.85rem;color:#555;margin:0 0 .3rem;font-weight:600}}
  .big{{font-size:1.5rem;font-weight:700}} .unit{{font-size:.9rem;font-weight:400}}
  .sub{{font-size:1rem;font-weight:600}} .row{{font-size:.8rem;color:#555}}
  .up{{color:#c0392b}} .down{{color:#1a4f9c}}
  table{{width:100%;border-collapse:collapse;font-size:.85rem}}
  td,th{{border-bottom:1px solid #eee;padding:.3rem .4rem;text-align:left}}
  .bar{{background:#eee;height:.8rem;margin:.4rem 0}}
  .bar>div{{background:#1a1a1a;height:100%}}
  .note{{font-size:.75rem;color:#888;margin-top:1.5rem;border-top:1px solid #ccc;padding-top:.5rem}}
  .tabs{{display:flex;flex-wrap:wrap;gap:.3rem;border-bottom:2px solid #1a1a1a;margin:.6rem 0 0}}
  .tabs button{{font:inherit;font-size:.85rem;padding:.35rem .7rem;border:1px solid #ccc;
    border-bottom:none;background:#f5f5f5;cursor:pointer}}
  .tabs button.on{{background:#1a1a1a;color:#fff;font-weight:700}}
  .sheet{{display:none}} .sheet.on{{display:block}}
  .twrap{{overflow-x:auto}}
  @media(max-width:40rem){{body{{padding:.8rem}}
    table{{font-size:.78rem}} td,th{{padding:.25rem .3rem}}
    .opt{{display:none}}}}
</style></head><body>
<h1>테마 레이더 — 검증 현황판</h1>
<div class="date">갱신 {today.isoformat()} · 계약 {CONTRACT_START} ~ 약 {VERDICT} (60거래일)</div>
<div class="tabs">{tab_btns}</div>
{tab_divs}
<script>
document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => {{
  document.querySelectorAll('.tabs button').forEach(x => x.classList.remove('on'));
  document.querySelectorAll('.sheet').forEach(x => x.classList.remove('on'));
  b.classList.add('on');
  document.getElementById(b.dataset.s).classList.add('on');
}});
</script>
<div class="note">관찰·검증 기록용 — 매매 추천 아님 · 주문 기능 없음 · 최종 판단과 실행은 본인.<br>
가상계좌 규칙: 리스크 1%/건 · 진입 3일 창 · 손절우선 · +2R 청산 · 20일 기한 · 왕복 비용 0.3% ·
동일 종목 중복 금지 · 종목당 20% 상한. <a href="index.html">→ 장전 브리핑</a></div>
</body></html>"""

    for d in (ROOT / "docs", ROOT / "out"):
        d.mkdir(exist_ok=True)
        (d / "status.html").write_text(html, encoding="utf-8")
    print(f"현황판 생성: 시트 {len(sheets)}개 · R트랙 {sum(closed_r) + sum(open_r):+.2f}R · "
          f"계좌① {a10['equity']:,.0f} · 계좌② {a30['equity']:,.0f}")


if __name__ == "__main__":
    main()
