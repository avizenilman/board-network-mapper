# Board Network Mapper

Map nonprofit board member networks using IRS 990 filings, LittleSis, and web research. Find warm introduction paths for fundraising.

**Built for Transportation Alternatives.** See [SPEC.md](SPEC.md) for the full technical specification.

## Setup

```bash
git clone https://github.com/avizenilman/board-network-mapper
cd board-network-mapper
pip install -r requirements.txt
python app.py
```

Requires Python 3.10+. No accounts or API keys needed for core functionality.

## What It Does

Type a name → see every nonprofit board they sit on → see everyone who co-serves on those boards → ranked by connection strength.

**Quick mode:** 990 data + LittleSis. Seconds.
**Deep research:** + web search for corporate boards, employer networks, event co-appearances. Minutes.

## For Developers

See [SPEC.md](SPEC.md) for:
- 38 success criteria with test inputs and expected outputs
- Ground truth data for Transportation Alternatives
- Data models, file structure, and build sequence
- API documentation for all data sources

Build in criterion order. Each criterion has a passing test before moving to the next.

## Status

Spec complete. Implementation not started.
