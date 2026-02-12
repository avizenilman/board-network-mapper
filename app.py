"""Board Network Mapper — Streamlit UI.

Run with: streamlit run app.py
"""

import asyncio
import time
from datetime import datetime

import nest_asyncio
import pandas as pd
import streamlit as st

nest_asyncio.apply()

from core.database import Database
from core.models import CoConnection, SearchResult
from core.network_builder import NetworkBuilder
from core.batch import (
    connections_to_dataframe,
    export_csv,
    load_exclusion_list,
    diff_snapshots,
)
from sources.propublica import ProPublicaSource

# Lazy imports for optional sources
_littlesis_source = None
_website_source = None
_web_search_source = None


def _get_sources(db: Database):
    """Initialize all available data sources."""
    sources = []

    # NPODC local database — primary source (fast, no network)
    try:
        from sources.npodc_990 import NPODCSource
        sources.append(NPODCSource(db=db))
    except (ImportError, Exception):
        pass

    # ProPublica — fallback for recent years NPODC may not cover
    sources.append(ProPublicaSource(db=db))

    try:
        from sources.littlesis import LittleSisSource
        sources.append(LittleSisSource(db=db))
    except (ImportError, Exception):
        pass

    try:
        from sources.web_search import WebSearchSource
        sources.append(WebSearchSource(db=db))
    except (ImportError, Exception):
        pass

    return sources


@st.cache_resource
def get_db():
    return Database()


@st.cache_resource
def get_builder():
    db = get_db()
    sources = _get_sources(db)
    return NetworkBuilder(sources=sources, db=db)


def run_async(coro):
    """Run an async coroutine in the event loop."""
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(coro)


def display_board_seats(result: SearchResult):
    """Display board seats in a table."""
    if not result.board_seats:
        st.info("No board seats found via 990 filings.")
        return

    st.subheader(f"Board Seats ({len(result.board_seats)})")

    # Deduplicate by org
    orgs = {}
    for seat in result.board_seats:
        key = seat.ein or seat.org_name
        if key not in orgs:
            orgs[key] = seat
        elif seat.fiscal_year > orgs[key].fiscal_year:
            orgs[key] = seat

    for seat in orgs.values():
        org_type_badge = "🏛️" if seat.org_type == "private_foundation" else ""
        with st.expander(f"{seat.org_name} {org_type_badge} — {seat.role} ({seat.tax_period})"):
            cols = st.columns(4)
            cols[0].metric("Role", seat.role)
            cols[1].metric("EIN", seat.ein)
            cols[2].metric("Type", seat.member_type)
            cols[3].metric("Compensation", f"${seat.total_compensation:,.0f}")
            st.caption(f"Source: {seat.source} | Tax period: {seat.tax_period}")


def display_connections(result: SearchResult, query_name: str):
    """Display co-connections with click-through drill-down."""
    if not result.connections:
        st.info("No co-connections found.")
        return

    st.subheader(f"Network Connections ({len(result.connections)})")

    # Disambiguation: if too many results, show filters
    if len(result.connections) > 20:
        col1, col2 = st.columns(2)
        with col1:
            type_filter = st.selectbox(
                "Filter by type",
                ["All", "Board", "Staff", "Advisory"],
                key=f"type_filter_{query_name}",
            )
        with col2:
            current_filter = st.selectbox(
                "Show",
                ["All", "Current only", "Former only"],
                key=f"current_filter_{query_name}",
            )
    else:
        type_filter = "All"
        current_filter = "All"

    filtered = result.connections
    if type_filter != "All":
        filtered = [c for c in filtered if c.member_type == type_filter.lower()]
    if current_filter == "Current only":
        filtered = [c for c in filtered if c.is_current]
    elif current_filter == "Former only":
        filtered = [c for c in filtered if not c.is_current]

    # Build dataframe for display
    rows = []
    for conn in filtered:
        rows.append({
            "Name": conn.name,
            "Shared Orgs": ", ".join(conn.shared_orgs),
            "Role(s)": ", ".join(set(conn.roles)),
            "Type": conn.member_type,
            "Overlap": conn.overlap_count,
            "Most Recent": conn.most_recent_overlap,
            "Current": "✓" if conn.is_current else "✗",
            "Compensation": f"${conn.compensation:,.0f}" if conn.compensation > 0 else "$0",
            "Confidence": conn.confidence,
            "Connected Via": ", ".join(conn.connected_via),
        })

    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Name": st.column_config.TextColumn("Name", width="medium"),
                "Compensation": st.column_config.TextColumn("Comp.", width="small"),
            },
        )

        # Click-through: select a person to drill down
        selected_name = st.selectbox(
            "Drill down into a person's network:",
            ["(select)"] + [c.name for c in filtered[:50]],
            key=f"drilldown_{query_name}",
        )
        if selected_name != "(select)":
            if st.button(f"Search \"{selected_name}\"", key=f"search_btn_{query_name}"):
                st.session_state["search_query"] = selected_name
                st.rerun()


