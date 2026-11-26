"""kindcheck.py v1 - does EVERY line of the book say what kind of line it is?

Two uses, one tool: a SAIL log (bash-redirected, so it may carry the CONSOLE's codepage bytes) and a
LIVE diary (`data/gates_shadow_*.jsonl`, always UTF-8 from the Pen). Law 105: a count must name its
unit. So this counts the WHOLE file and asks the names to add up - because counting the named lines can
never reveal the unnamed ones, which is the exact hole 14f exists to close.

  python kindcheck.py <file> [<file2> ...]        exit 0 = every json line named itself; 1 = it did not
"""
import json
import sys
from collections import Counter

NAMES = ("cycle", "verdict", "day", "error", "order")        # the five named classes of a diary line


def scan(path):
    with open(path, "rb") as fh:
        blob = fh.read()
    nonascii = sum(1 for b in blob if b > 127)
    why = ""
    try:
        blob.decode("utf-8")
    except UnicodeDecodeError as exc:
        why = (f"NOT valid utf-8 (first bad byte at {exc.start}) - this file was written through the "
               "console's codepage, so an em dash arrives as 0x97. The house reads logs with "
               "errors=\"ignore\" and so does this tool.")
    txt = blob.decode("utf-8", errors="ignore")
    lines = [x.strip() for x in txt.splitlines() if x.strip()]
    c = Counter()
    for x in lines:
        if x[:1] != "{":
            c["NOT-JSON"] += 1                       # a human print line - real, and a different book
            continue
        try:
            rec = json.loads(x)
        except Exception:
            c["BAD-JSON"] += 1                       # looked like a card, refused to parse: never silent
            continue
        c[str(rec.get("line_kind") or "UNNAMED")] += 1
    return lines, c, nonascii, why


def main(argv):
    if not argv:
        print("usage: python kindcheck.py <log-or-diary> [more...]")
        return 2
    rc = 0
    for path in argv:
        try:
            lines, c, nonascii, why = scan(path)
        except OSError as exc:
            print(f"\n kindcheck | {path}\n   !! cannot read: {type(exc).__name__}: {exc}")
            rc = 1
            continue
        named = {k: v for k, v in c.items() if k in NAMES}
        other = {k: v for k, v in c.items() if k not in NAMES}
        tot = sum(c.values())
        print(f"\n kindcheck | {path}")
        print(f"   lines read     : {len(lines)}   |   bytes above 0x7F: {nonascii}")
        if why:
            print(f"   codepage note  : {why}")
        print("   named classes  : " + (", ".join(f"{k}={named[k]}" for k in NAMES if k in named)
                                        or "(none - is 14f installed?)"))
        print("   other lines    : " + (", ".join(f"{k}={v}" for k, v in sorted(other.items())) or "none"))
        print(f"   the equation   : {tot} lines == " + " + ".join(f"{v} {k}" for k, v in sorted(c.items())))
        if tot != len(lines):
            print("   VERDICT: FAIL - the counter itself lost lines (a bug in THIS file, tell me)")
            rc = 1
        elif other.get("UNNAMED") or other.get("BAD-JSON"):
            print("   VERDICT: FAIL - a json line has no name. Either 14f is not installed, or a writer")
            print("              was found that never learned the word. Paste me this block, that's all.")
            rc = 1
        elif not named:
            print("   VERDICT: n/a - no diary lines here at all (a human-only log?)")
        else:
            print("   VERDICT: PASS - every json line names itself and the counts add up to the file")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
