"""패턴 자동 탐색 — 이중 검증 필수 (사용자 지시 2026-08-31: "지금도 붙일 수 있잖아").

사람 눈 대신 기계가 조건 조합을 훑는다. 과최적화 방지가 설계의 전부다:
  · 특징 8개를 구간화 → 2~3개 조합을 전수 시험 (시험 개수 공개 = 다중비교 부담 명시)
  · 훈련(전반 1년)에서 상위를 뽑고, 검증(후반 1년)에서 재시험 — 검증 성적만 인정
  · 합격선: 검증 n≥150 AND 검증 평균 R ≥ +0.117(A급) AND 검증 ≥ 훈련의 50%
표준 거래(비교 가능성): 다음날 고가가 당일 고가×1.002 돌파 시 체결, 손절 1.5×ATR14,
목표 +2R, 20일 기한, 동시터치 손절 우선. 거래대금 30억+. 수수료 미반영.
"""
from __future__ import annotations

import itertools
import statistics

import boot  # noqa: F401
import replay

SPLIT = "20250901"  # 훈련 < SPLIT <= 검증
MIN_TRAIN, MIN_VAL = 300, 150
BASE_R = 0.117


def features_and_r(bars):
    """(일자, 특징튜플, 결과R) 리스트 — 결과 R은 표준 거래를 한 번만 계산."""
    out = []
    closes = [b["close"] for b in bars]
    for i in range(70, len(bars) - 22):
        v20 = sum(b["close"] * b["volume"] for b in bars[i - 19:i + 1]) / 20
        if v20 < 30e8:
            continue
        c = closes[i]
        hi60 = max(closes[i - 59:i + 1])
        ma20 = sum(closes[i - 19:i + 1]) / 20
        # RSI14
        g = sum(max(closes[j] - closes[j - 1], 0) for j in range(i - 13, i + 1))
        l = sum(max(closes[j - 1] - closes[j], 0) for j in range(i - 13, i + 1))
        rsi = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
        # 특징 구간화
        fh = (c / hi60 - 1) * 100
        f1 = 0 if fh > -5 else (1 if fh > -15 else (2 if fh > -30 else 3))
        ex = c / ma20
        f2 = 0 if ex < 0.95 else (1 if ex < 1.05 else (2 if ex < 1.20 else 3))
        f3 = 0 if rsi < 45 else (1 if rsi < 60 else (2 if rsi < 75 else 3))
        av = sum(b["volume"] for b in bars[i - 20:i]) / 20
        vr = bars[i]["volume"] / av if av > 0 else 0
        f4 = 0 if vr < 0.7 else (1 if vr < 2 else (2 if vr < 5 else 3))
        streak = 0
        for j in range(i, max(i - 4, 0), -1):
            if closes[j] > bars[j]["open"]:
                streak += 1
            else:
                break
        f5 = min(streak, 3)
        f6 = 1 if c >= hi60 else 0
        box = closes[max(0, i - 126):i]
        f7 = 1 if box and min(box) > 0 and max(box) / min(box) < 1.3 else 0
        r5 = (c / closes[i - 5] - 1) * 100
        f8 = 0 if r5 < -5 else (1 if r5 < 0 else (2 if r5 < 5 else 3))
        # 표준 거래 결과
        trigger = bars[i]["high"] * 1.002
        nxt = bars[i + 1]
        if nxt["high"] < trigger:
            continue  # 미체결 — 패턴 성적에서 제외 (진입 없음)
        entry = max(trigger, nxt["open"])
        trs = [max(bars[j]["high"] - bars[j]["low"],
                   abs(bars[j]["high"] - bars[j - 1]["close"]),
                   abs(bars[j]["low"] - bars[j - 1]["close"]))
               for j in range(i - 13, i + 1)]
        atr = sum(trs) / 14
        stop = entry - 1.5 * atr
        if stop >= entry:
            continue
        target = entry + 2 * (entry - stop)
        r = None
        for b in bars[i + 1:i + 21]:
            if b["low"] <= stop:
                r = -1.0
                break
            if b["high"] >= target:
                r = 2.0
                break
        if r is None:
            r = (bars[min(i + 20, len(bars) - 1)]["close"] - entry) / (entry - stop)
        out.append((bars[i]["date"], (f1, f2, f3, f4, f5, f6, f7, f8), r))
    return out


