"""아침 리포트 — 3~5줄 요약 텍스트. 상세 근거는 대시보드/DB에 있다."""
from __future__ import annotations

from datetime import date as _date
from pathlib import Path

import boot  # noqa: F401
import viewdata

OUT = Path(__file__).resolve().parent.parent / "out"


def render(view: dict) -> str:
    v = view
    head = f"[장전 브리핑] {v['target_label']} · 생성 {v['generated_at']}"
    body = "\n".join(viewdata.headline(v))

    picks = ""
    if v["picks"]:
        picks = "\n\n[진입 기준 — 실행은 본인]\n" + "\n".join(
            f"  {p['name']}({p['code']}) {p['setup']} | "
            f"진입 {p['entry']:,.0f} / 손절 {p['stop']:,.0f} / "
            f"목표 {p['target1']:,.0f}·{p['target2']:,.0f} / 손익비 {p['rr']}"
            + (f" | ⚠ {p['risk_flags']}" if p["risk_flags"] else "")
            + f"\n    청산: {p['target1']:,.0f} 분할 익절 / {p['stop']:,.0f} 이탈 시 정리"
            for p in v["picks"])

    notes = ""
    if v["notes"]:
        notes = "\n\n[수집 경고]\n" + "\n".join(f"  - {n}" for n in v["notes"])

    tail = (f"\n\n풀 {v['counts']['pool']}종목은 대시보드에서 접힌 채 확인 "
            f"(차트 데이터 없는 {v['counts']['no_chart']}종목은 '차트 확인 필요').\n"
            "매매 추천 아님 · 주문 기능 없음 · 최종 판단과 실행은 본인.")

    return f"{head}\n{'-' * 58}\n{body}{picks}{notes}{tail}\n"


def write(view: dict, out_dir: Path = OUT) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{view['target_date']}.txt"
    path.write_text(render(view), encoding="utf-8")
    (out_dir / "latest.txt").write_text(render(view), encoding="utf-8")
    return path


if __name__ == "__main__":
    v = viewdata.build(_date.today().isoformat())
    print(render(v))
    print(f"저장: {write(v)}")
