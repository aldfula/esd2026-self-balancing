#!/usr/bin/env python3
"""Per-session statistics of the student's prompts (meeting-note 「프롬프트 기록」 tables).

    python3 tools/prompt_stats.py                     # every docs/meeting-notes/*.md except TEMPLATE.md
    python3 tools/prompt_stats.py --csv out.csv       # also write the table

For each session it counts:
  prompts    rows P1..Pn in the 「프롬프트 기록」 table
  short      prompts of <= 5 characters (spaces excluded), or a bare approval:
             an approval phrase from BARE with nothing else in it (no condition,
             no question). "진행. 단 ..." is not bare; "ㅇㅇ 그렇게 해" is
  questions  prompts that contain a question mark
  keywords   prompts that mention 계획 / 근거 / 왜 / 확인 / 조건
  mean_len   mean prompt length in characters (spaces excluded)

The numbers describe how the prompts look, not whether they were good.
Read them next to the 「논의 흐름」 sections. Standard library only.
"""

import argparse
import csv
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOTES = os.path.join(ROOT, "docs", "meeting-notes", "*.md")
KEYWORDS = ("계획", "근거", "왜", "확인", "조건")
BARE = {"ㅇㅇ", "ㅇㅋ", "응", "네", "넵", "예", "ok", "okay", "go", "진행", "진행해", "진행해줘", "진행해 줘", "계속", "계속해",
        "좋아", "좋아요", "그래", "그렇게 해", "그렇게 해줘", "ㅇㅇ 그렇게 해", "알아서 해줘", "알아서 해", "고고", "해줘",
        "다음", "승인", "ㅇㅇ 진행", "그럼 그렇게 해", "그렇게 하자", "응 그렇게 해", "좋아 진행", "ㄱㄱ", "오케이"}
ROW = re.compile(r"^\|\s*P(\d+)\s*\|\s*\[(\d\d:\d\d)\]\s*\|(.*)\|\s*$")


def prompts_in(path):
    """Return [(n, 'HH:MM', text)] from the 프롬프트 기록 table of one note."""
    rows, inside = [], False
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("## "):
                inside = line.startswith("## 프롬프트 기록")
                continue
            if inside:
                m = ROW.match(line.rstrip("\n"))
                if m:
                    text = m.group(3).replace("\\|", "|").strip()
                    rows.append((int(m.group(1)), m.group(2), text))
    return rows


def is_short(text):
    t = text.strip().rstrip(".!~").strip()
    return len(t.replace(" ", "")) <= 5 or t.lower() in BARE


def stats(rows):
    n = len(rows)
    texts = [t for _, _, t in rows]
    short = sum(is_short(t) for t in texts)
    questions = sum(("?" in t or "？" in t) for t in texts)
    keywords = sum(any(k in t for k in KEYWORDS) for t in texts)
    mean_len = sum(len(t.replace(" ", "")) for t in texts) / n if n else 0.0
    return {"prompts": n, "short": short, "short_pct": 100.0 * short / n if n else 0.0,
            "questions": questions, "keywords": keywords, "mean_len": mean_len}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--notes", default=NOTES, help="glob of meeting notes")
    ap.add_argument("--csv", help="write the per-session table here")
    args = ap.parse_args()
    paths = sorted(p for p in glob.glob(args.notes) if os.path.basename(p) != "TEMPLATE.md")
    if not paths:
        print(f"no notes match {args.notes}")
        return 1
    table, total_rows = [], []
    for p in paths:
        rows = prompts_in(p)
        numbers = [n for n, _, _ in rows]
        if numbers != list(range(1, len(rows) + 1)):
            print(f"warning: {os.path.basename(p)} prompt numbers are not 1..{len(rows)}: {numbers}", file=sys.stderr)
        s = stats(rows)
        table.append((os.path.basename(p), s))
        total_rows += rows
    print("| session | prompts | short (<=5 chars or bare approval) | questions | 계획/근거/왜/확인/조건 | mean length |")
    print("|---|---|---|---|---|---|")
    for name, s in table:
        print(f"| {name[:15]} | {s['prompts']} | {s['short']} ({s['short_pct']:.0f} %) | {s['questions']} | "
              f"{s['keywords']} | {s['mean_len']:.1f} |")
    t = stats(total_rows)
    print(f"| all | {t['prompts']} | {t['short']} ({t['short_pct']:.0f} %) | {t['questions']} | {t['keywords']} | {t['mean_len']:.1f} |")
    half = len(table) // 2
    if half == 0:
        return 0
    for label, part in (("first half", table[:half]), ("second half", table[half:])):
        rows = [r for name, _ in part for r in prompts_in(os.path.join(os.path.dirname(paths[0]), name))]
        s = stats(rows)
        print(f"{label} ({part[0][0][:15]}..{part[-1][0][:15]}): prompts {s['prompts']}, short {s['short_pct']:.0f} %, "
              f"questions {s['questions']}, keyword prompts {s['keywords']}, mean length {s['mean_len']:.1f}")
    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["session", "prompts", "short", "short_pct", "questions", "keywords", "mean_len"])
            for name, s in table:
                w.writerow([name, s["prompts"], s["short"], f"{s['short_pct']:.1f}", s["questions"], s["keywords"],
                            f"{s['mean_len']:.1f}"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
