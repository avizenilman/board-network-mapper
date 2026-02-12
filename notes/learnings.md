# Learnings

## LittleSis

[LEARN:littlesis-api] LittleSis API v2 relationship objects do NOT contain `entity1_name`/`entity2_name` fields or an `included` list. To get the related entity's name, parse it from the URL slug in the `entity` (= entity1) or `related` (= entity2) top-level fields. Format: `https://littlesis.org/{type}/{id}-{Name_With_Underscores}`. (2026-02-12)

[LEARN:littlesis-coverage] LittleSis covers ~400K entities. Many people relevant to our network mapping (e.g., Martin Mignot, Daniel Kaizer, Janet Liff) are NOT in LittleSis. It's strong for well-known US political/corporate figures (Jamie Dimon, Larry Fink, etc.) but weak for international VCs, lesser-known individuals, and non-US figures. Don't rely on it as the sole source. (2026-02-12)

[LEARN:littlesis-cache] The code caches empty LittleSis results (`[]`), which means once a name returns empty, it won't be re-queried even if the data is later added. This is correct behavior for performance but worth knowing when debugging "0 results" issues -- clear the cache to force a fresh query. (2026-02-12)
