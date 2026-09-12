"""국면 플레이북 — '어떤 상황에서 무엇이 오르나'를 우리 데이터로 잰 표 (2026-09-12 사용자 제안, 연구·표시 전용).

방식(learn_comove·oil_comove와 동일): 조건일을 잡고, 그 **다음 한국 거래일**에 전 종목 중앙값 대비 초과수익을
종목·테마별로 집계한다. 참여율 = 중앙값을 이긴 날 비율. 저녁에 조건을 확인하고 아침에 예약을 거는 직장인 흐름에 맞춘 정의.
  · 외부 시세(야후 2년): 상·하위 8% 급변일 — 자산마다 변동성이 달라 고정 %가 아니라 분위수로 자른다.
  · 시장 내부: 코스피 급락·급등, 급락 뒤 반등, 코스닥 괴리, 상한가·급등주 다수일, 거래대금 급증, 외인 대량 순매도·순매수(수급 1년).
  · 테마 대리 국면(대북·중동·방산·정책·계절): learn_comove 결과(data/learned_comove.json)를 그대로 인용.
표시 컷: 거래대금 20일 30억+ · 시총 1,000억+(검증상 회피 구간 제외). 종목 ★ = 참여 70%↑ AND 조건일 30일↑ (2,500종목 다중비교라 ★도 우연 가능 — 전향 추적 필수). 테마는 5종목↑.
**매수 신호 아님** — 관찰 우선순위·회피 참고. DB에 쓰지 않는다. 출력 data/playbook.json → 현황판 '국면' 시트.

실행: python src/playbook.py            (전체 재계산 + 전 종목 일봉 캐시 갱신 — 금요일 주간 세트; --no-refresh 로 갱신 생략)
      python src/playbook.py --flags    (오늘 국면 플래그만 갱신 — 저녁 루틴)
"""
from __future__ import annotations

import bisect
import io
import json
import statistics
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date, datetime, timedelta, timezone
from pathlib import Path

import boot  # noqa: F401
import replay
import tickers
from db import connect
from net import fetch_json
from prices_kr import fetch_ohlc

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "playbook.json"
Q = 0.08            # 급변일 분위수 (상·하위 8% ≈ 2년 40일)
MIN_VAL = 30        # 표시 종목 거래대금 20일 평균(억)
MIN_CAP = 1000      # 표시 종목 시총(억)
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=2y&interval=1d"

