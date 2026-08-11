"""국내 일봉 시세 + 기술지표.

지시문은 장중 실시간(호가·체결·분봉 VWAP)을 범위 밖으로 못박았다. 여기서 다루는 건
**일봉/종가 레벨뿐**이다. 그래도 진입가·손절선·목표가를 근거 있게 뽑기엔 충분하다.
"""
from __future__ import annotations

import re
import statistics
from concurrent.futures import ThreadPoolExecutor

import boot  # noqa: F401
from net import cached, fetch

OHLC_URL = ("https://api.finance.naver.com/siseJson.naver"
            "?symbol={code}&requestType=1&startTime={start}&endTime={end}&timeframe=day")
ROW_RE = re.compile(r'\["(\d{8})",\s*([\d.]+),\s*([\d.]+),\s*([\d.]+),\s*([\d.]+),\s*(\d+)')

MANAGEMENT_URL = "https://finance.naver.com/sise/management.naver"
ALERT_URL = "https://finance.naver.com/sise/investment_alert.naver?type={t}"


def fetch_ohlc(code: str, start: str, end: str) -> list[dict]:
    raw = fetch(OHLC_URL.format(code=code, start=start, end=end), timeout=20).decode(
        "utf-8", errors="replace")
    rows = [{"date": d, "open": float(o), "high": float(h), "low": float(low),
             "close": float(c), "volume": int(v)}
            for d, o, h, low, c, v in ROW_RE.findall(raw)]
    # 가격 0이나 결측은 의심 데이터 — 지시문 1번대로 해당 종목을 스킵시키기 위해 걸러낸다.
    return [r for r in rows if r["close"] > 0 and r["high"] >= r["low"] > 0]


def _sma(vals: list[float], n: int) -> float | None:
    return round(sum(vals[-n:]) / n, 2) if len(vals) >= n else None


def _atr(rows: list[dict], n: int = 14) -> float | None:
    if len(rows) < n + 1:
        return None
    trs = []
    for prev, cur in zip(rows[-n - 1:-1], rows[-n:]):
        trs.append(max(cur["high"] - cur["low"],
                       abs(cur["high"] - prev["close"]),
                       abs(cur["low"] - prev["close"])))
    return round(sum(trs) / len(trs), 2)


def indicators(rows: list[dict]) -> dict | None:
    """일봉 → 판정에 필요한 값들. 데이터가 모자라면 None (지어내지 않는다)."""
    if len(rows) < 25:
        return None
    closes = [r["close"] for r in rows]
    last = rows[-1]

    ma5, ma20, ma60 = _sma(closes, 5), _sma(closes, 20), _sma(closes, 60)
    if not ma5 or not ma20:
        return None

    vol20 = statistics.mean(r["volume"] for r in rows[-20:])
    value20 = statistics.mean(r["close"] * r["volume"] for r in rows[-20:])
    hi20 = max(r["high"] for r in rows[-20:])
    lo20 = min(r["low"] for r in rows[-20:])
    hi5 = max(r["high"] for r in rows[-5:])
    lo3 = min(r["low"] for r in rows[-3:])

    # 확인 층: 최근 20거래일 중 +5% 이상 마감한 일수 = "며칠째 돈이 붙었나"
    surge20 = sum(1 for prev, cur in zip(rows[-21:-1], rows[-20:])
                  if prev["close"] > 0 and cur["close"] / prev["close"] - 1 >= 0.05)

    # 60일 고점 대비 낙폭 — 위치별 성적 지도에서 가장 강한 구분자였다
    hi60 = max(r["high"] for r in rows[-60:]) if len(rows) >= 60 else max(
        r["high"] for r in rows)

    # RSI(14) Wilder
    rsi = None
    if len(rows) >= 30:
        seg = closes[-31:]
        gains = [max(0.0, seg[i] - seg[i - 1]) for i in range(1, len(seg))]
        losses = [max(0.0, seg[i - 1] - seg[i]) for i in range(1, len(seg))]
        ag, al = sum(gains[:14]) / 14, sum(losses[:14]) / 14
        for i in range(14, len(gains)):
            ag = (ag * 13 + gains[i]) / 14
            al = (al * 13 + losses[i]) / 14
        rsi = round(100.0 if al == 0 else 100 - 100 / (1 + ag / al), 1)

    # 볼린저 %B (20, 2σ)
    pctb = None
    seg20 = closes[-20:]
    m = sum(seg20) / 20
    sd = (sum((x - m) ** 2 for x in seg20) / 20) ** 0.5
    if sd > 0:
        pctb = round((last["close"] - (m - 2 * sd)) / (4 * sd), 2)

    return {
        "date": last["date"],
        "close": last["close"],
        "open": last["open"],
        "high": last["high"],
        "low": last["low"],
        "chg_pct": round((last["close"] / rows[-2]["close"] - 1) * 100, 2),
        "ma5": ma5, "ma20": ma20, "ma60": ma60,
        "atr": _atr(rows),
        "vol": last["volume"],
        "vol_ratio": round(last["volume"] / vol20, 2) if vol20 else None,
        "value20_eok": round(value20 / 1e8, 1),          # 20일 평균 거래대금(억원)
        "hi20": hi20, "lo20": lo20, "hi5": hi5, "lo3": lo3,
        "from_hi20": round((last["close"] / hi20 - 1) * 100, 2),
        "ext_ma20": round(last["close"] / ma20, 3),      # 20일선 이격
        "aligned": bool(ma60 and ma5 > ma20 > ma60),     # 정배열
        "inverse": bool(ma60 and ma5 < ma20 < ma60),     # 역배열
        "surge20": surge20,
        "hi60": hi60,
        "from_hi60": round((last["close"] / hi60 - 1) * 100, 2),
        "rsi": rsi,
        "pctb": pctb,
        "n_bars": len(rows),
    }


