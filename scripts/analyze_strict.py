"""Forensic analysis of a prior-semester risk tracker workbook.

    uv run python scripts/analyze_strict.py risk-spring-2026.xlsx

Reads the tracker grid and reports what it can and cannot account for: filled
slots vs the tracker's own totals, unattributable first-name-only cells, and
staffing before/after the pledge boundary. Written to establish why the old
spreadsheet could not be trusted; kept for provenance.
"""

import datetime as dt
import re
import statistics
import sys
from collections import Counter
from difflib import SequenceMatcher

import openpyxl

if len(sys.argv) < 2:
    raise SystemExit("usage: analyze_strict.py <tracker.xlsx>")
wb = openpyxl.load_workbook(sys.argv[1], data_only=True)
ws = wb['RISK SPRING 2026']

hdr = {c.column: str(c.value).strip() for c in ws[6] if c.value}
SLOT_COLS = {col: n for col, n in hdr.items()
             if re.match(r'^(RIDES|DOOR|BAR|SETUP|CLEANUP|Juice)', n, re.I)}
fam_of = lambda h: re.sub(r'\s*\d+$', '', h).upper()
NON_PEOPLE = {'pledges','pledge','rush','tbd','rush event','pledging start',
              'break','n/a','?','x','-','cancelled','canceled'}
norm = lambda n: re.sub(r'\s+', ' ', n.lower().strip().rstrip(',.`').replace("'", '').replace('.', ''))

records, cur_date, cur_event = [], None, None
for r in range(7, ws.max_row + 1):
    d = ws.cell(row=r, column=1).value
    if isinstance(d, dt.datetime):
        cur_date, cur_event = d.date(), ws.cell(row=r, column=2).value
    for col, h in SLOT_COLS.items():
        v = ws.cell(row=r, column=col).value
        if v and str(v).strip():
            records.append((cur_date, cur_event, fam_of(h), str(v).strip()))

people = [r for r in records if norm(r[3]) not in NON_PEOPLE]
raw = Counter(r[3] for r in people)

# split: full names (>=2 tokens) vs first-name-only
full = {n for n in raw if len(norm(n).split()) >= 2}
partial = {n for n in raw if len(norm(n).split()) < 2}

# STRICT merge: same first name AND last-name similarity > 0.80
names = sorted(full)
parent = {n: n for n in names}
def find(x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]; x = parent[x]
    return x
for i, a in enumerate(names):
    for b in names[i+1:]:
        na, nb = norm(a).split(), norm(b).split()
        if na[0] != nb[0]:
            continue
        if SequenceMatcher(None, ' '.join(na[1:]), ' '.join(nb[1:])).ratio() > 0.80:
            parent[find(a)] = find(b)
groups = {}
for n in names:
    groups.setdefault(find(n), []).append(n)

print('=' * 74)
print('A. CONFIRMED SPELLING VARIANTS OF THE SAME PERSON (strict: last name matches)')
print('=' * 74)
dupes = [g for g in groups.values() if len(g) > 1]
for g in sorted(dupes, key=lambda g: -sum(raw[x] for x in g)):
    print(f'  [{sum(raw[x] for x in g):>2} shifts]  ' + '  |  '.join(f'{x!r}×{raw[x]}' for x in g))
print(f'\n  {len(dupes)} people were spelled more than one way.')
print(f'  {len(full)} full-name spellings -> {len(groups)} distinct people')

print()
print('=' * 74)
print('B. AMBIGUOUS FIRST-NAME-ONLY ENTRIES (cannot be attributed to a person)')
print('=' * 74)
for n in sorted(partial, key=lambda x: -raw[x]):
    cands = sorted({g[0] for g in groups.values()
                    if norm(g[0]).split()[0] == norm(n)})
    tag = f'could be: {", ".join(cands)}' if len(cands) != 1 else f'-> {cands[0]}'
    if not cands:
        tag = 'no matching full name anywhere in the sheet'
    print(f'  {raw[n]:>2}x  {n!r:<20} {tag}')
print(f'\n  {sum(raw[n] for n in partial)} shifts cannot be reliably credited to anyone.')

# canonical counts, ambiguous excluded
canon = {}
for g in groups.values():
    best = max(g, key=lambda x: (len(x), raw[x]))
    for x in g:
        canon[x] = best
counts = Counter(canon[r[3]] for r in people if r[3] in canon)

print()
print('=' * 74)
print('C. WORKLOAD SPREAD (attributable shifts only)')
print('=' * 74)
v = sorted(counts.values(), reverse=True)
print(f'  distinct people worked : {len(counts)}')
print(f'  attributable shifts    : {sum(v)}   (+{sum(raw[n] for n in partial)} unattributable'
      f', +{sum(1 for r in records if norm(r[3]) in NON_PEOPLE)} placeholder cells)')
print(f'  min / median / max     : {min(v)} / {statistics.median(v)} / {max(v)}')
print(f'  stdev                  : {statistics.pstdev(v):.2f}')
print(f'  worked exactly once    : {sum(1 for x in v if x == 1)} people')
print(f'  worked 6+              : {sum(1 for x in v if x >= 6)} people')
print('  top: ' + ', '.join(f'{n} ({c})' for n, c in counts.most_common(6)))

# temporal clustering — his actual worry
print()
print('=' * 74)
print('D. TEMPORAL CLUSTERING — are one person\'s shifts spread out or bunched?')
print('=' * 74)
PL = dt.date(2026, 2, 20)
by_person = {}
for d, e, f, n in people:
    if n in canon and d:
        by_person.setdefault(canon[n], []).append(d)
worst = []
for p, ds in by_person.items():
    if len(ds) >= 3:
        ds = sorted(ds)
        span = (ds[-1] - ds[0]).days
        pre = sum(1 for x in ds if x < PL)
        worst.append((p, len(ds), pre, len(ds) - pre, span))
worst.sort(key=lambda x: -(x[2] / x[1]))
print(f'  {"person":<26}{"tot":>4}{"pre":>5}{"post":>6}{"span(d)":>9}')
for p, t, pre, post, span in worst[:10]:
    print(f'  {p:<26}{t:>4}{pre:>5}{post:>6}{span:>9}')
print('\n  (pre = shifts before pledging started; those are the ones that would')
print('   have been handed to pledges, leaving that brother under-worked.)')
