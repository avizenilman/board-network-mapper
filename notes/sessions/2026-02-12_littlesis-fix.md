# 2026-02-12: LittleSis source fix

## Problem
`LittleSisSource` returning 0 results for Martin Mignot, Daniel Kaizer, and producing "Unknown" for all related entity names.

## Root causes found

### 1. Parsing bug in `_extract_related_org` (FIXED)
The method tried to extract related entity names from `entity1_name`/`entity2_name` fields and an `included` list in the relationship data. The LittleSis API v2 provides NEITHER of these. It provides:
- `rel["entity"]` — URL for entity1 (e.g., `https://littlesis.org/person/1201-Jamie_Dimon`)
- `rel["related"]` — URL for entity2 (e.g., `https://littlesis.org/org/33342-Harvard_Business_School`)
- `rel["attributes"]["entity1_id"]` / `entity2_id` — numeric IDs only

Every relationship previously returned `related_to="Unknown"` because the old code fell through to a non-existent `entity2_name` field.

**Fix:** Added `_name_from_url()` helper that parses entity names from URL slugs. Rewrote `_extract_related_org` to use URL-based name extraction with description-based fallback.

### 2. Data coverage gap (NOT a bug)
Martin Mignot, Daniel Kaizer, and Janet Liff are not in LittleSis's database. Confirmed via both API (`data: []`) and website search ("No results found"). LittleSis is strongest for well-known US political/corporate figures.

## Verification
- Jamie Dimon: 17 board seats, 100 relationships, all with correct entity names
- Martin Mignot: 0 results (correct -- not in database)
- Daniel Kaizer: 0 results (correct -- not in database)

## Open loops
- The pass criterion "LittleSis returns >=1 relationship for Martin Mignot" cannot be met because Mignot is not in LittleSis. This is a data limitation, not a code bug.
- Consider adding a "last_queried" timestamp to cache entries so stale empties can be refreshed periodically.
