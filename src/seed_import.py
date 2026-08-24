"""수기 정리 엑셀(2026-07-06 ~ 2026-08-07) → 테마 지도 + 모멘텀 히스토리 시드.

지시문 4-4 ②: "지금까지 누적된 데이터로 초기 지도가 이미 상당 부분 구축돼 있으니
이를 시드로 사용하고, 뉴스에서 새 종목이 나오면 지도에 자동 추가·학습."

엑셀 헤더 형식이 7종 섞여 있어(종목코드 유무, 섹터/구분/테마, 상승률 컬럼 2개인 날짜비교본)
컬럼을 이름으로 찾고 없으면 건너뛴다. 파일 하나가 깨져도 나머지는 계속 읽는다.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import openpyxl

import boot  # noqa: F401
import themes_cfg
import tickers
from db import connect
from net import RunLog

SEED_DIR = Path(__file__).resolve().parent.parent.parent  # 무지랭이 폴더
FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_.*\.xlsx$")

NAME_KEYS = ("종목",)
CODE_KEYS = ("종목코드",)
CHG_KEYS = ("상승률",)
REASON_KEYS = ("상승 이유", "상승이유", "비고")
SECTOR_KEYS = ("섹터", "구분", "테마", "관련주")

# 종목명이 아닌데 이름 칸에 들어오는 값들
NOT_A_STOCK = re.compile(r"^(합계|소계|비고|참고|기타|계)$")
# 수기 표시(★ 등)는 종목명이 아니므로 떼어낸다
MARK_PREFIX = re.compile(r"^[\s★☆*・·]+")


def _header_index(row: tuple) -> dict | None:
    """헤더 행이면 컬럼 인덱스 맵을, 아니면 None."""
    cells = [str(c).strip() if c is not None else "" for c in row]
    if not any(c == "종목" for c in cells):
        return None
    idx: dict = {}
    for i, c in enumerate(cells):
        if c in NAME_KEYS and "name" not in idx:
            idx["name"] = i
        elif any(k in c for k in CODE_KEYS):
            idx["code"] = i
        elif any(k in c for k in CHG_KEYS):
            idx["chg"] = i  # 상승률이 2개면 마지막(= 최신일) 것이 남는다
        elif c in REASON_KEYS:
            idx["reason"] = i
        elif c in SECTOR_KEYS:
            idx["sector"] = i
    return idx if "name" in idx else None


def _to_float(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))
    return float(m.group()) if m else None


def parse_workbook(path: Path, log: RunLog) -> list[dict]:
    """한 파일 → [{name, code, chg, theme_raw, reason}]"""
    out: list[dict] = []
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            idx: dict | None = None
            group: str | None = None
            for row in ws.iter_rows(values_only=True):
                if not row:
                    continue
                first = str(row[0]).strip() if row[0] is not None else ""

                if first.startswith(("■", "□", "▪")):
                    group = first
                    continue

                found = _header_index(row)
                if found:
                    idx = found
                    continue
                if idx is None:
                    continue

                raw_name = row[idx["name"]] if idx["name"] < len(row) else None
                if not isinstance(raw_name, str):
                    continue
                name = MARK_PREFIX.sub("", raw_name).strip()
                if not name or NOT_A_STOCK.match(name) or name == "종목":
                    continue

                sector = None
                if "sector" in idx and idx["sector"] < len(row):
                    val = row[idx["sector"]]
                    sector = str(val).strip() if isinstance(val, str) and val.strip() else None

                code = None
                if "code" in idx and idx["code"] < len(row):
                    val = row[idx["code"]]
                    if val is not None:
                        cand = str(val).strip().zfill(6)
                        code = cand if re.fullmatch(r"[0-9A-Z]{6}", cand) else None

                out.append({
                    "name": name,
                    "code": code,
                    "chg": _to_float(row[idx["chg"]]) if "chg" in idx and idx["chg"] < len(row) else None,
                    "theme_raw": group or sector,
                    "reason": (str(row[idx["reason"]]).strip()
                               if "reason" in idx and idx["reason"] < len(row)
                               and isinstance(row[idx["reason"]], str) else None),
                })
    finally:
        wb.close()
    if not out:
        log.warn("seed", f"{path.name}: 데이터 행 0개 — 형식 확인 필요")
    return out


def run(seed_dir: Path = SEED_DIR, log: RunLog | None = None) -> dict:
    log = log or RunLog()
    files = sorted(p for p in seed_dir.glob("*.xlsx")
                   if FILE_RE.match(p.name) and not p.name.startswith("~"))
    if not files:
        log.warn("seed", f"{seed_dir}에 시드 엑셀이 없다")
        return {}

    theme_stocks: dict[str, set[str]] = defaultdict(set)
    theme_day: dict[tuple, list[float]] = defaultdict(list)
    stock_meta: dict[str, dict] = {}
    catalysts: dict[str, set[str]] = defaultdict(set)
    unmapped: set[str] = set()
    n_rows = 0

    for path in files:
        date = FILE_RE.match(path.name).group(1)
        try:
            rows = parse_workbook(path, log)
        except Exception as exc:  # noqa: BLE001 - 파일 하나가 죽어도 계속
            log.warn("seed", f"{path.name} 파싱 실패: {type(exc).__name__}: {exc}")
            continue

        for r in rows:
            n_rows += 1
            name = r["name"]
            code = r["code"] or tickers.to_code(name)
            if not code:
                unmapped.add(name)
            meta = stock_meta.setdefault(name, {"code": code, "first": date, "last": date})
            meta["code"] = meta["code"] or code
            meta["first"] = min(meta["first"], date)
            meta["last"] = max(meta["last"], date)

            theme, catalyst = themes_cfg.resolve(r["theme_raw"]) if r["theme_raw"] else (None, None)
            if not theme:
                continue
            theme_stocks[theme].add(name)
            if catalyst:
                catalysts[theme].add(catalyst)
            if r["chg"] is not None:
                theme_day[(date, theme)].append(r["chg"])

    # ── 지도 보강 (config/map_extra.json) ────────────────────────────────
    # 주 1회 전체 시장 A급 스캔(scan_gaps.py)에서 발견한 '지도 구멍' 종목.
    # 엑셀에 안 실렸을 뿐 업종이 명백한 종목만 사람이 확인하고 넣는다 (2026-08-25 사용자 확정).
    extra_path = Path(__file__).resolve().parent.parent / "config" / "map_extra.json"
    if extra_path.exists():
        extra = json.loads(extra_path.read_text(encoding="utf-8"))
        for theme, names in extra.get("map", {}).items():
            for name in names:
                theme_stocks[theme].add(name)
                stock_meta.setdefault(name, {"code": tickers.to_code(name),
                                             "first": "20260825", "last": "20260825"})

    # ── 저장 ────────────────────────────────────────────────────────────
    with connect() as conn:
        cur = conn.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        cfg_names = themes_cfg.by_name()
        for theme, names in theme_stocks.items():
            cfg = cfg_names.get(theme)
            kw = set(cfg.get("keywords", []) if cfg else [])
            kw |= {c for c in catalysts.get(theme, set()) if len(c) <= 20}
            cur.execute(
                """INSERT INTO themes (kr_theme, us_theme, track, keywords, updated_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(kr_theme) DO UPDATE SET
                     keywords=excluded.keywords, updated_at=excluded.updated_at""",
                (theme,
                 cfg.get("us_theme") if cfg else None,
                 cfg.get("track") if cfg else "domestic",
                 ",".join(sorted(kw)) or None,
                 now))

        # config에만 있고 시드에 안 나온 테마도 마스터에 넣어둔다
        for t in themes_cfg.themes():
            cur.execute(
                """INSERT OR IGNORE INTO themes (kr_theme, us_theme, track, keywords, updated_at)
                   VALUES (?,?,?,?,?)""",
                (t["kr_theme"], t.get("us_theme"), t.get("track", "domestic"),
                 ",".join(t.get("keywords", [])) or None, now))

        stock_themes: dict[str, set[str]] = defaultdict(set)
        for theme, names in theme_stocks.items():
            for n in names:
                stock_themes[n].add(theme)
        # config에 손으로 적은 종목도 지도에 합류시킨다
        for t in themes_cfg.themes():
            for n in t.get("kr_stocks", []):
                stock_themes[n].add(t["kr_theme"])
                stock_meta.setdefault(n, {"code": tickers.to_code(n), "first": None, "last": None})
                if not stock_meta[n]["code"]:
                    stock_meta[n]["code"] = tickers.to_code(n)
                    if not stock_meta[n]["code"]:
                        unmapped.add(n)

        saved = 0
        for name, meta in stock_meta.items():
            if not meta["code"]:
                continue  # 코드 없으면 시세 조회가 불가 → 지도에서 제외하고 아래에 사유 남김
            cur.execute(
                """INSERT INTO stocks (code, name, market, themes, watchlist, first_seen, last_seen)
                   VALUES (?,?,?,?,0,?,?)
                   ON CONFLICT(code) DO UPDATE SET
                     name=excluded.name,
                     themes=excluded.themes,
                     first_seen=MIN(IFNULL(stocks.first_seen,excluded.first_seen),
                                    IFNULL(excluded.first_seen,stocks.first_seen)),
                     last_seen=MAX(IFNULL(stocks.last_seen,excluded.last_seen),
                                   IFNULL(excluded.last_seen,stocks.last_seen))""",
                (meta["code"], name, tickers.market_of(meta["code"]),
                 ",".join(sorted(stock_themes.get(name, set()))) or None,
                 meta["first"], meta["last"]))
            saved += 1

        # 관심리스트 반영 (지시문 4-5: 관심 vs 신규 후보 구분)
        watch_path = Path(__file__).resolve().parent.parent / "config" / "watchlist.json"
        watch_missing: list[str] = []
        try:
            import json as _json
            watch = _json.loads(watch_path.read_text(encoding="utf-8")).get("stocks", [])
        except Exception as exc:  # noqa: BLE001
            log.warn("seed", f"watchlist.json 읽기 실패: {exc}")
            watch = []
        cur.execute("UPDATE stocks SET watchlist=0")
        for n in watch:
            code = tickers.to_code(n)
            if not code:
                watch_missing.append(n)
                continue
            cur.execute(
                """INSERT INTO stocks (code, name, market, watchlist) VALUES (?,?,?,1)
                   ON CONFLICT(code) DO UPDATE SET watchlist=1""",
                (code, n, tickers.market_of(code)))
        if watch_missing:
            log.warn("seed", "관심리스트 종목코드 미매칭: " + ", ".join(watch_missing))

        for (date, theme), changes in theme_day.items():
            cur.execute(
                """INSERT OR REPLACE INTO theme_daily
                   (date, kr_theme, n_stocks, avg_change, source) VALUES (?,?,?,?,'seed_xlsx')""",
                (date, theme, len(changes), round(sum(changes) / len(changes), 2)))
        conn.commit()

    if unmapped:
        log.warn("seed", f"종목코드 미매칭 {len(unmapped)}건 (상장폐지·비상장·표기차이 가능): "
                         + ", ".join(sorted(unmapped)[:15])
                         + ("…" if len(unmapped) > 15 else ""))

    summary = {
        "files": len(files), "rows": n_rows, "stocks": saved,
        "themes": len(theme_stocks), "theme_days": len(theme_day),
        "unmapped": sorted(unmapped),
    }
    log.ok("seed", f"파일 {summary['files']} · 행 {summary['rows']} · "
                   f"종목 {summary['stocks']} · 테마 {summary['themes']}")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=SEED_DIR)
    args = ap.parse_args()

    log = RunLog()
    s = run(args.dir, log)

    with connect() as conn:
        print("\n테마별 시드 종목 수 (상위 20)")
        for r in conn.execute("""
            SELECT kr_theme, COUNT(*) n FROM (
              SELECT TRIM(value) kr_theme FROM stocks, json_each('["'||REPLACE(themes,',','","')||'"]')
              WHERE themes IS NOT NULL)
            GROUP BY kr_theme ORDER BY n DESC LIMIT 20"""):
            print(f"  {r['kr_theme']:<18} {r['n']:>3}")
