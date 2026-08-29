"""검증 현황판 — 계약 진행·트랙별 성적·가상계좌를 한 화면에 (사용자 요청 2026-08-28).

아침 브리핑과 같은 신문형 디자인. 저녁 루틴 ⑧단계에서 생성 →
docs/status.html 커밋 → https://pumsamo.github.io/theme-radar/status.html

데이터: ledger.compute() 3회 (10억=무제약 R 트랙 근사 · 1,000만 · 3,000만) + DB.
채널 성적·연구 판정은 반자동(코드 내 표, 갱신일 명기) — 바뀔 때 여기 고친다.
"""
from __future__ import annotations

from datetime import date as _date
from pathlib import Path

import boot  # noqa: F401
import ledger
from db import connect

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_START = "2026-08-11"
CONTRACT_DAYS = 60
MIDCHECK = "2026-09-08"
VERDICT = "2026-11-04"

CHANNELS_ASOF = "2026-08-28"
CHANNELS = [
    ("초하쌤 콜 (방송·글·톡)", "10콜 · 4✅ 1❌ 1△ 4대기", "up"),
    ("초하쌤 방 언급 따라사기", "5일 +1.49% (126건)", "up"),
    ("수강생 반 언급", "5일 −2.20% (224건)", "down"),
    ("소식방 언급", "5일 −6.0% (2,407건)", "down"),
]
RESEARCH = [
    ("채택", "read-across (미국 테마 → 다음날 한국, 64.5% vs 44.3%)"),
    ("채택", "A급 자리 3/3 (낙폭 −15~−3 · 이격 · RSI) — 계약 동결 중"),
    ("채택", "악재 뉴스 회피 필터 · 적자기업 지지선 매수 회피"),
    ("기각", "삼박자 · 기준봉 눌림 · 소식방 따라사기 · 스프링 재진입"),
    ("기각", "448선 단독 (−0.075R) · 448+실적 YoY (판정선 미달)"),
    ("보류", "대장주 우위 (소급 편향 → 저녁 스캔 전방 데이터로 재검증 중)"),
]


def won(x: float) -> str:
    return f"{x:+,.0f}"


def pct_cls(x: float) -> str:
    return "up" if x > 0 else ("down" if x < 0 else "")


