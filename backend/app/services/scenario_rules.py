"""Scenario output rules (section 8 B).

Rules run in order on the model score. Each rule stores its own effect so the
user can read how much every rule moved the number.
"""
from __future__ import annotations

from dataclasses import dataclass, field

Values = dict[tuple[str, int], float]

RULE_TYPES = [
    "multiplier",
    "additive",
    "shift",
    "event",
    "cap",
    "launch_curve",
    "phase_out",
    "elasticity",
]

RULE_LABELS = {
    "multiplier": "Çarpan",
    "additive": "Toplama",
    "shift": "Dönem kaydırma",
    "event": "Olay",
    "cap": "Sınır",
    "launch_curve": "Lansman eğrisi",
    "phase_out": "Terk eğrisi",
    "elasticity": "Esneklik",
}


@dataclass
class ResolvedRule:
    id: int
    type: str
    name: str
    order_index: int
    series: set[str]
    buckets: list[int]
    params: dict = field(default_factory=dict)


@dataclass
class RuleOutcome:
    values: Values
    effects: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def apply_rules(values: Values, rules: list[ResolvedRule], future_buckets: list[int]) -> RuleOutcome:
    out: Values = dict(values)
    effects: list[dict] = []
    warnings: list[str] = []

    for rule in sorted(rules, key=lambda r: r.order_index):
        before = {k: v for k, v in out.items() if k[0] in rule.series}
        handler = _HANDLERS.get(rule.type)
        if handler is None:
            warnings.append(f"Bilinmeyen kural tipi atlandı: {rule.type}")
            continue
        handler(out, rule, future_buckets, warnings)
        for key, old in before.items():
            new = out.get(key, 0.0)
            if abs(new - old) > 1e-9:
                effects.append(
                    {"rule_id": rule.id, "series_key": key[0], "bucket_id": key[1], "delta": round(new - old, 4)}
                )
        # A rule may create cells that did not exist (launch curve on a new series).
        for key, new in out.items():
            if key[0] in rule.series and key not in before and abs(new) > 1e-9:
                effects.append(
                    {"rule_id": rule.id, "series_key": key[0], "bucket_id": key[1], "delta": round(new, 4)}
                )
    return RuleOutcome(values=out, effects=effects, warnings=warnings)


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------


def _multiplier(values: Values, rule: ResolvedRule, _future, _warn) -> None:
    factor = 1.0 + float(rule.params.get("pct", 0.0)) / 100.0
    for sk in rule.series:
        for b in rule.buckets:
            if (sk, b) in values:
                values[(sk, b)] = max(0.0, values[(sk, b)] * factor)


def _additive(values: Values, rule: ResolvedRule, _future, warn) -> None:
    """The amount is a total per bucket, split across the scope by current share."""
    amount = float(rule.params.get("amount", 0.0))
    per_bucket = bool(rule.params.get("per_bucket", True))
    buckets = rule.buckets or []
    if not buckets:
        return
    share_total = amount if per_bucket else amount / len(buckets)
    for b in buckets:
        present = [sk for sk in rule.series if (sk, b) in values]
        if not present:
            continue
        base = sum(values[(sk, b)] for sk in present)
        if base <= 0:
            warn.append(f"{rule.name or rule.type}: taban sıfır, eşit dağıtım uygulandı")
            for sk in present:
                values[(sk, b)] = max(0.0, values[(sk, b)] + share_total / len(present))
        else:
            for sk in present:
                w = values[(sk, b)] / base
                values[(sk, b)] = max(0.0, values[(sk, b)] + share_total * w)


def _shift(values: Values, rule: ResolvedRule, _future, warn) -> None:
    src = rule.params.get("source_bucket_id")
    tgt = rule.params.get("target_bucket_id")
    ratio = float(rule.params.get("ratio", 0.0)) / 100.0
    if src is None or tgt is None:
        warn.append(f"{rule.name or 'shift'}: kaynak veya hedef dönem eksik")
        return
    for sk in rule.series:
        if (sk, src) not in values:
            continue
        moved = values[(sk, src)] * ratio
        values[(sk, src)] = max(0.0, values[(sk, src)] - moved)
        values[(sk, tgt)] = max(0.0, values.get((sk, tgt), 0.0) + moved)


