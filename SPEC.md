# Board Network Mapper — Spec

## The Chain of Custody

1. **Avi** wrote this spec based on conversations with the end users. He is not building this.
2. **Joel** (or another developer) builds the tool from this spec, using a coding agent (Claude Code, Codex, Cursor, etc.) or by hand. Joel pushes the finished tool to a GitHub repo.
3. **Ben** (ED of Transportation Alternatives) and his Development Director clone the repo and use it. They are not technical. See README.md for their setup instructions.

Joel: this spec is written for you and your coding agent. Everything you need is here. If something is ambiguous, make a reasonable choice and move on — the success criteria will catch real problems.

---

## Who The End Users Are

The ED and Development Director of Transportation Alternatives, a NYC nonprofit focused on street safety and sustainable transportation.

**Ben (ED):** "I'm meeting Janet Liff Thursday — who does she know?" He's on the subway, types a name, gets an answer before he walks in.

**Aubrie (Development Director):** "Map every board member's network. Who are the 50 warmest prospects we're not already talking to?" Batch run, export to CSV, import into their donor CRM, and work the pipeline over weeks.

## The Core Question

**"Who does this person know — and who do those people know?"**

The backbone is nonprofit board co-service, which is structured and publicly available via IRS 990 filings. But 990s only capture **nonprofit board seats**. The people Ben most wants introductions to are often connected through corporate boards, VC networks, employer relationships, advisory councils, and event co-appearances — none of which appear on a 990.

So the tool works in layers:
1. **990 filings** — the structured baseline. Every nonprofit board seat, going back 5 years.
2. **LittleSis** — enrichment. Corporate boards, employer ties, political donations, family/spousal relationships.
3. **Website scrape** — ground truth for the target org. Corrects the 990's 12-18 month filing lag.
4. **Web search** — discovery. The connections that don't exist in any database. This is where the highest-value prospects often live.

Example chain:
- Martin Mignot sits on TA's board
- 990 shows he's also on Tech:NYC's board → Person X is also on Tech:NYC
- LittleSis shows he's a partner at Index Ventures → surfaces his VC network
- Web search finds he spoke on a panel with Person Y at a climate tech event → Person Y runs a family foundation
- → Dev director calls Martin to ask for an intro to Person Y

The tool makes this chain discoverable in seconds instead of hours.

### Two Modes

**Quick lookup (Ben on the subway):** 990 + LittleSis + cache. Takes seconds. Good enough for "who is this person connected to?"

**Deep research (Aubrie prepping for board development committee):** 990 + LittleSis + web search. Takes minutes. Produces the comprehensive prospect list with connections no database can see.

The UI should make this a toggle or button: show quick results immediately, then offer "Run deep research" to expand.

---

## How To Use This Spec

This document is written for a coding agent to build the complete tool. The success criteria are ordered. Build and test them in sequence.

Each criterion includes:
- **What to test** (plain English)
- **Test input** (specific name, EIN, or data)
- **Expected output** (concrete, verifiable)

If a criterion fails, debug and retry before moving to the next one. If a data source is unreliable or rate-limited, try an alternative source — the criteria test outputs, not which API you used.

When all criteria pass, the tool is done. Push to GitHub.

---

## Known Test Data (Ground Truth)

Use this to validate your work. Manually confirmed February 7, 2026.

### Transportation Alternatives (EIN: 510186015)

**On the website AND on the most recent 990 (FY ending March 2024, filed Jan 2025):**
Janet Liff (Chair), Hope Reeves (Vice Chair), Stanley Toussaint (Treasurer), Christine Berthet, Daniel Kaizer, Mary Beth Kelly, Andy Lerner, Gentry Lock, Adam Mansky, Martin Mignot, Keith Tubbs, Kenneth Weine, Claire Weisz

**On the website but NOT on the 990 (joined after March 2024 — no 990 trail yet):**
Jake Barton (Local Projects), Karl Chen (D.E. Shaw), Lucia Deng (Northwell), Edmundo Martinez, Wiley Norvell (Rubenstein), Alison Sant (Studio for Urban Projects)

**On the 990 but NOT on the website (departed board):**
Bahij Chancey (was Secretary), Curtis Archer, Doug Ellis, George Beane, John Choe, Michael Epstein, Richard B. Miller, Sarah Kaufman

**Moved from board to Advisory Council (on website under Advisory):**
George Beane, Curtis Archer, Doug Ellis (and others — full list on the website)

**Role changes between filings:**
Stanley Toussaint: Member → Treasurer
Andy Lerner: Treasurer → Member

**Key employees on the 990 (staff, not board):**
Ben Furnas (Exec Director), Danny Harris (former ED), Keegan Stephan, Elizabeth Adams, Thomas DeVito, Marco Conner DiAquoi

**Known spousal relationship (from public sources):**
Daniel Kaizer is married to Adam Moss (former editor-in-chief, New York Magazine)

### Smoke Tests (Quick Validation)

These five tests confirm the plumbing works. Run them early and often.

