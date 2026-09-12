"""유가 동행 분석 (읽기 전용 연구, 2026-09-12 사용자 질문 "유가 오를 때 오르는 종목은?").

USO(WTI ETF) 일간 +2%↑ 날의 다음 한국 거래일에 전 종목 중앙값 대비 초과수익·참여율을 잰다 (learn_comove와 같은 방식).
DB에 쓰지 않는다. 결과는 지도·회피 참고용이지 매수 신호가 아니다 (뉴스·매크로 층은 매수 신호로 쓰지 않는다는 검증 원칙).
실행: python src/oil_comove.py
"""
import sys, statistics
from collections import defaultdict
from datetime import datetime, timezone
R = r"C:\Users\pumsa_yvvjwu4\Desktop\클로드\무지랭이\theme-radar"
sys.path.insert(0, R + r"\src")
import boot  # noqa
import replay, tickers
from db import connect
from net import fetch_json

def chart(sym, rng="1y"):
    d = fetch_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d", timeout=20)
    res = d["chart"]["result"][0]
    ts, cl = res["timestamp"], res["indicators"]["quote"][0]["close"]
    return [(datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y%m%d"), c) for t, c in zip(ts, cl) if c is not None]

# 1) 유가 사실 확인 (WTI·브렌트 선물, 수준·최근 변화)
for sym, nm in (("CL=F", "WTI"), ("BZ=F", "브렌트")):
    s = chart(sym)
    c = [x[1] for x in s]
    hi = max(c); i_hi = c.index(hi)
    print(f"{nm}: 최근 {c[-1]:.2f} · 1일 {c[-1]/c[-2]-1:+.1%} · 5일 {c[-1]/c[-6]-1:+.1%} · 20일 {c[-1]/c[-21]-1:+.1%} · 60일 {c[-1]/c[-61]-1:+.1%} · 52주 고점 {hi:.2f}({s[i_hi][0]}) 比 {c[-1]/hi-1:+.1%}")
    print("   최근 8일:", " ".join(f"{d[4:]}:{v:.1f}" for d, v in s[-8:]))

# 2) 미국 USO(WTI ETF, 주식형이라 날짜 정렬 확실)로 '유가 급등일' 정의 → 다음 한국 거래일 반응
uso = chart("USO")
up_days = []
for (d0, c0), (d1, c1) in zip(uso, uso[1:]):
    r = c1 / c0 - 1
    if r >= 0.02:
        up_days.append((d1, r))
print(f"\nUSO 일간 +2%↑ 날: {len(up_days)}일 (최근 1년)")

bars_all = replay.load_bars()
kr_dates = sorted({b["date"] for bars in bars_all.values() for b in bars[-300:]})
def next_kr(d):
    for k in kr_dates:
        if k > d: return k
    return None
target = {}
for d, r in up_days:
    k = next_kr(d)
    if k: target[k] = r
print(f"매핑된 한국 거래일: {len(target)}일, 예: {sorted(target)[-5:]}")

# 종목별 일간 수익률, 그날 시장 중앙값
ret = {}
for code, bars in bars_all.items():
    m = {}
    for a, b in zip(bars, bars[1:]):
        if a["close"] and b["close"] and b["date"] in target:
            m[b["date"]] = b["close"] / a["close"] - 1
    if m: ret[code] = m
by_date = defaultdict(list)
for code, m in ret.items():
    for d, r in m.items(): by_date[d].append(r)
market = {d: statistics.median(v) for d, v in by_date.items() if len(v) >= 200}
print(f"유가 급등 다음날 한국 시장 중앙값 평균: {statistics.mean(market.values()):+.2%} ({len(market)}일)")

db = connect()
info = {code: (name, (themes or "")) for name, code, themes in db.execute("select name, code, themes from stocks")}
def val20(code):
    bs = bars_all[code][-20:]
    return statistics.mean(b["close"] * b["volume"] for b in bs) / 1e8 if bs else 0
rows = []
for code, m in ret.items():
    ex = [(r - market[d]) for d, r in m.items() if d in market]
    if len(ex) < 0.8 * len(market): continue
    v = val20(code)
    if v < 10: continue
    part = sum(1 for e in ex if e > 0) / len(ex)
    rows.append((part, statistics.mean(ex), len(ex), code, info.get(code, (code, ""))[0], info.get(code, ("", ""))[1], v))
rows.sort(key=lambda x: (x[0], x[1]), reverse=True)
print(f"\n■ 유가 급등 다음날 시장 초과수익 상위 (참여율 = 중앙값 이긴 날 비율, n={len(market)}일, 거래대금 20일 10억+, {len(rows)}종목 중)")
for part, mex, n, code, nm, th, v in rows[:30]:
    print(f"  {nm:<12} 참여 {part:>4.0%} · 평균 초과 {mex:+.2%} · 거래대금 {v:,.0f}억 · {th[:30]}")

# 테마별 집계
th_rows = defaultdict(list)
for part, mex, n, code, nm, th, v in rows:
    for t in th.split(","):
        t = t.strip()
        if t: th_rows[t].append((part, mex))
print("\n■ 테마별(지도) 구성 종목 중앙값 — 3종목 이상")
agg = [(statistics.median(p for p, _ in v), statistics.median(m for _, m in v), len(v), t) for t, v in th_rows.items() if len(v) >= 3]
agg.sort(reverse=True)
for p, m, n, t in agg[:14]:
    print(f"  {t:<14} 참여 {p:>4.0%} · 초과 {m:+.2%} · {n}종목")
print("  ...하위:")
for p, m, n, t in agg[-4:]:
    print(f"  {t:<14} 참여 {p:>4.0%} · 초과 {m:+.2%} · {n}종목")

# 지명 종목
print("\n■ 지명 종목")
byname = {code: (part, mex, n, v) for part, mex, n, code, nm, th, v in rows}
allrows = {code: None for code in ret}
for nm in ("화성밸브", "성광벤드", "태광", "하이록코리아", "S-Oil", "SK이노베이션", "GS", "HD현대", "한국카본", "동성화인텍", "HMM", "팬오션", "대한해운", "흥아해운",
           "넥스틸", "세아제강", "휴스틸", "삼성중공업", "한화오션", "HD현대중공업", "흥구석유", "중앙에너비스", "한국석유", "극동유화", "SH에너지화학", "대성산업",
           "삼성E&A", "비에이치아이", "한국가스공사", "지에스이", "한화솔루션", "OCI홀딩스", "포스코인터내셔널", "현대코퍼레이션", "삼성물산", "LS ELECTRIC"):
    code = tickers.to_code(nm)
    if not code: print(f"  {nm}: 코드 미매칭"); continue
    if code in byname:
        part, mex, n, v = byname[code]
        print(f"  {nm:<10} 참여 {part:>4.0%} · 평균 초과 {mex:+.2%} · n={n} · 거래대금 {v:,.0f}억 · 지도: {info.get(code, ('', ''))[1] or '없음'}")
    elif code in ret:
        ex = [(r - market[d]) for d, r in ret[code].items() if d in market]
        print(f"  {nm:<10} (필터 밖: n={len(ex)}, 거래대금 {val20(code):,.0f}억) 참여 {sum(1 for e in ex if e > 0)/max(1,len(ex)):.0%} · 초과 {statistics.mean(ex) if ex else 0:+.2%}")
    else:
        print(f"  {nm}: 일봉 캐시 없음")