# (키, 라벨, 야후 심볼, 방향, 묶음, 통설 — 검증 대상)
EXTERNAL = [
    ("oil_up", "유가 급등", "USO", "up", "원자재", "정유·석유유통·플랜트 수혜, 항공·화학 피해"),
    ("oil_dn", "유가 급락", "USO", "dn", "원자재", "항공·화학·유틸 수혜"),
    ("gas_up", "천연가스 급등", "NG=F", "up", "원자재", "가스공사·LNG 밸류체인"),
    ("copper_up", "구리 급등", "HG=F", "up", "원자재", "전선·풍산"),
    ("copper_dn", "구리 급락", "HG=F", "dn", "원자재", "전선 피해"),
    ("gold_up", "금 급등", "GC=F", "up", "원자재", "금 관련·고려아연"),
    ("grain_up", "곡물 급등", "ZC=F", "up", "원자재", "비료·농약 수혜, 사료·음식료 피해"),
    ("uranium_up", "우라늄 급등", "URA", "up", "원자재", "원전"),
    ("lithium_up", "리튬 급등", "LIT", "up", "원자재", "이차전지"),
    ("lithium_dn", "리튬 급락", "LIT", "dn", "원자재", "이차전지 피해"),
    ("tnx_up", "미 10년 금리 급등", "^TNX", "up", "금리·환율", "은행·보험 수혜, 성장주·바이오·리츠 피해"),
    ("tnx_dn", "미 10년 금리 급락", "^TNX", "dn", "금리·환율", "성장주·바이오 수혜"),
    ("krw_up", "원/달러 급등(원화 약세)", "KRW=X", "up", "금리·환율", "자동차·조선 수출주"),
    ("krw_dn", "원/달러 급락(원화 강세)", "KRW=X", "dn", "금리·환율", "항공·내수·외인 유입 대형주"),
    ("jpy_up", "엔화 급등(엔캐리 청산 경계)", "JPY=X", "dn", "금리·환율", "급락 전조 — 덜 빠지는 묶음 확인"),
    ("dxy_up", "달러 인덱스 급등", "DX-Y.NYB", "up", "금리·환율", "수출주 vs 외인 이탈"),
    ("spx_dn", "미국 증시 급락", "^GSPC", "dn", "해외 증시", "방어주(통신·음식료·유틸)"),
    ("spx_up", "미국 증시 급등", "^GSPC", "up", "해외 증시", "성장주·반도체"),
    ("sox_up", "미국 반도체 급등", "^SOX", "up", "해외 증시", "반도체 소부장 — 누가 앞서나"),
    ("sox_dn", "미국 반도체 급락", "^SOX", "dn", "해외 증시", "반도체 피해, 대체 묶음"),
    ("btc_up", "비트코인 급등", "BTC-USD", "up", "해외 증시", "가상화폐·스테이블코인"),
    ("btc_dn", "비트코인 급락", "BTC-USD", "dn", "해외 증시", "가상화폐 피해"),
    ("sse_up", "중국 증시 급등", "000001.SS", "up", "해외 증시", "화장품·면세·중국 소비"),
    ("hsi_up", "홍콩 증시 급등", "^HSI", "up", "해외 증시", "중국 소비·게임"),
    ("vix_up", "VIX 급등(공포)", "^VIX", "up", "위험 선호", "다음날 반등 선두 / 방어주"),
    ("hyg_dn", "하이일드 채권 급락(위험 회피)", "HYG", "dn", "위험 선호", "먼저 빠지는 묶음 = 회피"),
]
PROXY_THEMES = ["남북경협", "미국-이란 전쟁", "방산", "정책·정치", "폭염·계절", "제약바이오"]


def quantile(vals: list[float], q: float) -> float:
    s = sorted(vals)
    return s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))]


def yahoo(sym: str) -> list[tuple[str, float]]:
    """(날짜 YYYYMMDD, 종가). 선물·지수는 타임스탬프가 전날 밤(UTC 18시 이후)이면 다음 날짜로 정규화."""
    d = fetch_json(CHART.format(sym=sym.replace("^", "%5E")), timeout=20)
    res = d["chart"]["result"][0]
    out = []
    for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]):
        if c is None:
            continue
        dt = datetime.fromtimestamp(t, tz=timezone.utc)
        if dt.hour >= 18:
            dt = dt + timedelta(days=1)
        out.append((dt.strftime("%Y%m%d"), c))
    return out


def _yahoo_safe(sym: str) -> list[tuple[str, float]]:
    try:
        return yahoo(sym)
    except Exception as exc:  # noqa: BLE001
        print(f"  ! {sym} 수신 실패: {type(exc).__name__}: {exc}")
        return []


def changes(series: list[tuple[str, float]]) -> list[tuple[str, float]]:
    return [(d1, c1 / c0 - 1) for (d0, c0), (d1, c1) in zip(series, series[1:]) if c0]


def refresh_universe(bars_all: dict, last_trading: str) -> int:
    """전 종목 일봉을 최근 거래일까지 받아 캐시(data/cache/ohlc, git 제외)에 보존 — 주 1회(금요일 세트).
    백테스트용 캐시는 만든 날에 멈춰 있어서(8/10) 그대로 두면 최근 한 달이 분석에서 빠진다. 옛 세대 파일은 지운다."""
    from net import CACHE_DIR
    cdir = CACHE_DIR / "ohlc"
    cdir.mkdir(parents=True, exist_ok=True)
    stale = [c for c, b in bars_all.items() if b and b[-1]["date"] < last_trading]
    if not stale:
        return 0

    def one(code):
        try:
            return code, fetch_ohlc(code, "20240701", last_trading)
        except Exception:  # noqa: BLE001
            return code, None
    n = 0
    with ThreadPoolExecutor(6) as ex:
        for code, rows in ex.map(one, stale):
            if not rows or len(rows) < 80 or rows[-1]["date"] <= bars_all[code][-1]["date"]:
                continue
            bars_all[code] = rows
            for old in cdir.glob(f"{code}_*.json"):
                old.unlink()
            (cdir / f"{code}_20240701_{rows[-1]['date']}.json").write_text(json.dumps(rows), encoding="utf-8")
            n += 1
    print(f"  · 일봉 캐시 갱신 {n}/{len(stale)}종목 → {last_trading}")
    return n