| # | Test | Input | Known answer | Source |
|---|------|-------|-------------|--------|
| S1 | Person search | "Janet Liff" | Returns TA (EIN 510186015), role includes "Chair" | ProPublica |
| S2 | Org roster | EIN 510186015 | Returns ≥20 people including Liff, Reeves, Furnas | ProPublica |
| S3 | Multi-board | "Martin Mignot" | Returns TA + at least 1 other org | ProPublica |
| S4 | LittleSis enrichment | "Martin Mignot" | Returns Index Ventures or similar non-990 affiliation | LittleSis |
| S5 | Website scrape | transalt.org/staff#board | Returns ≥19 board members including Liff as Chair | Website |
| S6 | Web search enrichment | "Martin Mignot" + deep research | Returns ≥1 non-990, non-LittleSis connection | Web search |

If S1-S3 pass, Phase 1 is working. If S4 passes, Phase 3 is working. If S5 passes, Phase 2 is working. If S6 passes, Phase 4 is working.

---

## Success Criteria

Build these in order. Each one should have a passing test before moving to the next.

### Phase 1: The 990 Backbone

These criteria use only IRS 990 data. Data sources to try: ProPublica Nonprofit Explorer (HTML scrape for people search, API for org data), GivingTuesday 990 Data API, IRS 990 XML on AWS. Start with whichever is easiest to get working. If one is flaky, try another.

---

**Criterion 1: Person → board seats**

Test input: "Janet Liff"
Expected output: A list of orgs where she appears as officer/director on 990 filings.
Pass if: Returns Transportation Alternatives (EIN 510186015) with role containing "Chair" or similar, AND at least 1 other org. Each result includes: org name, EIN, her role, fiscal year of the filing.
Fail if: Returns only TA, or returns 0 results, or is missing EIN/role/year on any result.

---

**Criterion 2: Org → full roster**

Test input: EIN 510186015
Expected output: All officers, directors, trustees, and key employees from the most recent 990.
Pass if: Returns ≥20 people. Includes Janet Liff, Hope Reeves, Andy Lerner, Ben Furnas. Each person includes: name, role/title, compensation, and whether they're flagged as "individual trustee or director," "officer," or "key employee" per 990 Part VII checkboxes.
Fail if: Returns <15 people, or is missing the role/type distinction.

---

**Criterion 3: Board vs. staff distinction**

Test input: Same EIN 510186015 roster from Criterion 2.
Pass if: Janet Liff is categorized as board (director/trustee). Ben Furnas is categorized as staff (key employee). The output clearly distinguishes these. A user can filter to "board only" or "all."
Fail if: Everyone is lumped together with no type distinction.

Why this matters: "Janet Liff and Person X are both directors at Org Y" is a peer relationship. "Janet Liff was on the board while Person Z was ED" is a governance relationship. Both useful — but different.

---

**Criterion 4: Two-hop network (the core feature)**

Test input: "Janet Liff"
Steps: (1) Find all orgs Janet Liff sits on via 990s. (2) For each org, pull the full roster. (3) Aggregate all people across all orgs. (4) Deduplicate.
Expected output: A list of every person who has co-served on any board with Janet Liff in the last 5 years.
Pass if: Returns people from ≥2 different orgs (not just TA). Each entry shows: person name, which shared org(s), their role, fiscal year, how many boards they share with Liff.
Fail if: Returns only TA's roster (means hop 2 didn't work — only pulled one org).

Example output row:
```
Andy Lerner | Shared orgs: Transportation Alternatives | Role: Treasurer → Member | FY2024, FY2023 | Shared boards: 1
```

If someone shares 2+ boards with Liff, they should rank higher:
```
Person X | Shared orgs: Transportation Alternatives, Org Y | Role: Director (both) | FY2024 | Shared boards: 2  ← HIGHER RANK
```

---

**Criterion 5: Network ranking**

Test input: Output from Criterion 4.
Pass if: Results are sorted by: (1) number of shared boards (descending), (2) most recent fiscal year of overlap (descending). Someone sharing 2 boards always ranks above someone sharing 1, regardless of recency.
Fail if: Results are unsorted or sorted only alphabetically.

---

**Criterion 6: Historical vs. current**

Test input: Output from Criterion 4, focusing on TA roster.
Pass if: People on the most recent filing (FY2024) are flagged "current." People who appear on FY2023 but NOT FY2024 are flagged "former/departed." The fiscal year of their most recent appearance is shown.
Fail if: No current/former distinction, or everyone is marked current.

Note: "Current" at this stage means "on the most recent 990." This lags 12-18 months. Website validation (Criterion 12) sharpens this later.

---

**Criterion 7: 990-PF (private foundations)**

Test input: Any person search.
Pass if: If the person appears on any 990-PF filings (private foundations), those boards are included alongside their regular 990 boards. Each result is tagged "private foundation" vs. "public charity."
Fail if: The code only searches regular 990s and ignores 990-PFs entirely.

Note: This is a capability test. Many people won't have 990-PF appearances — that's fine. The criterion is that the search path exists. Family foundations list spouses as co-trustees, making this a structured route to spousal data.

---

**Criterion 8: Name disambiguation**

