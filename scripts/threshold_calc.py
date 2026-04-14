"""Cross-market threshold derivation calculator.

From phase-1-dual-platform.md:

  EV = (1 - p_div) * r_cross + p_div * (-1.0 annualized)
  For r_cross to match an intra-market trade at threshold r_intra:
      r_cross >= (r_intra + p_div) / (1 - p_div)

Run:
  python scripts/threshold_calc.py --intra 0.20 --p-div 0.02
  python scripts/threshold_calc.py --intra 0.20 --p-div 0.05
"""
from __future__ import annotations

import argparse
from decimal import Decimal


def derive_cross_threshold(intra: Decimal, p_divergence: Decimal) -> Decimal:
    """Return the minimum cross-market annualized threshold for given divergence prob.

    EV(cross @ r) = (1 - p)*r + p*(-1)
    Set equal to intra threshold:
        (1 - p)*r - p = r_intra
        r = (r_intra + p) / (1 - p)
    """
    if p_divergence < 0 or p_divergence >= 1:
        raise ValueError("p_divergence must be in [0, 1)")
    return (intra + p_divergence) / (Decimal(1) - p_divergence)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--intra", type=str, default="0.20", help="intra-market annualized threshold (e.g. 0.20)")
    p.add_argument("--p-div", type=str, default="0.05", help="assumed rule-divergence probability per pair")
    args = p.parse_args()

    intra = Decimal(args.intra)
    p_div = Decimal(args.p_div)
    r = derive_cross_threshold(intra, p_div)

    print(f"Intra-market threshold:   {intra:.4f}  ({intra*100:.2f}%)")
    print(f"Assumed p_divergence:     {p_div:.4f}  ({p_div*100:.2f}%)")
    print(f"Cross-market min threshold: {r:.4f}  ({r*100:.2f}%)")
    print()
    print("Reading: better edge case review → lower p_divergence → lower threshold →")
    print("more trades qualify. Sloppy review costs money directly.")


if __name__ == "__main__":
    main()