def bulk_indicators(codes: list[str], start: str, end: str,
                    workers: int = 6) -> tuple[dict[str, dict], dict[str, str]]:
    """{code: indicators}, {code: 실패사유}. 한 종목이 죽어도 나머지는 계속."""
    ok: dict[str, dict] = {}
    bad: dict[str, str] = {}

    def one(code: str):
        try:
            rows = fetch_ohlc(code, start, end)
            if not rows:
                return code, None, "일봉 응답 비어 있음"
            ind = indicators(rows)
            if not ind:
                return code, None, f"거래일 부족({len(rows)}일)"
            return code, ind, None
        except Exception as exc:  # noqa: BLE001
            return code, None, f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for code, ind, err in pool.map(one, codes):
            if ind:
                ok[code] = ind
            else:
                bad[code] = err or "알 수 없음"
    return ok, bad


def risk_lists() -> dict[str, set[str]]:
    """관리종목·투자경고/주의 종목코드. 실패하면 빈 집합 — 그 경우 '확인 필요'로 표기한다."""
    def grab(url: str) -> set[str]:
        html = fetch(url, timeout=20).decode("euc-kr", errors="replace")
        return set(re.findall(r"code=(\d{6})", html))

    def build() -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        try:
            out["관리종목"] = sorted(grab(MANAGEMENT_URL))
        except Exception:  # noqa: BLE001
            out["관리종목"] = []
        alerts: set[str] = set()
        for t in ("caution", "warning", "risk"):
            try:
                alerts |= grab(ALERT_URL.format(t=t))
            except Exception:  # noqa: BLE001
                pass
        out["투자경고"] = sorted(alerts)
        return out

    data = cached("risk_lists", ttl_sec=12 * 3600, producer=build)
    return {k: set(v) for k, v in data.items()}


if __name__ == "__main__":
    from datetime import date, timedelta
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=200)).strftime("%Y%m%d")

    risk = risk_lists()
    print(f"관리종목 {len(risk['관리종목'])} · 투자경고류 {len(risk['투자경고'])}\n")

    for name, code in [("오이솔루션", "138080"), ("삼성전자", "005930"),
                       ("한미반도체", "042700"), ("위닉스", "044340")]:
        ind = indicators(fetch_ohlc(code, start, end))
        if not ind:
            print(f"{name}: 데이터 부족")
            continue
        print(f"{name}({code}) {ind['date']} 종가 {ind['close']:,.0f} ({ind['chg_pct']:+.1f}%)")
        print(f"   MA5 {ind['ma5']:,.0f} / MA20 {ind['ma20']:,.0f} / MA60 "
              f"{ind['ma60']:,.0f}" if ind["ma60"] else "   MA60 없음")
        print(f"   정배열={ind['aligned']} 역배열={ind['inverse']} 이격 {ind['ext_ma20']:.2f} "
              f"고점대비 {ind['from_hi20']:+.1f}% 거래대금 {ind['value20_eok']:,.0f}억 "
              f"거래량비 {ind['vol_ratio']}")