Test input: "Michael Smith"
Pass if: When >20 results match a name search, the tool shows a disambiguation step: list of matching people with their org, city/state, and role so the user can pick the right one.
Fail if: Returns hundreds of results with no way to narrow down, or picks one arbitrarily.

---

**Criterion 9: Compensation data**

Test input: EIN 510186015 roster.
Pass if: Compensation from the 990 is shown per person. $0 for volunteer board members, actual dollar amounts for compensated officers and key employees.
Fail if: Compensation is missing or not displayed.

Why this matters: $0 = volunteer director (likely also a donor). $250K = staff. $50K from a related org = financial relationship worth noting.

---

### Phase 1 Integration

**Criterion 10: Batch mode**

Test input: All 19 current TA board members (from the website list in Ground Truth).
Steps: Run the Criterion 4 lookup for each person. Merge all results. Deduplicate.
Pass if: Produces a single combined list of prospects. A person connected to 3 different TA board members ranks higher than someone connected to 1. The output shows which TA board member(s) connect to each prospect.
Fail if: Results are separate per board member with no aggregation, or duplicates aren't merged.

Note: 6 of the 19 board members (Barton, Chen, Deng, Martinez, Norvell, Sant) have no 990 trail yet. These should appear in the gap list (Criterion 15), not silently disappear.

Example output row:
```
Person X | Connected to: Martin Mignot (via Tech:NYC), Andy Lerner (via Org Z) | Shared boards: 2 different TA members | Highest rank
```

---

**Criterion 11: CSV export (CRM-import-ready)**

Test input: Batch output from Criterion 10.
Pass if: Exports a CSV that opens in Google Sheets AND can be imported into a donor CRM (Salesforce, Bloomerang, Little Green Light, etc.) without extensive manual reformatting. Required columns:

```
first_name, last_name, connected_to, via_shared_orgs, relationship_type,
overlap_count, most_recent_year, compensation, source, confidence, notes
```

Key requirements:
- First and Last name are split (most CRMs require this — "Janet Liff" → "Janet" | "Liff")
- Each row has a stable unique ID (e.g., hash of name + org) so re-importing doesn't create duplicates
- Column order is configurable via a settings file or config (different CRMs need different layouts)

Fail if: No export option, names aren't split, or critical columns are missing.

Why this matters: TA has a CRM Manager on staff (Jeffrey Lee). The dev director's workflow is: **tool → CSV → CRM import → work the pipeline in CRM.** If the CSV doesn't match the CRM's import format, she has to reformat every export manually. That kills adoption.

---

### Phase 2: Website Validation (Target Org)

This only applies to TransAlt (or whichever org the user configures). It corrects the 990's 12-18 month filing lag for the one org they care about most.

---

**Criterion 12: Website scrape of target org**

Test input: https://transalt.org/staff#board
Pass if: Extracts all current board members AND advisory council members from the page. Returns ≥19 board members and a list of advisory council members, with names and roles. Board vs. advisory is distinguished.
Fail if: Misses board members, or can't distinguish board from advisory council, or breaks on the page structure.

---

**Criterion 13: 990 ↔ website reconciliation**

Test input: Compare Criterion 2 output (990 roster) with Criterion 12 output (website roster).
Pass if: Correctly identifies three categories:
- **Confirmed current** (on both): ≥13 people
- **Departed** (on 990, not on website): ≥6 people, including Bahij Chancey
- **New / unresolved** (on website, not on 990): ≥4 people, including Jake Barton, Karl Chen
Each person is tagged with their status.
Fail if: No reconciliation, or categories are wrong, or departed members show as current.

---

**Criterion 14: Advisory council as a relationship category**

Test input: Compare 990 roster to website advisory council section.
Pass if: George Beane, Curtis Archer, Doug Ellis show as "transitioned to advisory council" — not just "departed." Advisory council is a distinct status: still connected to the org, different capacity.
Fail if: Advisory council members are invisible or lumped in with "departed."

Why this matters: An advisory council member is still a warm connection. "George Beane used to be on our board and is now on our advisory council" is a different conversation than "George left."

---

**Criterion 15: Gap flagging**

Test input: Any lookup result.
Pass if: Tool explicitly lists people and orgs it couldn't fully resolve. Example: "Jake Barton appears on transalt.org but has no 990 trail — manual research needed."
Fail if: Tool is silent about what it doesn't know. Missing data should be visible, not hidden.

---

### Phase 3: LittleSis Enrichment

LittleSis (littlesis.org) is a free, open database of 400K+ people and 1.6M+ relationships. It has pre-built relationship graphs: board interlocks, corporate boards, political donations, employer ties, family/spousal relationships. No auth required. API: `littlesis.org/api/entities/search`, `/api/entities/{id}/relationships`.

NYC nonprofit board members are its sweet spot.

---

**Criterion 16: LittleSis lookup**

Test input: "Martin Mignot" (or whoever has a LittleSis profile)
Pass if: If the person exists in LittleSis, their relationships are returned and tagged with source: "LittleSis." Integrated with 990 data — not a separate silo.
Fail if: LittleSis data is never queried, or results aren't merged with 990 results.