def display_relationships(result: SearchResult):
    """Display non-board relationships (LittleSis, web search)."""
    if not result.relationships:
        return

    st.subheader(f"Additional Relationships ({len(result.relationships)})")

    # Group by confidence
    high = [r for r in result.relationships if r.confidence == "high"]
    medium = [r for r in result.relationships if r.confidence == "medium"]
    low = [r for r in result.relationships if r.confidence == "low"]

    for label, rels in [("High Confidence", high), ("Medium Confidence", medium), ("Low / Needs Verification", low)]:
        if rels:
            st.markdown(f"**{label}** ({len(rels)})")
            for rel in rels:
                st.markdown(
                    f"- **{rel.related_to}** — {rel.relationship_type} "
                    f"({rel.context[:100]}{'...' if len(rel.context) > 100 else ''}) "
                    f"[{rel.source}]"
                )
                if rel.source_url:
                    st.caption(f"Source: {rel.source_url}")


def display_gaps(result: SearchResult):
    """Display gap list — things that couldn't be resolved."""
    if not result.gaps:
        return

    st.subheader("Gaps / Manual Research Needed")
    for gap in result.gaps:
        st.warning(gap)

    if result.sources_failed:
        st.error(f"Data sources unavailable: {', '.join(result.sources_failed)}")


def search_history_sidebar():
    """Show search history in sidebar."""
    db = get_db()
    history = db.get_search_history(limit=20)

    if history:
        st.sidebar.markdown("---")
        st.sidebar.subheader("Search History")
        for h in history:
            ts = datetime.fromtimestamp(h["timestamp"]).strftime("%m/%d %H:%M")
            label = f"{h['query_name']} ({h['result_count']} results, {h['mode']}) — {ts}"
            if st.sidebar.button(label, key=f"history_{h['timestamp']}"):
                st.session_state["search_query"] = h["query_name"]
                st.rerun()


