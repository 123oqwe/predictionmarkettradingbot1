"""Validate Phase 4 extractor against the hand-built event_map ground truth.

For each pair in event_map.yaml:
  1. Synthesize a minimal "market description" from the pair's notes/tags.
  2. Run the extractor in STUB mode (or ANTHROPIC if API key set).
  3. Run the matcher on the two extracted criteria.
  4. Compare the matcher's verdict to the ground truth (any divergent cases?
     match should be False).

Output: a markdown report showing per-pair agreement.

Usage:
  python scripts/validate_extractor.py --output reports/extractor_validation.md
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.matching.event_map import load_event_map  # noqa: E402
from src.matching.extractor import (  # noqa: E402
    Extractor,
    ExtractorConfig,
    ExtractorMode,
)
from src.matching.matcher import compare  # noqa: E402


def _market_text(pair, side: str) -> tuple:
    """Build (title, description, rules) text for one side of a pair.

    For real validation the operator pulls actual rules text from each
    platform's API. For this validation, we synthesize minimal descriptors
    from the event_map's notes — enough for STUB extractor to bin the
    event_type correctly.
    """
    if side == "polymarket":
        title = pair.pair_id.replace("-", " ").title() + " (Polymarket)"
        platform = "Polymarket"
        platform_id = pair.polymarket_market_id
    else:
        title = pair.pair_id.replace("-", " ").title() + " (Kalshi)"
        platform = "Kalshi"
        platform_id = pair.kalshi_market_ticker
    notes = pair.notes or ""
    tags = ", ".join(pair.topic_tags or [])
    description = f"Topic tags: {tags}. {notes}"
    rules = "\n".join(
        f"- {ec.scenario} → {getattr(ec, side)} (divergent={ec.divergent})"
        for ec in pair.edge_cases_reviewed
    )
    return title, description, rules


async def main_async(args) -> None:
    em = load_event_map(args.event_map)
    if not em.pairs:
        print("event_map empty; nothing to validate")
        return

    mode = ExtractorMode.ANTHROPIC if os.environ.get("ANTHROPIC_API_KEY") else ExtractorMode.STUB
    cfg = ExtractorConfig(mode=mode)
    extractor = Extractor(cfg)

    out = []
    out.append("# Extractor validation report")
    out.append("")
    out.append(f"- Event map: {args.event_map}")
    out.append(f"- Mode: **{mode.value}**")
    out.append(f"- Pairs: {len(em.pairs)}")
    out.append("")

    agreements = 0
    for pair in em.pairs:
        a_title, a_desc, a_rules = _market_text(pair, "polymarket")
        b_title, b_desc, b_rules = _market_text(pair, "kalshi")

        crit_a = await extractor.extract(
            title=a_title,
            description=a_desc,
            rules_text=a_rules,
        )
        crit_b = await extractor.extract(
            title=b_title,
            description=b_desc,
            rules_text=b_rules,
        )
        result = compare(crit_a, crit_b)

        # Ground truth: pair has at least one divergent case → expected match=False.
        ground_truth_should_match = not any(
            ec.divergent for ec in pair.edge_cases_reviewed
        )
        agree = (result.match == ground_truth_should_match)
        if agree:
            agreements += 1

        out.append(f"## {pair.pair_id}")
        out.append("")
        out.append(f"- Ground truth: any divergent? "
                   f"{'YES' if not ground_truth_should_match else 'NO'} → "
                   f"expected match={ground_truth_should_match}")
        out.append(f"- Extractor said: match={result.match}, confidence={result.confidence:.2f}")
        out.append(f"- Agreement: {'✅' if agree else '❌'}")
        if result.differences:
            out.append("- Differences:")
            for d in result.differences[:10]:
                out.append(f"    - {d}")
        out.append("")

    out.insert(2, f"- Agreement: **{agreements}/{len(em.pairs)}** = "
                  f"{agreements/len(em.pairs):.0%}\n")

    text = "\n".join(out) + "\n"
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text)
        print(f"wrote {args.output}")
    else:
        print(text)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--event-map", default="event_map.yaml")
    p.add_argument("--output", default=None)
    args = p.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
