"""SQLite 저장소. 지시문 6번 슬림 스키마 — 테이블 7개에서 늘리지 않는다."""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "radar.db"

SCHEMA = """
-- 테마 마스터: 미국 테마 ↔ 국내 테마 매핑
CREATE TABLE IF NOT EXISTS themes (
    kr_theme    TEXT PRIMARY KEY,
    us_theme    TEXT,
    track       TEXT NOT NULL DEFAULT 'readacross',  -- readacross | domestic
    keywords    TEXT,                                -- 뉴스 매칭용 키워드(콤마 구분)
    updated_at  TEXT
);

-- 일자별 테마 등장/상승 기록 (모멘텀 = 확인 층)
CREATE TABLE IF NOT EXISTS theme_daily (
    date        TEXT NOT NULL,
    kr_theme    TEXT NOT NULL,
    n_stocks    INTEGER,          -- 그날 이 테마에서 오른 종목 수
    avg_change  REAL,             -- 평균 상승률(%)
    source      TEXT,             -- seed_xlsx | naver_theme
    PRIMARY KEY (date, kr_theme, source)
);

-- 종목 마스터
CREATE TABLE IF NOT EXISTS stocks (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    market      TEXT,
    themes      TEXT,             -- 소속 테마(콤마 구분)
    watchlist   INTEGER NOT NULL DEFAULT 0,
    first_seen  TEXT,
    last_seen   TEXT,
    surge_days  INTEGER,          -- 최근 20거래일 중 +5% 이상 마감한 일수 (확인 층)
    surge_asof  TEXT
);

-- 뉴스/공시에서 추출한 테마 신호 (선행 층)
CREATE TABLE IF NOT EXISTS news_signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    published   TEXT,
    source      TEXT NOT NULL,    -- 한국경제 | 매일경제 | 연합뉴스 | DART | manual
    kr_theme    TEXT,
    direction   TEXT NOT NULL,    -- 호재 | 악재 | 죽은테마
    stocks      TEXT,             -- 본문에서 잡힌 종목명(콤마 구분)
    title       TEXT NOT NULL,
    summary     TEXT,
    url         TEXT,
    weight      REAL NOT NULL DEFAULT 1.0,
    UNIQUE (date, source, title, kr_theme)
);

-- 일자별 후보
CREATE TABLE IF NOT EXISTS candidates (
    date         TEXT NOT NULL,
    code         TEXT NOT NULL,
    name         TEXT NOT NULL,
    kr_theme     TEXT,
    origin       TEXT NOT NULL,   -- readacross | news | momentum
    tier         TEXT NOT NULL,   -- pick | pool
    reason       TEXT,            -- 통과한 필터 한 줄
    setup        TEXT,            -- 눌림목 | 정배열 돌파 | 과열 | 역배열 ...
    entry        REAL,
    stop         REAL,
    target1      REAL,
    target2      REAL,
    rr           REAL,            -- 손익비
    score        REAL,
    risk_flags   TEXT,
    data_status  TEXT NOT NULL,   -- ok | 차트 확인 필요 | 데이터 부재
    PRIMARY KEY (date, code, origin)
);

-- 일자별 글로벌 기준선 스냅샷
CREATE TABLE IF NOT EXISTS global_baseline (
    date        TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    label       TEXT,
    close       REAL,
    change_pct  REAL,
    asof        TEXT,             -- 시세 기준시각(현지 마감)
    source      TEXT,
    PRIMARY KEY (date, symbol)
);

-- 후보 성과 추적 (Phase 3에서 채움 — 스키마만 미리 둔다)
CREATE TABLE IF NOT EXISTS outcomes (
    date        TEXT NOT NULL,
    code        TEXT NOT NULL,
    horizon     INTEGER NOT NULL, -- 3 | 5 | 10 | 20 거래일
    ret_pct     REAL,
    max_gain    REAL,
    max_draw    REAL,
    hit_stop    INTEGER,
    PRIMARY KEY (date, code, horizon)
);

CREATE INDEX IF NOT EXISTS idx_news_date  ON news_signals (date);
CREATE INDEX IF NOT EXISTS idx_cand_date  ON candidates (date);
CREATE INDEX IF NOT EXISTS idx_theme_date ON theme_daily (date);
"""


# 테이블은 7개에서 안 늘린다. 컬럼 추가는 기존 DB에도 반영되게 여기서 처리한다.
_ADD_COLUMNS = {
    "stocks": {"surge_days": "INTEGER", "surge_asof": "TEXT"},
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, cols in _ADD_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


if __name__ == "__main__":
    with connect() as c:
        tables = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print(f"{DB_PATH}\n  tables: {', '.join(tables)}")