def main():
    bars_all = replay.load_bars()
    rows = []
    for code, bars in bars_all.items():
        if len(bars) >= 120:
            rows.extend(features_and_r(bars))
    train = [(f, r) for d, f, r in rows if d < SPLIT]
    val = [(f, r) for d, f, r in rows if d >= SPLIT]
    print(f"체결 표본: 훈련 {len(train):,} · 검증 {len(val):,}")
    base_t = statistics.mean(r for _, r in train)
    base_v = statistics.mean(r for _, r in val)
    print(f"무필터 기준: 훈련 {base_t:+.3f}R · 검증 {base_v:+.3f}R (A급 목표 +0.117R)")

    # 조합 생성: 특징 2개 조합 전수 + (후단) 상위 파생 3개 조합
    n_feat, n_bin = 8, 4
    combos = []
    for a, b in itertools.combinations(range(n_feat), 2):
        for va in range(n_bin):
            for vb in range(n_bin):
                combos.append(((a, va), (b, vb)))

    def stats_for(combo, data):
        rs = [r for f, r in data if all(f[k] == v for k, v in combo)]
        return rs

    scored = []
    for combo in combos:
        rs = stats_for(combo, train)
        if len(rs) >= MIN_TRAIN:
            scored.append((statistics.mean(rs), len(rs), combo))
    scored.sort(reverse=True)
    top_pairs = scored[:30]

    # 상위 페어에 특징 1개 추가한 트리플
    triples = []
    seen = set()
    for _, _, combo in top_pairs:
        used = {k for k, _ in combo}
        for c in range(n_feat):
            if c in used:
                continue
            for vc in range(n_bin):
                t = tuple(sorted(combo + ((c, vc),)))
                if t in seen:
                    continue
                seen.add(t)
                rs = stats_for(t, train)
                if len(rs) >= MIN_TRAIN:
                    triples.append((statistics.mean(rs), len(rs), t))
    triples.sort(reverse=True)
    total_tested = len(combos) + len(seen)
    print(f"시험 조합 수: {total_tested:,} (페어 {len(combos)} + 트리플 {len(seen)})")

    names = ["고점比", "이격", "RSI", "거래량비", "연속양봉", "신고가", "좁은박스", "5일수익률"]
    bins = {0: ["-5%내", "0.95미만", "RSI<45", "<0.7배", "0개", "아님", "아님", "<-5%"],
            1: ["-15~-5", "0.95-1.05", "45-60", "0.7-2배", "1개", "신고가", "좁은박스", "-5~0"],
            2: ["-30~-15", "1.05-1.20", "60-75", "2-5배", "2개", "", "", "0~5"],
            3: ["<-30", ">1.20", ">75", ">5배", "3+", "", "", ">5%"]}

    def desc(combo):
        return " & ".join(f"{names[k]}={bins[v][k]}" for k, v in combo)

    print("\n── 이중 검증 결과 (훈련 상위 → 검증 재시험) ──")
    survivors = 0
    for mt, nt, combo in (top_pairs[:15] + triples[:15]):
        rv = stats_for(combo, val)
        if len(rv) < MIN_VAL:
            continue
        mv = statistics.mean(rv)
        ok = mv >= BASE_R and mv >= mt * 0.5
        mark = "★합격" if ok else "  탈락"
        if ok or mt > 0.25:
            print(f"{mark} 훈련 {mt:+.3f}R(n={nt:,}) → 검증 {mv:+.3f}R(n={len(rv):,}) | {desc(combo)}")
        if ok:
            survivors += 1
    print(f"\n합격 패턴: {survivors}개 (합격선: 검증 ≥ +0.117R AND 훈련의 50% 유지)")


if __name__ == "__main__":
    main()
