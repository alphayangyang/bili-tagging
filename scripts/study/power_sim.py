"""Minimal detectable effect simulation for the planned paired contrasts.

Answers a protocol question: with N participants and a paired Wilcoxon signed-rank
test (normal approximation, Holm-corrected across the three planned contrasts),
how large must the standardised mean difference be for 80% power?

Usage:
    python3 scripts/study/power_sim.py --n 24 --reps 2000
"""
from __future__ import annotations

import argparse
import random

from analyze import holm, wilcoxon_signed_rank

DEFAULT_DELTAS = (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
DEFAULT_SEED = 20261119


def simulate_one(n: int, delta: float, reps: int, seed: int, alpha: float = 0.05, contrasts: int = 3) -> dict:
    """Empirical power for one standardised effect size (unit SD, gaussian noise)."""
    rng = random.Random(seed)
    hits = 0
    hits_holm = 0
    for _ in range(reps):
        differences = [round(rng.gauss(delta, 1.0), 6) for _ in range(n)]
        p_value = wilcoxon_signed_rank(differences)["p"]
        if p_value < alpha:
            hits += 1
        # Conservative proxy: all three planned contrasts carry the same p-value.
        adjusted = holm([p_value] * contrasts)[0]
        if adjusted < alpha:
            hits_holm += 1
    return {
        "delta": delta,
        "power": round(hits / reps, 3),
        "power_holm": round(hits_holm / reps, 3),
        "n": n,
        "reps": reps,
    }


def simulate(n: int = 24, reps: int = 2000, deltas=DEFAULT_DELTAS, seed: int = DEFAULT_SEED) -> list[dict]:
    """Power curve over standardised effect sizes."""
    return [simulate_one(n, float(delta), reps, seed) for delta in deltas]


def minimal_detectable(rows: list[dict], target: float = 0.8, key: str = "power") -> float | None:
    """Smallest delta in the curve reaching the target power (None when never reached)."""
    for row in rows:
        if row[key] >= target:
            return row["delta"]
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=24)
    parser.add_argument("--reps", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)
    rows = simulate(args.n, args.reps, seed=args.seed)
    print("N=%d reps=%d (paired Wilcoxon, normal approximation, gaussian noise, unit SD)" % (args.n, args.reps))
    print("delta  power  power_holm3")
    for row in rows:
        print("%.2f   %.3f  %.3f" % (row["delta"], row["power"], row["power_holm"]))
    print("minimum detectable delta at power 0.80: %s (Holm: %s)" % (
        minimal_detectable(rows),
        minimal_detectable(rows, key="power_holm"),
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())