def load_market():
    bars_all = replay.load_bars()
    ret: dict[str, dict[str, float]] = {}
    val20: dict[str, float] = {}
    for code, bars in bars_all.items():
        bs = bars[-506:]
        if len(bs) < 60:
            continue
        val20[code] = statistics.mean(b["close"] * b["volume"] for b in bs[-20:]) / 1e8
        m = {}
        for a, b in zip(bs, bs[1:]):
            if a["close"] and b["close"]:
                m[b["date"]] = b["close"] / a["close"] - 1
        ret[code] = m
    by_date: dict[str, list[float]] = defaultdict(list)
    for m in ret.values():
        for d, r in m.items():
            by_date[d].append(r)
    market = {d: statistics.median(v) for d, v in by_date.items() if len(v) >= 1000}
    kr_dates = sorted(market)
    return bars_all, ret, val20, market, kr_dates


def internal_metrics(bars_all, ret, val20, market, kr_dates):
    """시장 내부 지표(날짜별): 코스피·코스닥 등락, 상한가 수, +10% 수, 거래대금, 외인 순매수(억)."""
    today = _date.today().strftime("%Y%m%d")
    ks = {b["date"]: b["close"] for b in fetch_ohlc("KOSPI", "20240701", today)}
    kq = {b["date"]: b["close"] for b in fetch_ohlc("KOSDAQ", "20240701", today)}
    def chg(series):
        ds = sorted(series)
        return {d1: series[d1] / series[d0] - 1 for d0, d1 in zip(ds, ds[1:]) if series[d0]}
    ks_c, kq_c = chg(ks), chg(kq)
    lim: dict[str, int] = defaultdict(int)
    big: dict[str, int] = defaultdict(int)
    tval: dict[str, float] = defaultdict(float)
    for code, bars in bars_all.items():
        for b in bars[-506:]:
            d = b["date"]
            if d not in market:
                continue
            r = ret.get(code, {}).get(d)
            if r is None:
                continue
            if r >= 0.29:
                lim[d] += 1
            if r >= 0.10:
                big[d] += 1
            tval[d] += b["close"] * b["volume"] / 1e8
    frgn: dict[str, float] = defaultdict(float)
    # 수급 파일(전 종목, 매일 갱신)의 종가로 일봉 캐시보다 최신인 날짜의 상한가·급등주·거래대금을 보강한다 (저녁 플래그용)
    lim2: dict[str, int] = defaultdict(int)
    big2: dict[str, int] = defaultdict(int)
    tval2: dict[str, float] = defaultdict(float)
    last_bar = kr_dates[-1] if kr_dates else "0"
    fdir = ROOT / "data" / "flows"
    if fdir.exists():
        for p in fdir.glob("*.json"):
            try:
                fl = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            ds = sorted(fl)
            for d in ds:
                v = fl[d]
                if len(v) >= 4 and d in ks:
                    frgn[d] += v[1] * v[2] / 1e8
            for d0, d1 in zip(ds, ds[1:]):
                if d1 > last_bar and d1 in ks and len(fl[d1]) >= 4 and fl[d0][2]:
                    r = fl[d1][2] / fl[d0][2] - 1
                    if r >= 0.29:
                        lim2[d1] += 1
                    if r >= 0.10:
                        big2[d1] += 1
                    tval2[d1] += fl[d1][2] * fl[d1][3] / 1e8
    for d in lim2:
        lim[d], big[d], tval[d] = lim2[d], big2[d], tval2[d]
    return {"kospi": ks_c, "kosdaq": kq_c, "lim": dict(lim), "big": dict(big), "tval": dict(tval), "frgn": dict(frgn)}


