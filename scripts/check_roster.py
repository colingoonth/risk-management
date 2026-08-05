"""Cross-check a chapter roster workbook against a risk tracker workbook.

    uv run python scripts/check_roster.py roster.xlsx tracker.xlsx

Reports names present in one source and not the other. This is what surfaced the
spelling drift that dropped people from eligibility — run it before trusting any
name-keyed join.
"""

import re
import sys
from collections import Counter
from difflib import SequenceMatcher

import openpyxl

if len(sys.argv) < 3:
    raise SystemExit("usage: check_roster.py <roster.xlsx> <tracker.xlsx>")

norm = lambda s: re.sub(r'\s+', ' ', str(s).lower().strip().rstrip(',.`').replace("'", '').replace('.', ''))

wb = openpyxl.load_workbook(sys.argv[1], data_only=True)
ws = wb['Fraternity Roster']
hdr = [c.value for c in ws[1]]
print('HEADERS:', hdr)
print()

rows = []
for r in ws.iter_rows(min_row=2, values_only=True):
    if not any(r):
        continue
    idx, a, b, pc, grad, status, notes = (list(r) + [None] * 7)[:7]
    rows.append(dict(idx=idx, first=a, last=b, pc=pc, grad=grad, status=status, notes=notes))

print(f'ROSTER ROWS: {len(rows)}')
print()
print('=' * 70)
print('A. COMPLETENESS')
print('=' * 70)
for f in ('first', 'last', 'pc', 'grad', 'status', 'notes'):
    missing = sum(1 for r in rows if r[f] in (None, ''))
    print(f'  {f:<8} missing/blank: {missing:>3} / {len(rows)}')
print()
print('  Status values :', dict(Counter(str(r["status"]) for r in rows)))
print('  Grad years    :', dict(sorted(Counter(str(r["grad"]) for r in rows).items())))
print('  PC values     :', dict(Counter(str(r["pc"]) for r in rows)))
nn = [r for r in rows if r['notes']]
print(f'  Notes present : {len(nn)}')
for r in nn:
    print(f'      {r["first"]} {r["last"]}: {r["notes"]!r}')
print()

roster_names = {norm(f'{r["first"]} {r["last"]}'): f'{r["first"]} {r["last"]}' for r in rows}

# duplicates within roster
print('=' * 70)
print('B. DUPLICATES / NEAR-DUPLICATES INSIDE THE ROSTER')
print('=' * 70)
keys = sorted(roster_names)
dupe_found = False
c = Counter(keys)
for k, v in c.items():
    if v > 1:
        print(f'  EXACT DUPLICATE: {k}'); dupe_found = True
for i, a in enumerate(keys):
    for b in keys[i + 1:]:
        if SequenceMatcher(None, a, b).ratio() > 0.87:
            print(f'  near-dupe: {roster_names[a]!r}  ~  {roster_names[b]!r}'); dupe_found = True
first_dupes = {k: v for k, v in Counter(norm(r['first']) for r in rows).items() if v > 1}
print(f'  shared first names (ambiguity risk): '
      f'{ {k: v for k, v in sorted(first_dupes.items())} }')
if not dupe_found:
    print('  no exact/near duplicate full names')
print()

# ---- cross-check vs Spring workers ----
wb2 = openpyxl.load_workbook(sys.argv[2], data_only=True)
ws2 = wb2['RISK SPRING 2026']
hdr2 = {c.column: str(c.value).strip() for c in ws2[6] if c.value}
SLOT = {col for col, n in hdr2.items() if re.match(r'^(RIDES|DOOR|BAR|SETUP|CLEANUP|Juice)', n, re.I)}
NON = {'pledges','pledge','rush','tbd','break','n/a','?','x','-','cancelled','canceled','rush event','pledging start'}
workers = Counter()
for r in range(7, ws2.max_row + 1):
    for col in SLOT:
        v = ws2.cell(row=r, column=col).value
        if v and str(v).strip() and norm(v) not in NON:
            workers[str(v).strip()] += 1
worked_full = {n for n in workers if len(norm(n).split()) >= 2}

def best_match(n, pool):
    bn = norm(n); best, sc = None, 0
    for p in pool:
        s = SequenceMatcher(None, bn, p).ratio()
        if s > sc:
            best, sc = p, s
    return best, sc

print('=' * 70)
print('C. WORKED LAST SPRING BUT NOT ON THE NEW ROSTER')
print('=' * 70)
missing = []
for n in sorted(worked_full):
    if norm(n) in roster_names:
        continue
    m, sc = best_match(n, roster_names)
    if sc > 0.86:
        print(f'  spelling drift : {n!r:<28} -> roster has {roster_names[m]!r}')
    else:
        missing.append((n, workers[n], roster_names.get(m, m), sc))
print()
for n, cnt, m, sc in sorted(missing, key=lambda x: -x[1]):
    print(f'  ABSENT  {n!r:<28} worked {cnt:>2}x   closest roster: {m!r} ({sc:.2f})')
print(f'\n  {len(missing)} people worked last spring and are not on the new roster')
print('  (expected: graduated seniors — but verify none are just missing)')
print()

# ---- 21 tab cross-check ----
ws3 = wb2['21 ']
tw = []
for r in ws3.iter_rows(values_only=True):
    if r and r[0]:
        nm = re.sub(r'[\d\(\)\-,;]', ' ', str(r[0]))
        tw.append(re.sub(r'\s+', ' ', nm).strip())
print('=' * 70)
print('D. 21+ LIST vs ROSTER')
print('=' * 70)
print(f'  names on the 21 tab: {len(tw)}')
gone = []
for n in tw:
    m, sc = best_match(n, roster_names)
    if sc < 0.86:
        gone.append((n, roster_names.get(m, m), sc))
for n, m, sc in gone:
    print(f'  not on roster : {n!r:<28} closest {m!r} ({sc:.2f})')
print(f'  {len(tw) - len(gone)} of {len(tw)} matched to the new roster')