def main():
    st.set_page_config(
        page_title="Board Network Mapper",
        page_icon="🔗",
        layout="wide",
    )

    st.title("Board Network Mapper")
    st.caption("Map nonprofit board member networks. Find warm introduction paths.")

    # Sidebar
    st.sidebar.title("Settings")
    mode = st.sidebar.radio("Search Mode", ["Quick", "Deep Research"], index=0)
    search_mode = "quick" if mode == "Quick" else "deep"

    st.sidebar.markdown("---")

    # Cache status
    db = get_db()
    cache_age_info = ""
    st.sidebar.markdown("**Cache**")
    ttl_days = st.sidebar.slider("Cache TTL (days)", 1, 90, 30)
    if ttl_days != 30:
        db.ttl = ttl_days * 24 * 3600

    # Exclusion list upload
    st.sidebar.markdown("---")
    st.sidebar.markdown("**Known Prospects**")
    exclusion_file = st.sidebar.file_uploader(
        "Upload exclusion CSV",
        type=["csv"],
        help="CSV of people already in your pipeline. They'll be flagged in results.",
    )
    if exclusion_file is not None:
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as f:
            f.write(exclusion_file.read())
            count = load_exclusion_list(db, f.name)
            st.sidebar.success(f"Loaded {count} exclusions")

    excluded = db.get_excluded_names()
    if excluded:
        st.sidebar.caption(f"{len(excluded)} people in exclusion list")

    search_history_sidebar()

    # Main content — tabs
    tab_single, tab_batch, tab_org, tab_snapshots = st.tabs([
        "Person Search", "Batch Search", "Org Lookup", "Snapshots"
    ])

    # ---- Person Search Tab ----
    with tab_single:
        # Use session state for search query (for drill-down)
        default_query = st.session_state.get("search_query", "")

        search_query = st.text_input(
            "Search for a person:",
            value=default_query,
            placeholder="e.g., Janet Liff, Martin Mignot",
            key="person_search_input",
        )

        if search_query:
            builder = get_builder()
            with st.spinner(f"Searching for {search_query}..."):
                start_time = time.time()
                result = run_async(builder.search_person(search_query, mode=search_mode))
                elapsed = time.time() - start_time

            st.success(f"Found {len(result.board_seats)} board seats, {len(result.connections)} connections in {elapsed:.1f}s")

            # Display results
            display_board_seats(result)
            display_connections(result, search_query)
            display_relationships(result)
            display_gaps(result)

            # CSV download
            if result.connections:
                csv_data = export_csv(
                    result.connections,
                    result.relationships,
                    db,
                    query_description=f"Person: {search_query}",
                )
                st.download_button(
                    "Download CSV",
                    csv_data,
                    file_name=f"network_{search_query.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime="text/csv",
                )

    # ---- Batch Search Tab ----
    with tab_batch:
        st.subheader("Batch Search")
        st.markdown("Enter multiple names (one per line) to search all at once and merge results.")

        # Pre-populate with TA board members
        ta_board_default = """Janet Liff
Hope Reeves
Stanley Toussaint
Christine Berthet
Daniel Kaizer
Mary Beth Kelly
Andy Lerner
Gentry Lock
Adam Mansky
Martin Mignot
Keith Tubbs
Kenneth Weine
Claire Weisz
Jake Barton
Karl Chen
Lucia Deng
Edmundo Martinez
Wiley Norvell
Alison Sant"""

        batch_input = st.text_area(
            "Names (one per line):",
            value="",
            height=200,
            placeholder=ta_board_default,
        )

        if st.button("Run Batch Search"):
            names = [n.strip() for n in batch_input.strip().split("\n") if n.strip()]
            if not names:
                st.warning("Enter at least one name.")
            else:
                builder = get_builder()
                progress = st.progress(0)
                status = st.empty()

                with st.spinner(f"Searching {len(names)} people..."):
                    start_time = time.time()
                    merged, gaps = run_async(builder.batch_search(names, mode=search_mode))
                    elapsed = time.time() - start_time

                progress.progress(100)
                status.success(
                    f"Found {len(merged)} unique connections from {len(names)} people "
                    f"({len(gaps)} gaps) in {elapsed:.1f}s"
                )

                if gaps:
                    st.subheader("Gap List")
                    for gap in gaps:
                        st.warning(gap)

                if merged:
                    # Show results
                    df = connections_to_dataframe(merged, db=db)
                    st.subheader(f"Merged Connections ({len(df)} rows)")

                    # Pipeline filter
                    show_pipeline = st.checkbox("Show 'already in pipeline' separately", value=True)
                    if show_pipeline and "in_pipeline" in df.columns:
                        pipeline = df[df["in_pipeline"] == True]
                        new_prospects = df[df["in_pipeline"] != True]
                        if len(pipeline) > 0:
                            st.markdown(f"**Already in pipeline:** {len(pipeline)}")
                        st.dataframe(new_prospects, use_container_width=True, hide_index=True)
                    else:
                        st.dataframe(df, use_container_width=True, hide_index=True)

                    # Download
                    csv_data = export_csv(
                        merged, db=db,
                        query_description=f"Batch: {len(names)} people",
                    )
                    st.download_button(
                        "Download CSV",
                        csv_data,
                        file_name=f"batch_network_{datetime.now().strftime('%Y%m%d')}.csv",
                        mime="text/csv",
                    )

    # ---- Org Lookup Tab ----
    with tab_org:
        st.subheader("Organization Lookup")
        ein_input = st.text_input(
            "Enter EIN:",
            placeholder="e.g., 510186015 (Transportation Alternatives)",
        )

        if ein_input:
            builder = get_builder()
            with st.spinner(f"Looking up EIN {ein_input}..."):
                roster = run_async(builder.search_org(ein_input))

            if roster.members:
                st.success(f"{roster.org_name} — {len(roster.members)} people")

                board = [m for m in roster.members if m.member_type == "board"]
                staff = [m for m in roster.members if m.member_type == "staff"]

                col1, col2 = st.columns(2)
                with col1:
                    st.metric("Board Members", len(board))
                with col2:
                    st.metric("Staff / Key Employees", len(staff))

                # Show as dataframe
                rows = [{
                    "Name": m.name,
                    "Role": m.role,
                    "Type": m.member_type,
                    "Officer": "✓" if m.is_officer else "",
                    "Compensation": f"${m.total_compensation:,.0f}",
                    "Former": "✓" if m.is_former else "",
                } for m in roster.members]

                df = pd.DataFrame(rows)
                st.dataframe(df, use_container_width=True, hide_index=True)

                # Website reconciliation for TA
                if ein_input.strip() == "510186015":
                    try:
                        from sources.website_transalt import TransAltScraper, reconcile_with_990
                        if st.button("Run Website Reconciliation"):
                            with st.spinner("Scraping transalt.org..."):
                                ws = TransAltScraper(db=db)
                                website_members = run_async(ws.scrape())
                                recon = reconcile_with_990(website_members, roster)

                            st.subheader("990 ↔ Website Reconciliation")
                            for category, members in recon.items():
                                st.markdown(f"**{category}** ({len(members)})")
                                for m in members:
                                    st.markdown(f"- {m}")
                    except ImportError:
                        st.info("Website scraper not yet available.")
            else:
                st.warning(f"No data found for EIN {ein_input}")

    # ---- Snapshots Tab ----
    with tab_snapshots:
        st.subheader("Export Snapshots")
        snapshots = db.get_snapshots(limit=20)
        if snapshots:
            for snap in snapshots:
                ts = datetime.fromtimestamp(snap["timestamp"]).strftime("%Y-%m-%d %H:%M")
                st.markdown(
                    f"**{ts}** — {snap['query_description']} ({snap['row_count']} rows)"
                )
                csv_data = db.get_snapshot_data(snap["id"])
                if csv_data:
                    st.download_button(
                        f"Download snapshot {ts}",
                        csv_data,
                        file_name=f"snapshot_{ts.replace(' ', '_').replace(':', '')}.csv",
                        mime="text/csv",
                        key=f"snap_{snap['id']}",
                    )

            # Diff tool
            if len(snapshots) >= 2:
                st.markdown("---")
                st.subheader("Compare Snapshots")
                col1, col2 = st.columns(2)
                with col1:
                    old_id = st.selectbox(
                        "Older snapshot",
                        [s["id"] for s in snapshots],
                        format_func=lambda x: next(
                            f"{datetime.fromtimestamp(s['timestamp']).strftime('%Y-%m-%d')} ({s['row_count']} rows)"
                            for s in snapshots if s["id"] == x
                        ),
                    )
                with col2:
                    new_id = st.selectbox(
                        "Newer snapshot",
                        [s["id"] for s in snapshots],
                        format_func=lambda x: next(
                            f"{datetime.fromtimestamp(s['timestamp']).strftime('%Y-%m-%d')} ({s['row_count']} rows)"
                            for s in snapshots if s["id"] == x
                        ),
                        index=min(1, len(snapshots) - 1),
                    )

                if st.button("Compare"):
                    diff = diff_snapshots(db, old_id, new_id)
                    if "error" in diff:
                        st.error(diff["error"])
                    else:
                        st.metric("Added", diff["added_count"])
                        st.metric("Removed", diff["removed_count"])
                        st.metric("Unchanged", diff["unchanged_count"])

                        if diff["added"]:
                            st.markdown("**New connections:**")
                            st.dataframe(pd.DataFrame(diff["added"]))
                        if diff["removed"]:
                            st.markdown("**Removed connections:**")
                            st.dataframe(pd.DataFrame(diff["removed"]))
        else:
            st.info("No snapshots yet. Run a search and download CSV to create one.")


if __name__ == "__main__":
    main()
