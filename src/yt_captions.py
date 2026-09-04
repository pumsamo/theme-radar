"""유튜브 라이브 자막 → 콜 추출용 텍스트 (오동나무경제 종가배팅 등, 2026-09-04 신설).

/watch 스킬은 영어 자막을 고정으로 받아 한국어 종목명이 뭉개지므로, yt-dlp로 한국어 원문
자동자막(ko-orig)만 직접 받아 '[MM:SS] 문장' 텍스트로 만든다. 영상은 내려받지 않는다(토큰·시간 절약).

사용:
  python src/yt_captions.py list                 # 채널 최근 라이브 목록 (길이·제목·id)
  python src/yt_captions.py <video_id|url>       # 자막 → out/captions/<id>.txt + 키워드 밀도표
출력 텍스트는 out/ (gitignore) 에 남는다. 읽을 땐 밀도 높은 구간만 읽을 것.
"""
from __future__ import annotations

import collections
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "captions"
CHANNEL_STREAMS = "https://www.youtube.com/@stock_vault/streams"
CALL_KW = re.compile(r"(일선|손절|종배|종가\s?배팅|매수|지지|저항|목표|양봉|진입|익절|던지|깨지|돌파|눌림|시가)")
TS_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\.\d{3} --> ")
TAG_RE = re.compile(r"<[^>]+>")


def run(*args: str) -> str:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}      # yt-dlp가 cp949로 찍는 것 방지
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    if r.returncode != 0:
        sys.stderr.write(r.stderr[-800:])
    return r.stdout


def list_streams(n: int = 6) -> None:
    print(run("yt-dlp", "--flat-playlist", "--playlist-end", str(n),
              "--print", "%(duration)s s | %(title)s | %(id)s", CHANNEL_STREAMS))


def vtt_to_lines(vtt: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    last, cur = "", None
    for line in vtt.splitlines():
        m = TS_RE.match(line)
        if m:
            h, mi, s = map(int, m.groups())
            cur = h * 3600 + mi * 60 + s
            continue
        if cur is None or not line.strip() or line.startswith(("WEBVTT", "Kind:", "Language:")):
            continue
        text = TAG_RE.sub("", line).replace("&nbsp;", " ").replace("&gt;", ">").strip()
        if not text or text == last:
            continue
        if last and (text in last or last in text):      # 롤링 자막 중복
            if len(text) > len(last):
                out[-1] = (out[-1][0], text)
                last = text
            continue
        out.append((cur, text))
        last = text
    return out


def fetch(video: str) -> None:
    vid = video.rsplit("v=", 1)[-1].rsplit("/", 1)[-1][:11]
    OUT.mkdir(parents=True, exist_ok=True)
    stem = OUT / vid
    run("yt-dlp", "--skip-download", "--write-auto-subs", "--sub-langs", "ko-orig",
        "--sub-format", "vtt", "-o", str(stem), f"https://www.youtube.com/watch?v={vid}")
    vtts = list(OUT.glob(f"{vid}*.vtt"))
    if not vtts:
        raise SystemExit("한국어 자막(ko-orig) 없음 — /watch 스킬(영어)로 대체하거나 포기")
    lines = vtt_to_lines(vtts[0].read_text(encoding="utf-8", errors="replace"))
    txt = stem.with_suffix(".txt")
    txt.write_text("".join(f"[{t // 60:02d}:{t % 60:02d}] {s}\n" for t, s in lines), encoding="utf-8")
    dens: collections.Counter = collections.Counter()
    for t, s in lines:
        dens[t // 300 * 5] += len(CALL_KW.findall(s))
    print(f"{len(lines)}줄 → {txt}")
    print("5분구간 | 매매 키워드 수  (높은 구간만 읽을 것)")
    for b in sorted(dens):
        print(f"{b:3d}~{b + 5:3d}분 | {dens[b]:3d} {'#' * min(dens[b], 40)}")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] == "list":
        list_streams()
    else:
        fetch(sys.argv[1])