def internal_conditions(im, kr_dates):
    """(키, 라벨, 묶음, 통설, 조건일 리스트(관측일 D), 기준 설명, 최신 관측값, 최신 충족 여부)."""
    out = []
    ks, kq = im["kospi"], im["kosdaq"]
    days = sorted(d for d in ks if d >= (kr_dates[0] if kr_dates else "0"))  # 코스피 달력 기준 (일봉 캐시보다 최신 날짜 포함)
    def pick(metric: dict, label, key, group, note, side, q=Q, fmt=lambda x: f"{x:+.1%}"):
        vals = {d: metric[d] for d in days if d in metric}
        if len(vals) < 100:
            return
        thr = quantile(list(vals.values()), 1 - q if side == "up" else q)
        sel = [d for d, v in vals.items() if (v >= thr if side == "up" else v <= thr)]
        last_d = max(vals)
        last_v = vals[last_d]
        active = last_v >= thr if side == "up" else last_v <= thr
        out.append((key, label, group, note, sel, f"{'≥' if side == 'up' else '≤'} {fmt(thr)}", fmt(last_v), active, last_d))
    pick(ks, "코스피 급락일", "kospi_dn", "시장 내부", "다음날 덜 빠지는·반등하는 묶음", "dn")
    pick(ks, "코스피 급등일", "kospi_up", "시장 내부", "다음날 이어가는 묶음", "up")
    # 급락 뒤 반등일: 전날 하위 8% 급락 & 당일 +1%↑ → 다음날
    thr_dn = quantile([ks[d] for d in days], Q)
    reb = [d1 for d0, d1 in zip(days, days[1:]) if ks[d0] <= thr_dn and ks[d1] >= 0.01]
    last = days[-1]
    out.append(("rebound", "급락 뒤 반등일", "시장 내부", "반등이 이어지는 묶음", reb, f"전날 ≤ {thr_dn:+.1%} & 당일 ≥ +1%",
                f"{ks[last]:+.1%}(전날 {ks[days[-2]]:+.1%})", (ks[days[-2]] <= thr_dn and ks[last] >= 0.01), last))
    gap = {d: kq[d] - ks[d] for d in days if d in kq}
    pick(gap, "코스닥 우위일(코스닥−코스피)", "kq_gap_up", "시장 내부", "중소형 테마 확산 여부", "up", fmt=lambda x: f"{x * 100:+.1f}%p")
    pick(gap, "코스피 우위일(대형주 장)", "kq_gap_dn", "시장 내부", "대형주 장세 지속 여부", "dn", fmt=lambda x: f"{x * 100:+.1f}%p")
    pick(im["lim"], "상한가 다수일(과열)", "limit_many", "시장 내부", "과열 다음날 — 추격 위험 확인", "up", fmt=lambda x: f"{x:.0f}개")
    pick(im["big"], "급등주(+10%) 다수일", "big_many", "시장 내부", "테마 순환 속도", "up", fmt=lambda x: f"{x:.0f}개")
    pick(im["tval"], "시장 거래대금 급증일", "tval_up", "시장 내부", "돈이 몰린 다음날", "up", fmt=lambda x: f"{x / 1e4:.1f}조")
    if im["frgn"]:
        pick(im["frgn"], "외인 대량 순매도일", "frgn_sell", "수급", "대형주 vs 중소형 괴리, 방어주", "dn", q=0.10, fmt=lambda x: f"{x:+,.0f}억")
        pick(im["frgn"], "외인 대량 순매수일", "frgn_buy", "수급", "외인 주도 묶음", "up", q=0.10, fmt=lambda x: f"{x:+,.0f}억")
    return out