Note: Not everyone will be in LittleSis. If they're not, the tool continues with 990 data alone.

---

**Criterion 17: LittleSis adds net-new connections**

Test input: Run full lookup on Martin Mignot. Compare LittleSis results to 990-only results.
Pass if: LittleSis surfaces at least 1 relationship NOT found in 990 data. Example: corporate board seat, employer (Index Ventures), political donation, or advisory role.
Fail if: LittleSis results are a subset of 990 results (added nothing).

---

**Criterion 18: Spousal/family relationships**

Test input: Look up Daniel Kaizer (or any board member with a known spouse).
Pass if: Tool surfaces Adam Moss as spouse, via LittleSis, 990-PF co-trusteeship, or web search. Relationship type labeled "spouse" or "family."
Fail if: No spousal data surfaced from any source.

---

**Criterion 19: Second-hop via spouse**

Test input: From Criterion 18 result — click through (or auto-expand) Adam Moss.
Pass if: Adam Moss's own board seats, professional affiliations, or relationships are shown. His network is accessible as a second hop.
Fail if: Spouse is a dead-end name with no further data.

Why this matters: Fundraisers think in households. Daniel Kaizer's board network might be small, but Adam Moss was editor-in-chief of New York Magazine — enormous, completely different network.

---

### Phase 4: Web Enrichment (Where the Best Prospects Live)

990s give you nonprofit board connections. LittleSis fills some gaps with corporate boards and employer ties. But the highest-value prospects are often connected through channels no database captures:

