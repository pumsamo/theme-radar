"""초하쌤 공개 강의 방향 언급 소급 채점 (2026-09-07, 연구용) — 20/60거래일 수익률과 코스피·코스닥 대비 초과수익. 종목명은 강의 자막에서 뽑은 것(ASR)이라 tickers.to_code 미매칭은 건너뛴다."""
import json, sys, urllib.request, statistics as st
import boot  # noqa: F401 (src 경로 부팅)
import boot, tickers, prices_kr  # noqa

# KOSPI (Yahoo, 2y)
import datetime as dt
def yahoo(sym):
    req = urllib.request.Request(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=2y&interval=1d",
                                 headers={"User-Agent": "Mozilla/5.0"})
    j = json.load(urllib.request.urlopen(req, timeout=30))["chart"]["result"][0]
    return {dt.datetime.utcfromtimestamp(t + 9 * 3600).strftime("%Y%m%d"): c
            for t, c in zip(j["timestamp"], j["indicators"]["quote"][0]["close"]) if c}
ks = yahoo("%5EKS11"); kq = yahoo("%5EKQ11")
kdates = sorted(ks); qdates = sorted(kq)
BENCH = ks  # 기본 코스피

M = {  # date: ([긍정], [부정])
 "20250827": (["씨티케이","세림B&G","네패스아크","상신이디피","두산퓨얼셀","엑스게이트","우리넷","인벤티지랩","티엑스알로보틱스"],
              ["동국씨엠","갤럭시아머니트리","스튜디오미르","대한조선","한국첨단소재"]),
 "20250917": (["네패스아크","두산테스나","톱텍","HPSP","인벤티지랩","블루엠텍"], ["한미반도체"]),
 "20251105": (["대덕전자","심텍","솔리드","케이엠더블유","대한광통신","NAVER","범한퓨얼셀","LG에너지솔루션","엘앤에프","포스코퓨처엠",
               "잉글우드랩","SKC","태웅","한국전력","JYP Ent."], ["LG CNS"]),
 "20251126": (["부광약품","테크윙","두산에너빌리티","NAVER","엔씨소프트","폴라리스AI","아모레퍼시픽","하이브","대한광통신","범한퓨얼셀"],
              ["PSK","HJ중공업"]),
 "20260121": (["에스엘","두산로보틱스","뉴로메카","에스피지","레인보우로보틱스","효성티앤씨","덕산하이메탈","티피씨글로벌","로보스타",
               "나라스페이스테크놀로지","AP위성","두산에너빌리티","HL만도"], []),
 "20260310": (["브이티","두산에너빌리티","대한조선","HJ중공업","HD한국조선해양","해성에어로보틱스","미래에셋벤처투자","하나솔루션","대한광통신"],
              ["HL만도"]),
 "20260331": (["에코프로"], ["카페24","삼성전자","SK하이닉스","삼성SDI","신풍제약","금양"]),
}

def idx_ret(d0, n, series=None, dates=None):
    series = series or ks; dates = dates or kdates
    i = next((k for k, d in enumerate(dates) if d >= d0), None)
    if i is None or i + n >= len(dates): return None
    return series[dates[i + n]] / series[dates[i]] - 1

rows, miss = [], []
for d0, (pos, neg) in M.items():
    for names, sign in ((pos, "+"), (neg, "−")):
        for n in names:
            code = tickers.to_code(n)
            if not code: miss.append(f"{d0[4:]} {n}"); continue
            try: b = prices_kr.fetch_ohlc(code, "20250801", "20260907")
            except Exception as e: miss.append(f"{d0[4:]} {n}({e})"); continue
            cl = [x["close"] for x in b if x["date"] >= d0]
            if len(cl) < 61: miss.append(f"{d0[4:]} {n}(데이터 {len(cl)}일)"); continue
            r20, r60 = cl[20]/cl[0]-1, cl[60]/cl[0]-1
            k20, k60 = idx_ret(d0, 20), idx_ret(d0, 60)
            q20, q60 = idx_ret(d0, 20, kq, qdates), idx_ret(d0, 60, kq, qdates)
            rows.append((d0, sign, n, r20, r60, r20-k20, r60-k60, r20-q20, r60-q60))

print(f"{'일자':6} {'방향':2} {'종목':14} {'20일':>7} {'60일':>7} {'αKS60':>7} {'αKQ60':>7}")
for d0, s, n, r20, r60, a20, a60, b20, b60 in rows[:9]:
    print(f"{d0[2:]:6} {s:2} {n:14} {r20:+7.1%} {r60:+7.1%} {a60:+7.1%} {b60:+7.1%}")
print("  ... (전체 표는 생략)")
for d0 in M:
    g = [r for r in rows if r[0] == d0 and r[1] == "+"]
    if g: print(f"{d0}: 긍정 n={len(g)} 절대60 {st.mean(r[4] for r in g):+.1%} · αKS60 {st.mean(r[6] for r in g):+.1%} · αKQ60 {st.mean(r[8] for r in g):+.1%} · 코스피60 {idx_ret(d0,60):+.1%} 코스닥60 {idx_ret(d0,60,kq,qdates):+.1%}")
for s in "+−":
    g = [r for r in rows if r[1] == s]
    if not g: continue
    print(f"\n[{s}] n={len(g)} 절대20 {st.mean(r[3] for r in g):+.1%} 절대60 {st.mean(r[4] for r in g):+.1%} | "
          f"αKS20 {st.mean(r[5] for r in g):+.1%} ({sum(r[5]>0 for r in g)}/{len(g)}) αKS60 {st.mean(r[6] for r in g):+.1%} ({sum(r[6]>0 for r in g)}/{len(g)}) | "
          f"αKQ20 {st.mean(r[7] for r in g):+.1%} ({sum(r[7]>0 for r in g)}/{len(g)}) αKQ60 {st.mean(r[8] for r in g):+.1%} ({sum(r[8]>0 for r in g)}/{len(g)})")
print("\n미매칭/제외:", ", ".join(miss))