def score(target_dates: list[str], ret, val20, market, info, mcap) -> dict:
    """조건일 집합 → 종목·테마 통계."""
    tds = [d for d in target_dates if d in market]
    if not tds:
        return {"n": 0}
    rows = []
    for code, m in ret.items():
        if val20.get(code, 0) < 10:
            continue
        ex = [m[d] - market[d] for d in tds if d in m]
        if len(ex) < 0.8 * len(tds):
            continue
        part = sum(1 for e in ex if e > 0) / len(ex)
        rows.append((part, statistics.mean(ex), code))
    th: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for part, mex, code in rows:
        for t in info.get(code, ("", ""))[1].split(","):
            t = t.strip()
            if t:
                th[t].append((part, mex))
    themes = [(statistics.median(p for p, _ in v), statistics.median(m for _, m in v), len(v), t)
              for t, v in th.items() if len(v) >= 5]
    themes.sort(reverse=True)
    disp = [r for r in rows if val20[r[2]] >= MIN_VAL and (mcap.get(r[2]) or 0) >= MIN_CAP]
    disp.sort(key=lambda x: (x[0], x[1]), reverse=True)
    n = len(tds)
    def stock_row(part, mex, code):
        return {"name": info.get(code, (code, ""))[0], "code": code, "part": round(part, 2), "excess": round(mex * 100, 2),
                "val": round(val20[code]), "cap": round(mcap.get(code) or 0), "themes": info.get(code, ("", ""))[1],
                "star": bool(n >= 30 and part >= 0.70)}
    return {
        "n": n, "dates_last": tds[-3:],
        "mkt_next": round(statistics.mean(market[d] for d in tds) * 100, 2),
        "top_themes": [{"theme": t, "part": round(p, 2), "excess": round(m * 100, 2), "n": k} for p, m, k, t in themes[:5]],
        "bottom_themes": [{"theme": t, "part": round(p, 2), "excess": round(m * 100, 2), "n": k} for p, m, k, t in themes[-3:]],
        "top_stocks": [stock_row(*r) for r in disp[:8]],
        "bottom_stocks": [stock_row(*r) for r in sorted(disp, key=lambda x: (x[0], x[1]))[:4]],
    }


def load_info():
    db = connect()
    info = {code: (name, themes or "") for name, code, themes in db.execute("select name, code, themes from stocks")}
    for code in list(replay.load_bars().keys()):
        if code not in info:  # 지도 밖 종목 이름은 KIND 표에서
            info[code] = (tickers.to_name(code) or code, "")
    try:
        mc = json.loads((ROOT / "data" / "mcap.json").read_text(encoding="utf-8"))["stocks"]
        mcap = {c: (v or {}).get("mcap_eok") or 0 for c, v in mc.items()}
    except Exception:  # noqa: BLE001
        mcap = {}
    return info, mcap