def main() -> None:
    today = _date.today()
    db = connect()
    run_days = db.execute(
        "select count(distinct date) from candidates where date >= ? and tier in ('pick','pool')",
        (CONTRACT_START,)).fetchone()[0]
    picks_n = db.execute(
        "select count(*) from (select distinct date, code from candidates where date >= ? and tier='pick')",
        (CONTRACT_START,)).fetchone()[0]

    big = ledger.compute(1_000_000_000)   # 사실상 무제약 → R 트랙 근사
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
        <div class="row">보유 {len(a['open'])}종목 · 현금 {a['cash']:,.0f}원</div>
      </div>"""

    # 두 계좌 병기: 종결은 (일자,종목,결과) 키로, 보유는 종목명 키로 합친다
    c10 = {(c["date"], c["name"], c["label"]): c["pnl"]
           for c in a10["closed"] if c["label"] != "미체결 소멸"}
    c30 = {(c["date"], c["name"], c["label"]): c["pnl"]
           for c in a30["closed"] if c["label"] != "미체결 소멸"}
    def money_td(v):
        return (f"<td class='{pct_cls(v)}'>{won(v)}원</td>" if v is not None
                else "<td class='row'>—</td>")
    closed_rows = "".join(
        f"<tr><td>{k[0][4:6]}/{k[0][6:8]}</td><td>{k[1]}</td>"
        f"<td class='{('up' if k[2]=='목표' else 'down')}'>{k[2]}</td>"
        f"{money_td(c10.get(k))}{money_td(c30.get(k))}</tr>"
        for k in sorted(set(c10) | set(c30)))
    p10 = {p["name"]: p for p in a10["open"]}
    p30 = {p["name"]: p for p in a30["open"]}
    def pos_td(p):
        return (f"<td class='{pct_cls(p['pnl'])}'>{p['shares']}주 {won(p['pnl'])}원</td>"
                if p else "<td class='row'>—</td>")
    all_pos = sorted(set(p10) | set(p30),
                     key=lambda n: -((p30.get(n) or p10.get(n))["pnl"]))
    pos_rows = "".join(
        f"<tr><td>{n}</td>"
        f"<td>{(p10.get(n) or p30.get(n))['fill']:,.0f}</td>"
        f"<td>{(p10.get(n) or p30.get(n))['cur']:,.0f}</td>"
        f"{pos_td(p10.get(n))}{pos_td(p30.get(n))}</tr>"
        for n in all_pos)
    ch_rows = "".join(f"<tr><td>{n}</td><td class='{c}'>{v}</td></tr>" for n, v, c in CHANNELS)
    rs_rows = "".join(
        f"<tr><td class='{('up' if k=='채택' else 'down' if k=='기각' else '')}'>{k}</td><td>{v}</td></tr>"
        for k, v in RESEARCH)

    html = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>테마 레이더 — 검증 현황판</title>
<style>
  body{{font-family:'Noto Serif KR','Nanum Myeongjo',Batang,serif;background:#fff;color:#1a1a1a;
       max-width:60rem;margin:0 auto;padding:1rem 1.2rem;line-height:1.6}}
  h1{{font-size:1.6rem;border-bottom:3px double #1a1a1a;padding-bottom:.4rem;margin-bottom:.2rem}}
  .date{{color:#666;font-size:.85rem;margin-bottom:1.2rem}}
  h2{{font-size:1.05rem;border-bottom:1px solid #999;padding-bottom:.2rem;margin:1.6rem 0 .6rem}}
  .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(15rem,1fr));gap:.8rem}}
  .card{{border:1px solid #ddd;padding: .8rem 1rem}}
  .card h3{{font-size:.85rem;color:#555;margin:0 0 .3rem;font-weight:600}}
  .big{{font-size:1.5rem;font-weight:700}} .unit{{font-size:.9rem;font-weight:400}}
  .sub{{font-size:1rem;font-weight:600}} .row{{font-size:.8rem;color:#555}}
  .up{{color:#c0392b}} .down{{color:#1a4f9c}}
  table{{width:100%;border-collapse:collapse;font-size:.85rem}}
  td,th{{border-bottom:1px solid #eee;padding:.3rem .4rem;text-align:left}}
  .bar{{background:#eee;height:.8rem;margin:.4rem 0}}
  .bar>div{{background:#1a1a1a;height:100%;width:{prog}%}}
  .note{{font-size:.75rem;color:#888;margin-top:1.5rem;border-top:1px solid #ccc;padding-top:.5rem}}
  .tabs{{display:flex;gap:.3rem;border-bottom:2px solid #1a1a1a;margin:1rem 0 0}}
  .tabs button{{font:inherit;font-size:.9rem;padding:.4rem .9rem;border:1px solid #ccc;
    border-bottom:none;background:#f5f5f5;cursor:pointer}}
  .tabs button.on{{background:#1a1a1a;color:#fff;font-weight:700}}
  .sheet{{display:none}} .sheet.on{{display:block}}
  @media(max-width:40rem){{body{{padding:.8rem}}}}
</style></head><body>
<h1>테마 레이더 — 검증 현황판</h1>
<div class="date">갱신 {today.isoformat()} 저녁 · 계약 {CONTRACT_START} ~ 약 {VERDICT} (60거래일)</div>

<div class="tabs">
  <button class="on" data-s="s1">요약</button>
  <button data-s="s2">거래</button>
  <button data-s="s3">채널</button>
  <button data-s="s4">연구</button>
</div>

<div class="sheet on" id="s1">
<h2>계약 진행</h2>
<div class="bar"><div></div></div>
<div class="row">D+{run_days}/{CONTRACT_DAYS} 거래일 ({prog}%) · 중간점검 {MIDCHECK} · 판정 기준: 체결 평균 R ≥ +0.10</div>

<h2>스코어보드</h2>
<div class="cards">
  <div class="card"><h3>R 트랙 (자금 무제약 · 판정 기준)</h3>
    <div class="big {pct_cls(sum(closed_r)+sum(open_r))}">{sum(closed_r)+sum(open_r):+.2f}<span class="unit">R</span></div>
    <div class="sub">종결 {sum(closed_r):+.2f}R · 진행 {sum(open_r):+.2f}R</div>
    <div class="row">픽 {picks_n} · 종결 {len(closed_r)}건 · 보유 {len(open_r)}건 · 미체결 소멸 {lapsed}건</div>
  </div>
  {acct_card(a10, "가상계좌 ① 종자돈 1,000만")}
  {acct_card(a30, "가상계좌 ② 종자돈 3,000만")}
</div>
</div>

<div class="sheet" id="s2">
<h2>종결 거래</h2>
<table><tr><th>일자</th><th>종목</th><th>결과</th><th>계좌① 손익</th><th>계좌② 손익</th></tr>{closed_rows or '<tr><td colspan=5>아직 없음</td></tr>'}</table>

<h2>보유 포지션 (① {len(a10['open'])} · ② {len(a30['open'])}종목)</h2>
<table><tr><th>종목</th><th>진입</th><th>현재</th><th>계좌①</th><th>계좌②</th></tr>{pos_rows or '<tr><td colspan=5>없음</td></tr>'}</table>
<div class="row">— 표시는 그 계좌에선 미보유 (고가주 리스크 규칙·현금 한도 차이)</div>
</div>

<div class="sheet" id="s3">
<h2>채널 성적 <span class="row">(수동 집계 {CHANNELS_ASOF} 기준)</span></h2>
<table>{ch_rows}</table>
</div>

<div class="sheet" id="s4">
<h2>연구 판정 <span class="row">(수동 집계 {CHANNELS_ASOF} 기준)</span></h2>
<table>{rs_rows}</table>
</div>

<script>
document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => {{
  document.querySelectorAll('.tabs button').forEach(x => x.classList.remove('on'));
  document.querySelectorAll('.sheet').forEach(x => x.classList.remove('on'));
  b.classList.add('on');
  document.getElementById(b.dataset.s).classList.add('on');
}});
</script>

<div class="note">관찰·검증 기록용 — 매매 추천 아님 · 주문 기능 없음 · 최종 판단과 실행은 본인.<br>
가상계좌: 리스크 1%/건 · 진입 3일 창 · 손절우선 · +2R 청산 · 20일 기한 · 왕복 비용 0.3% ·
동일 종목 중복 금지 · 종목당 20% 상한. <a href="index.html">→ 장전 브리핑</a></div>
</body></html>"""

    for d in (ROOT / "docs", ROOT / "out"):
        d.mkdir(exist_ok=True)
        (d / "status.html").write_text(html, encoding="utf-8")
    print(f"현황판 생성: docs/status.html (R트랙 {sum(closed_r)+sum(open_r):+.2f}R · "
          f"계좌① {a10['equity']:,.0f} · 계좌② {a30['equity']:,.0f})")


if __name__ == "__main__":
    main()