def _event(values: Values, rule: ResolvedRule, future_buckets, _warn) -> None:
    effect = rule.params.get("effect", "temporary")
    pct = float(rule.params.get("pct", 0.0)) / 100.0
    set_value = rule.params.get("set_value")
    buckets = rule.buckets or []
    if not buckets:
        return

    def _apply(sk: str, b: int, factor: float) -> None:
        if (sk, b) not in values:
            return
        if set_value is not None:
            values[(sk, b)] = float(set_value)
        else:
            values[(sk, b)] = max(0.0, values[(sk, b)] * (1.0 + factor))

    for sk in rule.series:
        if effect == "point":
            _apply(sk, buckets[0], pct)
        elif effect == "temporary":
            for b in buckets:
                _apply(sk, b, pct)
        elif effect == "level":
            start = buckets[0]
            for b in future_buckets:
                if b >= start:
                    _apply(sk, b, pct)
        elif effect == "ramp":
            n = len(buckets)
            for i, b in enumerate(buckets, start=1):
                _apply(sk, b, pct * i / n)
            last = buckets[-1]
            for b in future_buckets:
                if b > last:
                    _apply(sk, b, pct)


def _cap(values: Values, rule: ResolvedRule, _future, _warn) -> None:
    upper = rule.params.get("max")
    lower = rule.params.get("min")
    scope_level = rule.params.get("scope_level", "series")
    for b in rule.buckets:
        present = [sk for sk in rule.series if (sk, b) in values]
        if not present:
            continue
        if scope_level == "total":
            total = sum(values[(sk, b)] for sk in present)
            target = total
            if upper is not None and total > float(upper):
                target = float(upper)
            if lower is not None and total < float(lower):
                target = float(lower)
            if target != total and total > 0:
                k = target / total
                for sk in present:
                    values[(sk, b)] *= k
        else:
            for sk in present:
                v = values[(sk, b)]
                if upper is not None:
                    v = min(v, float(upper))
                if lower is not None:
                    v = max(v, float(lower))
                values[(sk, b)] = max(0.0, v)


def _launch_curve(values: Values, rule: ResolvedRule, _future, _warn) -> None:
    """Sets the level directly. A new series has no usable history."""
    peak = float(rule.params.get("peak", 0.0))
    template = rule.params.get("template", "s_curve")
    analog = rule.params.get("analog_profile") or []
    buckets = rule.buckets or []
    n = len(buckets)
    if n == 0:
        return
    for sk in rule.series:
        for i, b in enumerate(buckets):
            if analog:
                profile = float(analog[i % len(analog)])
            elif template == "linear":
                profile = (i + 1) / n
            else:  # s curve
                x = (i + 1) / n
                profile = 1.0 / (1.0 + pow(2.718281828, -10 * (x - 0.5)))
            values[(sk, b)] = max(0.0, peak * profile)


def _phase_out(values: Values, rule: ResolvedRule, future_buckets, _warn) -> None:
    profile = rule.params.get("profile", "linear")
    buckets = rule.buckets or []
    n = len(buckets)
    if n == 0:
        return
    for sk in rule.series:
        for i, b in enumerate(buckets, start=1):
            if (sk, b) not in values:
                continue
            factor = max(0.0, 1.0 - i / n) if profile == "linear" else pow(0.6, i)
            values[(sk, b)] = max(0.0, values[(sk, b)] * factor)
        last = buckets[-1]
        for b in future_buckets:
            if b > last and (sk, b) in values:
                values[(sk, b)] = 0.0


def _elasticity(values: Values, rule: ResolvedRule, _future, _warn) -> None:
    """value * (1 + price change) ** elasticity."""
    e = float(rule.params.get("elasticity", 0.0))
    price_pct = float(rule.params.get("price_pct", 0.0)) / 100.0
    factor = pow(max(0.01, 1.0 + price_pct), e)
    for sk in rule.series:
        for b in rule.buckets:
            if (sk, b) in values:
                values[(sk, b)] = max(0.0, values[(sk, b)] * factor)


_HANDLERS = {
    "multiplier": _multiplier,
    "additive": _additive,
    "shift": _shift,
    "event": _event,
    "cap": _cap,
    "launch_curve": _launch_curve,
    "phase_out": _phase_out,
    "elasticity": _elasticity,
}