def build(flags_only: bool = False) -> dict:
    bars_all, ret, val20, market, kr_dates = load_market()
    if not flags_only and "--no-refresh" not in sys.argv:
        ks_last = fetch_ohlc("KOSPI", "20260801", _date.today().strftime("%Y%m%d"))[-1]["date"]
        if refresh_universe(bars_all, ks_last):
            bars_all, ret, val20, market, kr_dates = load_market()
    info, mcap = load_info()
    prev = json.loads(OUT.read_text(encoding="utf-8")) if (flags_only and OUT.exists()) else None
    syms = sorted({s for _, _, s, _, _, _ in EXTERNAL})
    with ThreadPoolExecutor(6) as ex:
        fetched = dict(zip(syms, ex.map(_yahoo_safe, syms)))
    def next_kr(d):
        i = bisect.bisect_right(kr_dates, d)
        return kr_dates[i] if i < len(kr_dates) else None
    conditions, flags = [], []
    for key, label, sym, side, group, note in EXTERNAL:
        ser = fetched.get(sym) or []
        ch = changes(ser)
        if len(ch) < 200:
            flags.append({"key": key, "label": label, "value": None, "threshold": None, "active": False, "note": "시세 부재"})
            continue
        vals = [c for _, c in ch]
        thr = quantile(vals, 1 - Q) if side == "up" else quantile(vals, Q)
        sel_ext = [d for d, c in ch if (c >= thr if side == "up" else c <= thr)]
        targets = sorted({t for t in (next_kr(d) for d in sel_ext) if t})
        last_d, last_c = ch[-1]
        active = last_c >= thr if side == "up" else last_c <= thr
        flags.append({"key": key, "label": label, "group": group, "asof": last_d, "value": round(last_c * 100, 2),
                      "threshold": round(thr * 100, 2), "side": side, "active": bool(active)})
        if flags_only:
            continue
        st = score(targets, ret, val20, market, info, mcap)
        conditions.append({"key": key, "label": label, "group": group, "note": note, "symbol": sym,
                           "rule": f"{sym} 1일 {'≥' if side == 'up' else '≤'} {thr:+.1%} (상·하위 {Q:.0%})", **st})
    for key, label, group, note, sel, rule, last_v, active, last_d in internal_conditions(
            internal_metrics(bars_all, ret, val20, market, kr_dates), kr_dates):
        flags.append({"key": key, "label": label, "group": group, "asof": last_d, "value": last_v, "threshold": rule, "active": bool(active)})
        if flags_only:
            continue
        targets = sorted({t for t in (next_kr(d) for d in sel) if t})
        st = score(targets, ret, val20, market, info, mcap)
        conditions.append({"key": key, "label": label, "group": group, "note": note, "symbol": None, "rule": rule, **st})
    proxies = []
    lc = ROOT / "data" / "learned_comove.json"
    if lc.exists():
        L = json.loads(lc.read_text(encoding="utf-8"))
        for t in PROXY_THEMES:
            th = (L.get("themes") or {}).get(t)
            if not th:
                continue
            proxies.append({"theme": t, "n": len(th.get("spike_days", [])), "last": max(th.get("spike_days", ["-"])),
                            "leaders": [{"name": x["name"], "part": x.get("part", 0), "excess": x.get("avg_excess", x.get("excess", 0))} for x in th.get("leaders", [])[:5]],
                            "candidates": [{"name": x["name"], "part": x.get("part", 0), "excess": x.get("avg_excess", x.get("excess", x.get("excess_pp", 0)))} for x in th.get("candidates", [])[:5]]})
    out = {
        "generated": _date.today().isoformat(),
        "window": {"from": kr_dates[0], "to": kr_dates[-1], "days": len(kr_dates)},
        "params": {"quantile": Q, "min_val_eok": MIN_VAL, "min_cap_eok": MIN_CAP, "mapping": "조건일 → 다음 한국 거래일"},
        "conditions": (prev or {}).get("conditions", []) if flags_only else conditions,
        "proxies": (prev or {}).get("proxies", []) if flags_only else proxies,
        "flags": {"asof": _date.today().isoformat(), "active": [f for f in flags if f.get("active")], "all": flags},
    }
    if flags_only and prev:
        out["generated"] = prev.get("generated", out["generated"])
        out["window"] = prev.get("window", out["window"])
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main() -> None:
    flags_only = "--flags" in sys.argv
    out = build(flags_only)
    act = out["flags"]["active"]
    print(f"국면 플래그 ({out['flags']['asof']}): " + (" · ".join(
        f"{f['label']} {f['value'] if isinstance(f['value'], str) else f'{f['value']:+.1f}%'}({f.get('asof', '')[4:]})" for f in act) if act else "해당 없음"))
    if flags_only:
        return
    print(f"\n국면 플레이북 — 창 {out['window']['from']}~{out['window']['to']} ({out['window']['days']}거래일) · 조건 {len(out['conditions'])}개 · 대리 {len(out['proxies'])}개")
    for c in out["conditions"]:
        if c.get("n", 0) == 0:
            print(f"  {c['label']}: 조건일 없음")
            continue
        th = " · ".join(f"{t['theme']} {t['part']:.0%}/{t['excess']:+.1f}%" for t in c["top_themes"][:3])
        stk = " · ".join(f"{s['name']}{'★' if s['star'] else ''} {s['part']:.0%}" for s in c["top_stocks"][:5])
        print(f"  [{c['group']}] {c['label']:<20} n={c['n']:>3} 다음날 시장 {c['mkt_next']:+.2f}% | 테마: {th} | 종목: {stk}")
    for p in out["proxies"]:
        print(f"  [대리] {p['theme']:<10} 급등일 {p['n']}일 | 대장: " + " · ".join(f"{x['name']} {x['part']:.0%}" for x in p["leaders"][:3])
              + " | 지도 밖: " + " · ".join(f"{x['name']} {x['part']:.0%}" for x in p["candidates"][:3]))


if __name__ == "__main__":
    main()