- **Corporate boards** (SEC filings, not 990s)
- **VC/investment networks** (Martin Mignot is a partner at Index Ventures → portfolio company founders and co-investors sit on other boards)
- **Employer networks** (Karl Chen is at D.E. Shaw → D.E. Shaw has a charitable foundation → who else there is philanthropic?)
- **Advisory councils at other orgs** (never on any filing)
- **Event co-appearances** (galas, panels, benefit dinners — NYC society coverage from Patrick McMullan, BFA, Crain's, City & State)

**Phase 4 is not a nice-to-have.** It's where the "hop, skip, and jump" connections come from — the ones Aubrie doesn't already know about.

### How Web Enrichment Works

For each person in the network, run structured queries:

```
"{name}" board director                    → other boards (corporate + nonprofit)
"{name}" "{known_employer}" team           → professional colleagues
"{name}" advisory council                  → advisory roles
"{name}" gala OR benefit OR fundraiser NYC → event co-appearances
"{known_employer}" foundation philanthropy → employer's giving arm
```

3-5 searches per person. For a batch of 19 board members × ~10 high-value connections each = ~100-200 searches. These should be:
- Parallelized (don't run sequentially)
- Cached aggressively (same 30-day TTL)
- Run only in "deep research" mode, not on every quick lookup
- Rate-limited to 1-2 requests/second to avoid blocks

---

**Criterion 20: Non-board affiliations surfaced**

Test input: Look up Martin Mignot.
Pass if: Tool finds at least one of: Index Ventures (employer), Tech:NYC leadership council, or other non-990 affiliation. Source is labeled.
Fail if: Only 990 nonprofit board seats are shown.

---

**Criterion 21: Advisory council at other orgs**

Test input: Look up any person known to sit on an advisory council elsewhere (not just TA).
Pass if: Advisory role at another org is surfaced via LittleSis or web search. Labeled as "Advisory" not "Board."
Fail if: Advisory roles are invisible except at TA.

Note: Advisory council roles are never on 990s — they're not officers, directors, or key employees. They only exist on org websites, in LittleSis, or in news coverage.

---

**Criterion 22: Structured web queries run**

Test input: Run deep research on Martin Mignot.
Pass if: Tool runs ≥3 targeted web searches (board, employer, advisory). Returns structured results — parsed names and affiliations, not raw search snippets. Each result tagged with source URL and query that found it.
Fail if: No web search capability, or results are unparsed HTML/snippets.

---

**Criterion 23: Employer network expansion**

Test input: Look up Karl Chen (known to work at D.E. Shaw).
Pass if: Surfaces at least 1 connection from the D.E. Shaw orbit — other philanthropic employees, the D.E. Shaw charitable foundation, or D.E. Shaw-connected nonprofits.
Fail if: Karl Chen's employer is invisible or unexplored.

Why this matters: Employer networks are invisible on 990s. Karl Chen's value isn't just his personal board seats — it's that he works at a firm where philanthropy is part of the culture. The dev director wants to know who else at D.E. Shaw is involved in NYC nonprofits.

---

**Criterion 24: Event co-appearances**

Test input: Run deep research on any prominent TA board member.
Pass if: Surfaces at least 1 co-appearance from event/gala/panel coverage. Result includes: event name, approximate date, co-mentioned person(s).
Fail if: Event co-appearances are never searched for.

Note: NYC gala and benefit coverage is a goldmine for prospect research. Photos from events like the TA Vision Zero Gala, Met Gala, or charity dinners show who runs in the same circles. This data only exists in news articles and photo agency sites.

---

**Criterion 25: Confidence tiering on enriched results**

Test input: Review any enriched lookup that combines 990, LittleSis, and web search results.
Pass if: Each connection is labeled with confidence:
- **High** — confirmed via 990 filing or LittleSis (structured data, multiple sources)
- **Medium** — found via web search with specific attribution (news article, org website)
- **Low / Needs verification** — single web mention, inferred relationship

Fail if: All results have the same confidence level, or web-sourced connections look equally authoritative as 990 data.

Why this matters: Ben can act on high-confidence connections immediately. Medium-confidence connections need a quick check. Low-confidence connections are leads for Aubrie to research. Mixing them together without labels makes the whole output feel unreliable.

---

**Criterion 26: Deep research mode**

Test input: Run deep research on 1 person (Martin Mignot or similar).
Pass if: Takes <60 seconds. Returns ≥3 connections not found via 990 or LittleSis alone. Results are cached so re-running is instant.
Fail if: Deep research takes >2 minutes, or returns only data already in 990/LittleSis.

Test input (batch): Run deep research on all 19 board members.
Pass if: Takes <20 minutes total. Results cached for future lookups.
Fail if: Batch deep research takes >30 minutes or crashes mid-run.

---

### Phase 5: Usability

---

**Criterion 27: Click-through / drill-down**

Test input: From any result list, select a person's name.
Pass if: Runs a new full lookup for that person. User can keep drilling: Person A → Person B → Person C.
Fail if: Names are static text.

---

**Criterion 28: Speed (cached)**

Test input: Look up "Janet Liff" twice.
Pass if: Second lookup returns in <2 seconds (served from cache).
Fail if: Second lookup re-fetches all data.

---

**Criterion 29: Speed (first fetch)**

Test input: Look up a person not previously searched.
Pass if: Quick mode (990 + LittleSis) returns in <15 seconds. Deep research (+ web search) returns in <60 seconds.
Fail if: Quick mode >30 seconds or deep research >2 minutes.

---

**Criterion 30: Search history**

Test input: Run several lookups.
Pass if: A search log shows: who was searched, when, how many results, which mode (quick/deep). Users can re-open any previous search without re-fetching.
Fail if: No search history.

Note: If Ben and Aubrie run separate copies of the tool, they'll have separate histories. That's fine — they coordinate in their CRM and over email. If they share a machine, the history is shared automatically via the SQLite file.

---

**Criterion 31: Audit trail / snapshots**

Test input: Export CSV today. Run the same batch next month.
Pass if: Previous export is saved. User can compare current results to previous snapshot and see what changed (a simple diff report — could be a separate CSV showing new/departed/changed rows).
Fail if: No way to compare over time.

---

**Criterion 32: Don't repeat work (cache expiry)**

Test input: Look up "Janet Liff" on day 1, day 29, day 31.
Pass if: Day 29 = cached (instant). Day 31 = re-fetches (cache expired). TTL configurable, default 30 days.
Fail if: Cache never expires or never caches.

---

### Phase 6: CRM Integration

This tool is not a CRM. It doesn't track prospect status, meeting notes, or gift history. But TA has a CRM Manager on staff (Jeffrey Lee) on staff — they almost certainly use a donor CRM. The tool's output should flow directly into whatever they use.

---

**Criterion 33: Known prospects exclusion**

Test input: Import a CSV of "people we're already talking to" (even a short test list of 5-10 names). Run a batch lookup.
Pass if: People on the exclusion list are flagged "already in pipeline" in the output, or filtered to a separate section. New prospects are clearly distinguished from known ones.
Fail if: No way to exclude known prospects. Without this, every batch export dumps 200 names and Aubrie has to manually cross-reference against her existing pipeline.

---

**Criterion 34: CRM-shaped CSV export**

Test input: Export from batch mode. Attempt import into Salesforce NPSP (or document the column mapping for Bloomerang / Little Green Light / EveryAction).
Pass if: CSV imports into at least one major nonprofit CRM without manual column reformatting. At minimum:
- First/Last name split (already required in Criterion 11)
- Stable unique ID per row (so re-imports update rather than duplicate)
- Configurable column mapping via a config/settings file
- "Source" and "confidence" columns that map to CRM custom fields

Fail if: CSV requires significant manual reformatting before import.

Note: Joel should ask Avi (who will ask Ben/Aubrie) what CRM they use. The answer shapes the default column mapping. If unknown, Salesforce NPSP is the safest default for NYC nonprofits of TA's size.

---

### Shadow Test: Transportation Alternatives

Integration test. Run the full tool against TA's board and compare to ground truth.

---

**Criterion 35: Full pipeline — single lookup**

Test input: "Janet Liff"
Pass if: Returns board seats (≥2 orgs), co-members across all boards, ranked by shared boards and recency, with board/staff distinction, compensation data, and source attribution on every fact. In deep research mode, also returns web-sourced connections with confidence labels.

---

**Criterion 36: Full pipeline — batch**

Test input: All 19 current TA board members.
Pass if: Combined, deduplicated prospect list. Multi-connection prospects rank highest. CSV export works and is CRM-import-ready. Gap list identifies new members needing manual research. At least 50 unique prospects surfaced in quick mode; at least 100 in deep research mode.

---

**Criterion 37: Shadow accuracy**

Test input: Compare batch output to ground truth above.
Pass if:
- ≥12 of 13 "confirmed current" members found in 990 data
- ≥6 of 8 departed members correctly flagged
- ≥4 of 6 new members correctly flagged as gaps
- Advisory council members categorized correctly (not just "departed")
- No false positives (nobody incorrectly attributed to TA)
- Web-enriched results (deep mode) surface at least 20 connections not found in 990 data alone

---

**Criterion 38: End-to-end value**

This is the only human-evaluated criterion. Show the batch output to Ben and Aubrie.
Pass if: The prospect list contains at least 5 names they didn't already have on their radar. At least 2 of those 5 came from enrichment layers (LittleSis, web search), not 990 data alone.
Fail if: They already knew everyone, or the output is too noisy to use, or enrichment added nothing.

---

## Data Sources

### Architecture Principle

Data sources are pluggable. Every source implements some or all of:

```python
class DataSource:
    name: str  # "propublica_990", "littlesis", "transalt_website", "web_search"

    async def search_person(self, name: str) -> list[BoardSeat]:
        """Given a name, return board seats found."""

    async def search_org(self, ein: str) -> list[BoardMember]:
        """Given an EIN, return board members and key employees."""

    async def search_relationships(self, name: str) -> list[Relationship]:
        """Given a name, return non-board relationships. Optional."""

    async def deep_research(self, name: str, context: dict = {}) -> list[Relationship]:
        """Given a name + known context (employer, orgs), run structured
        web queries and return discovered connections. Optional.
        Context dict might include: {'employer': 'D.E. Shaw', 'orgs': ['TA', 'Tech:NYC']}"""
```

Not all sources implement all methods. ProPublica implements `search_person` and `search_org`. LittleSis implements `search_person` and `search_relationships`. The website scraper implements `search_org` (by URL, not EIN — adapt the interface as needed). That's fine.

Every fact is tagged with its source. Adding a new source (Candid, OpenSecrets, state AG data) means writing a new module that conforms to the interface. The ranking and deduplication logic doesn't change.

### Available Sources (in build order)

**1. ProPublica Nonprofit Explorer**
- People search: `projects.propublica.org/nonprofits/search?q={name}&search_type=people` (HTML scrape — no API for people search)
- Org API: `projects.propublica.org/nonprofits/api/v2/organizations/{ein}.json` (JSON, but doesn't include officers)
- Org page: `projects.propublica.org/nonprofits/organizations/{ein}` (HTML scrape for officer lists under each fiscal year)
- Rate limit: Cap at 10 concurrent requests. Cache aggressively. ProPublica is a nonprofit — don't hammer them.
- Tip: The org page lists officers under each fiscal year's "Compensation" table. Some orgs have a "See filing for N other people" link — follow it for the complete list.

**2. GivingTuesday 990 Data API**
- Base URL: `990-infrastructure.gtdata.org`
- Table 2 contains board members: name, role, compensation
- Free, no auth. Check their API documentation for exact endpoints.

**3. LittleSis**
- Entity search: `littlesis.org/api/entities/search?q={name}`
- Relationships: `littlesis.org/api/entities/{id}/relationships`
- Free, no auth.
- 400K+ people, 1.6M+ relationships.
- Relationship types include: board member, employee, donor, family/spouse, lobbying.
- Has an "interlocks" concept — shared board positions already mapped.

**4. TransAlt website**
- URL: `transalt.org/staff#board`
- Bespoke scraper for this one org.
- Board members and advisory council are in separate sections on the page.
- Advisory council members are NOT on any 990 — this is the only source for them.

**5. Web search (deep research mode)**
- Use whatever search API is available: SerpAPI, Google Custom Search, Brave Search API, or raw requests + Google.
- Structured queries per person (see Phase 4 criteria for patterns).
- Parse results for names, org affiliations, event names, and dates. Don't return raw search snippets — extract structured data.
- Rate limit to 1-2 requests/second to avoid blocks.
- Cache results with the same 30-day TTL as other sources.
- Every result tagged with source URL and the query that found it.

**6. Future (not in scope for v1):**
- Candid/GuideStar (paywalled — best data if they get a subscription)
- OpenSecrets (political donation overlay)
- IRS 990 XML on AWS (raw firehose — what ProPublica/GivingTuesday are built on)
- State AG charity registration data
- Direct CRM read (Salesforce API, Bloomerang API, etc.) — only if CSV import proves too manual

### Error Handling

When a source fails (down, rate-limited, malformed response):
1. Retry up to 3 times with backoff
2. If still failing, skip that source for this query
3. Note the skipped source in the result's gap list: "ProPublica unavailable — results may be incomplete"
4. Never crash the whole lookup because one source is down

---

## Data Models

```python
from pydantic import BaseModel
from datetime import datetime

class BoardMember(BaseModel):
    name: str
    role: str                      # "Director", "Chair", "Treasurer", "Exec Director"
    member_type: str               # "board", "officer", "staff", "advisory"
    is_board: bool                 # 990 Part VII: Individual trustee or director
    is_officer: bool               # 990 Part VII: Officer
    is_key_employee: bool          # 990 Part VII: Key employee
    compensation: float = 0.0
    source: str

class BoardSeat(BaseModel):
    person_name: str
    org_name: str
    ein: str
    role: str
    member_type: str               # "board", "officer", "staff", "advisory"
    tax_period: str                # "2024-03"
    compensation: float = 0.0
    source: str
    is_current: bool = True
    org_type: str = "public_charity"  # or "private_foundation"

class Relationship(BaseModel):
    person_name: str
    related_to: str                # person or org name
    relationship_type: str         # "board_member", "spouse", "employee", "donor", "advisor"
    context: str = ""              # "both at D.E. Shaw", "family foundation co-trustees"
    source: str
    source_url: str = ""           # for web search results: the URL that confirmed this
    confidence: str = "high"       # "high" (990/LittleSis), "medium" (web, specific attribution), "low" (single mention)
    year: str = ""

class CoConnection(BaseModel):
    name: str
    shared_orgs: list[str]
    relationship_types: list[str]
    member_type: str               # "board", "staff", "advisory", "spouse"
    most_recent_overlap: str
    overlap_count: int
    is_current: bool
    compensation: float = 0.0
    sources: list[str]
    confidence: str = "high"       # lowest confidence across all supporting sources
    spouse: str = ""

class SearchResult(BaseModel):
    query_name: str
    timestamp: datetime
    board_seats: list[BoardSeat]
    connections: list[CoConnection]
    gaps: list[str]                # names/orgs needing manual research
    sources_used: list[str]
    sources_failed: list[str] = [] # sources that errored
```

## Storage

SQLite database (single file on disk, created at runtime). Tables:

**cache**: Raw API/scrape results keyed by (source, query, query_type). TTL: 30 days (configurable).

**search_log**: Every search. Query name, timestamp, result count, sources hit.

**snapshots**: Point-in-time exports. Saved when CSV is exported. Enables diff against previous.

**validations**: Website ↔ 990 reconciliation. Columns: ein, person_name, status (confirmed/departed/new_unresolved/advisory), on_990, on_website, validated_at.

## File Structure

```
board-network-mapper/
├── SPEC.md                        # This file (Joel reads this)
├── README.md                      # Setup instructions (Ben reads this)
├── requirements.txt
├── app.py                         # UI entry point
├── core/
│   ├── __init__.py
│   ├── models.py                  # Pydantic models
│   ├── network_builder.py         # Merge, deduplicate, rank
│   ├── cache.py                   # SQLite cache layer
│   └── batch.py                   # Batch mode
├── sources/
│   ├── __init__.py
│   ├── base.py                    # DataSource interface
│   ├── propublica.py              # 990 via ProPublica
│   ├── givingtuesday.py           # 990 via GivingTuesday API
│   ├── littlesis.py               # Relationship graph
│   ├── web_search.py              # Deep research: structured web queries
│   └── website_transalt.py        # TransAlt board page
├── tests/
│   ├── conftest.py                # Ground truth fixtures
│   ├── test_smoke.py              # S1-S5 smoke tests
│   ├── test_criterion_01.py       # One file per criterion
│   ├── ...
│   ├── test_criterion_38.py
│   └── fixtures/
│       └── ground_truth_transalt.json
└── data/
    └── board_mapper.db            # SQLite (created at runtime)
```

## Build Sequence

Build in criterion order. Each step should have a passing test.

1. Core models + cache (models.py, cache.py)
2. Criteria 1-3: Person search, org roster, board/staff distinction
3. Criteria 4-6: Two-hop network, ranking, current/former
4. Criterion 7: 990-PF capability
5. Criteria 8-9: Disambiguation, compensation
6. Criteria 10-11: Batch mode, CRM-ready CSV export
7. Criteria 12-15: Website scraper, reconciliation, advisory council, gap flagging
8. Criteria 16-19: LittleSis, spousal data, second-hop via spouse
9. Criteria 20-26: Web enrichment — structured queries, employer networks, event co-appearances, confidence tiering, deep research mode
10. Criteria 27-32: Click-through, speed, history, audit trail, caching
11. Criteria 33-34: CRM workflow — known-prospect exclusion, CRM-shaped export
12. Criteria 35-38: Shadow test, end-to-end validation

## Deployment

Joel builds and pushes to GitHub. Ben clones and runs locally.

```bash
git clone https://github.com/<joel>/board-network-mapper
cd board-network-mapper
pip install -r requirements.txt
python app.py
```

No auth, no cloud, no accounts. The SQLite file is the only state.

If Ben can't deal with Terminal, Joel can deploy to a free tier (Render, Railway, Streamlit Cloud) and give Ben a URL. But local-first is the default.

## What This Is NOT

- **Not a CRM.** The tool finds prospects. The CRM manages relationships. Prospect status, meeting notes, and gift history live in whatever CRM TA already uses. But the CSV export should be shaped so it imports cleanly.
- **Not a platform or product.** Tool for two people at one org.
- **Not real-time.** 990s lag 12-18 months. Web search results may be stale. The tool is honest about what it knows, when it last checked, and how confident it is.
- **AI is optional.** The 990/LittleSis/website pipeline is deterministic — data in, ranked list out. Web enrichment may benefit from LLM parsing of search results in a future version, but v1 should work without it.

## Requirements

```
aiohttp>=3.9.0
beautifulsoup4>=4.12.0
pydantic>=2.0.0
nest-asyncio>=1.5.0
pandas>=2.0.0
lxml>=4.9.0
pytest>=7.0.0
pytest-asyncio>=0.21.0
```

UI framework: builder's choice. Streamlit is fast. Flask gives more control. Whatever gets to Criterion 27 fastest.

---

## Before You Start Building (Questions for Joel → Avi → Ben)

These don't block Phase 1, but the answers shape Phases 4-6 and CRM integration. Joel should ask Avi, who will ask Ben/Aubrie:

1. **What CRM do you use?** (Shapes the CSV export columns and the known-prospects exclusion format. If Salesforce, the Nonprofit Success Pack has a specific import layout.)
2. **Can you export your current prospect list?** (Even a rough CSV of "people we're already working." This becomes the exclusion list for Criterion 33.)
3. **Who else should have access?** (Just Ben and Aubrie? The CRM manager? Board members themselves? Affects deployment — local tool vs. hosted URL.)
4. **Are there specific people you want to test first?** (Beyond Janet Liff / Martin Mignot — their actual priority lookups would be better smoke tests than our defaults.)
5. **How do you currently prep for donor meetings?** (Understanding the manual process helps calibrate what "useful" means for Criterion 38.)

---

## Criterion Summary

| # | Phase | Criterion | Key Test |
|---|-------|-----------|----------|
| 1 | 990 Backbone | Person → board seats | Janet Liff returns TA + ≥1 other org |
| 2 | 990 Backbone | Org → full roster | EIN 510186015 returns ≥20 people |
| 3 | 990 Backbone | Board vs. staff | Liff = board, Furnas = staff |
| 4 | 990 Backbone | Two-hop network | Liff returns people from ≥2 orgs |
| 5 | 990 Backbone | Network ranking | Sorted by shared boards, then recency |
| 6 | 990 Backbone | Historical vs. current | Departed members flagged |
| 7 | 990 Backbone | 990-PF capability | Private foundations searched if they exist |
| 8 | 990 Backbone | Name disambiguation | "Michael Smith" shows disambiguation |
| 9 | 990 Backbone | Compensation data | $0 for volunteers, real numbers for staff |
| 10 | Integration | Batch mode | 19 members → merged prospect list |
| 11 | Integration | CSV export (CRM-ready) | First/Last split, stable IDs, configurable columns |
| 12 | Website | Target org scrape | ≥19 board + advisory from transalt.org |
| 13 | Website | 990 ↔ website reconciliation | 3 categories: confirmed, departed, new |
| 14 | Website | Advisory council status | Beane/Archer/Ellis = advisory, not departed |
| 15 | Website | Gap flagging | Barton/Chen flagged as needing manual research |
| 16 | LittleSis | LittleSis lookup | If in LittleSis, relationships returned |
| 17 | LittleSis | Net-new connections | LittleSis adds ≥1 relationship not in 990 |
| 18 | LittleSis | Spousal/family | Kaizer → Adam Moss as spouse |
| 19 | LittleSis | Second-hop via spouse | Adam Moss's network accessible |
| 20 | Web Enrichment | Non-board affiliations | Mignot → Index Ventures or similar |
| 21 | Web Enrichment | Advisory at other orgs | Advisory role elsewhere surfaced |
| 22 | Web Enrichment | Structured web queries | ≥3 targeted searches, parsed results |
| 23 | Web Enrichment | Employer network | Karl Chen → D.E. Shaw orbit |
| 24 | Web Enrichment | Event co-appearances | ≥1 gala/panel co-mention found |
| 25 | Web Enrichment | Confidence tiering | High/medium/low labels on all results |
| 26 | Web Enrichment | Deep research mode | <60 sec, ≥3 net-new connections |
| 27 | Usability | Click-through | Names are clickable, drill-down works |
| 28 | Usability | Speed (cached) | <2 seconds on second lookup |
| 29 | Usability | Speed (first fetch) | Quick <15 sec, deep <60 sec |
| 30 | Usability | Search history | Log visible, previous searches re-openable |
| 31 | Usability | Audit trail / snapshots | Previous exports saved, diff available |
| 32 | Usability | Cache expiry | 30-day TTL, configurable |
| 33 | CRM | Known prospects exclusion | Import exclusion list, flag "already in pipeline" |
| 34 | CRM | CRM-shaped export | Imports into major nonprofit CRM |
| 35 | Shadow Test | Full pipeline — single | Janet Liff end-to-end with all sources |
| 36 | Shadow Test | Full pipeline — batch | 19 members, ≥50 quick / ≥100 deep prospects |
| 37 | Shadow Test | Shadow accuracy | Matches ground truth within tolerances |
| 38 | Shadow Test | End-to-end value | ≥5 new names Ben/Aubrie didn't know |
