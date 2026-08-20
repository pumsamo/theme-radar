"""카톡 '대화 내보내기' 텍스트에서 종목 언급을 뽑아 다음날 성적을 잰다.

방법 (텔레그램 채널 채점과 동일한 철학):
  - 종목명이 언급된 날짜 D를 신호로 본다 (사전 콜인지 사후 자랑인지 구분하지 않는다 —
    구분 대신, 판정을 '다음 거래일 시가 매수 → D+1 종가 / D+5 종가'로 통일한다.
    자랑이든 콜이든 "그 말을 듣고 다음날 산 사람"의 성적이 곧 채널의 실용 가치다)
  - 같은 종목이 같은 날 여러 번 언급돼도 1건.
  - 기준선: 같은 날 코스피·코스닥 지수의 같은 방식 수익률.

한계(정직하게): 종목명 부분일치라 오탐 가능(짧은 이름 제외로 완화),
장중/장후 언급을 구분하지 않음, 수수료 미반영.
"""
from __future__ import annotations

import re
import statistics
import sys

import boot  # noqa: F401
import prices_kr
import tickers

DATE_RE = re.compile(r"-+ (\d{4})년 (\d{1,2})월 (\d{1,2})일")
MSG_RE = re.compile(r"^\[([^\]]+)\] \[[^\]]+\] (.*)")

# 일반 단어와 겹쳐 오탐이 많은 이름들
STOP = {"실적", "우리", "한화", "현대", "삼성", "동양", "동일", "국보", "한일", "신성",
        "대상", "무학", "백금", "지엔씨", "서울", "부국", "성신", "일신", "경농"}


def parse(path: str) -> dict[str, set[str]]:
    """{yyyymmdd: {본문 합침}} — 날짜별 전체 텍스트."""
    days: dict[str, list[str]] = {}
    cur = None
    for line in open(path, encoding="utf-8", errors="replace"):
        m = DATE_RE.search(line)
        if m:
            cur = f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"
            continue
        m = MSG_RE.match(line.strip())
        if m and cur:
            days.setdefault(cur, []).append(m.group(2))
        elif cur and line.strip() and not line.startswith("---"):
            days.setdefault(cur, []).append(line.strip())  # 여러 줄 메시지 연속부
    return {d: set(txts) for d, txts in days.items()}


def main() -> None:
    paths = sys.argv[1:]
    if not paths:
        raise SystemExit("사용법: python score_kakao_export.py <내보내기.txt> ...")

    table = tickers.table()
    names = [n for n in table if len(n) >= 3 and n not in STOP]

    # 긴 이름 먼저 매칭해 소거 — '이닉스'가 'SK하이닉스' 안에서 잡히는 오탐 방지.
    # 이름 바로 앞에 한글·영숫자가 붙은 경우('온디바이스')도 제외. 뒤는 조사가 붙으니 허용.
    names.sort(key=len, reverse=True)
    boundary = re.compile(r"[가-힣A-Za-z0-9]")
    mentions: dict[tuple[str, str], None] = {}  # (date, name)
    for p in paths:
        for d, txts in parse(p).items():
            blob = "\n".join(txts)
            for n in names:
                out, hit, i = [], False, 0
                while True:
                    j = blob.find(n, i)
                    if j < 0:
                        out.append(blob[i:])
                        break
                    out.append(blob[i:j])
                    if j == 0 or not boundary.match(blob[j - 1]):
                        hit = True
                        out.append(" " * len(n))  # 소거 — 더 짧은 이름 재매칭 방지
                    else:
                        out.append(n)
                    i = j + len(n)
                blob = "".join(out)
                if hit:
                    mentions[(d, n)] = None
    print(f"종목 언급 {len(mentions)}건 (날짜×종목 기준, 파일 {len(paths)}개)")

    # 시세: 언급 종목만
    need = sorted({n for _, n in mentions})
    bars_by: dict[str, list[dict]] = {}
    for n in need:
        code = table[n]["code"]
        try:
            bars_by[n] = prices_kr.fetch_ohlc(code, "20260601", "20260821")
        except Exception:  # noqa: BLE001
            pass

    def fwd(name: str, d: str):
        bs = bars_by.get(name)
        if not bs:
            return None
        dates = [b["date"] for b in bs]
        # d 다음 거래일
        nxt = next((i for i, dd in enumerate(dates) if dd > d), None)
        if nxt is None or bs[nxt]["open"] <= 0:
            return None
        e = bs[nxt]["open"]
        r1 = (bs[nxt]["close"] / e - 1) * 100
        r5 = (bs[nxt + 4]["close"] / e - 1) * 100 if nxt + 4 < len(bs) else None
        return r1, r5

    r1s, r5s = [], []
    per_day: dict[str, list[float]] = {}
    for d, n in mentions:
        f = fwd(n, d)
        if not f:
            continue
        r1s.append(f[0])
        per_day.setdefault(d, []).append(f[0])
        if f[1] is not None:
            r5s.append(f[1])

    # 기준선: 같은 기간 코스피·코스닥 매일 시가→종가/5일
    base1 = []
    for sym in ("KOSPI", "KOSDAQ"):
        bs = prices_kr.fetch_ohlc(sym, "20260601", "20260821")
        for i in range(1, len(bs)):
            if bs[i]["open"] > 0:
                base1.append((bs[i]["close"] / bs[i]["open"] - 1) * 100)

    print(f"\n판정 가능 {len(r1s)}건")
    if r1s:
        win = sum(1 for r in r1s if r > 0) / len(r1s) * 100
        print(f"  다음날 시가매수→종가: 평균 {statistics.mean(r1s):+.2f}% · 중앙값 "
              f"{statistics.median(r1s):+.2f}% · 승률 {win:.1f}%")
    if r5s:
        print(f"  5일 보유: 평균 {statistics.mean(r5s):+.2f}% · 중앙값 {statistics.median(r5s):+.2f}%")
    if base1:
        print(f"  기준선(지수 당일 시가→종가): 평균 {statistics.mean(base1):+.2f}%")
    print("  ※ 수수료 왕복 0.25%p 미반영 · 언급=콜 가정의 한계 있음")

    # 많이 언급된 종목 상위
    cnt: dict[str, int] = {}
    for _, n in mentions:
        cnt[n] = cnt.get(n, 0) + 1
    top = sorted(cnt.items(), key=lambda kv: -kv[1])[:15]
    print("\n언급 상위:", " · ".join(f"{n}({c})" for n, c in top))


if __name__ == "__main__":
    main()
