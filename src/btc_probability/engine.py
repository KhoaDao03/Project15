"""Causal event reducer. Order books are downstream diagnostics only."""

from dataclasses import asdict, replace

from .books import Book
from .models import research_settlement, simulate, terminal
from .reference import ReferenceHistory
from .schema import YEAR, Config, ContractSpec, Forecast, Reference, digest
from .volatility import select


class Engine:
    def __init__(self, config=None, mode="live", *, baselines=True):
        self.config = config or Config()
        self.mode = mode
        self.baselines = baselines
        self.contracts = {}
        self.contract_ids = {}
        self.history = ReferenceHistory()
        self.reference_ids = []
        self.options = []
        self.option_id = None
        self.books = {}
        self.book_ids = {}
        self.last_sequence = 0
        self.last_received = float("-inf")
        self.flags = {}
        self.audit_references = []
        self.last_reconciliation = None

    def apply(self, event):
        seq, kind, now, data = event["seq"], event["kind"], event["received_time"], event["data"]
        if seq <= self.last_sequence:
            raise ValueError("DUPLICATE_EVENT_SEQUENCE")
        if now < self.last_received:
            raise ValueError("RECEIPT_CLOCK_REGRESSION")
        self.last_sequence, self.last_received = seq, now
        if kind == "contract":
            spec = ContractSpec.from_dict(data["normalized"])
            if spec.metadata_received > now or (self.mode != "synthetic" and spec.synthetic):
                raise ValueError("NONCAUSAL_OR_SYNTHETIC_CONTRACT")
            self.contracts[spec.market_ticker] = spec
            self.contract_ids[spec.market_ticker] = seq
        elif kind == "reference":
            self.audit_references.append(event)
            self.audit_references = [e for e in self.audit_references if e["received_time"] >= now - 1200]
            ref = Reference(**data["normalized"])
            if ref.received_time != now:
                raise ValueError("REFERENCE_RECEIPT_MISMATCH")
            if ref.source_time > now + self.config.clock_skew:
                self.flags["reference"] = "REFERENCE_CLOCK_SKEW"
            else:
                self.history.add(ref)
                self.reference_ids.append((seq, ref))
                cutoff = now - max(305, self.config.realized_window + 5)
                self.history.rows = [r for r in self.history.rows if r.source_time >= cutoff]
                self.reference_ids = [(i, r) for i, r in self.reference_ids if r.source_time >= cutoff]
                self.flags.pop("reference", None)
        elif kind == "outcome":
            from .reconciliation import reconcile

            self.last_reconciliation = None

            spec = self.contracts.get(data["market_ticker"])
            if spec and spec.profile_id and spec.sample_times:
                self.last_reconciliation = reconcile(
                    dict(
                        market_ticker=spec.market_ticker,
                        observation_end=spec.observation_end_time,
                        references=self.audit_references,
                        official_result_event=event,
                    ),
                    now,
                )
        elif kind == "options":
            self.options = data["normalized"]
            self.option_id = seq
        elif kind == "book":
            ticker = data["market_ticker"]
            self.books.setdefault(ticker, Book()).apply(data["normalized"], now)
            self.book_ids[ticker] = seq
        elif kind == "connection":
            for book in self.books.values():
                book.invalidate(data.get("reason", "RECONNECT"))
        elif kind == "failure":
            self.flags[data["component"]] = data["reason"]
        elif kind == "recovery":
            self.flags.pop(data["component"], None)
        elif kind == "forecast_tick":
            return [
                self.forecast(t, now, seq, provider=provider)
                for t, s in self.contracts.items()
                if s.open_time <= now <= s.observation_end_time + 5
                for provider in (
                    ("fixed", "options", "realized") if self.baselines else (self.config.provider,)
                )
            ]
        return []

    def forecast(self, ticker, now, tick_id=0, *, provider=None):
        c = replace(self.config, provider=provider) if provider else self.config
        spec = self.contracts[ticker]
        reference, known, missing, reasons = self.history.snapshot(spec, now)
        lineage = [self.contract_ids[ticker], tick_id]
        lineage += [
            seq
            for seq, r in self.reference_ids
            if r.received_time <= now
            and (now - max(305, c.realized_window + 5) <= r.source_time <= now or r.source_time in known)
        ]
        if self.option_id:
            lineage.append(self.option_id)
        if ticker in self.book_ids:
            lineage.append(self.book_ids[ticker])
        f = Forecast(
            ticker,
            now,
            self.mode,
            c.provider,
            digest(c),
            spec.rules_hash,
            sorted(set(lineage)),
            target=spec.target,
            series_ticker=spec.series_ticker,
            event_ticker=spec.event_ticker,
            lifecycle=spec.lifecycle,
            rules_url=spec.rules_url,
            contract_version=spec.version,
            observation_end_time=spec.observation_end_time,
            trading_close_time=spec.trading_close_time,
            discount=c.discount,
            remaining_seconds=max(0, spec.observation_end_time - now),
            known_samples=len(known),
            expected_samples=len(spec.sample_times),
            missing_samples=missing,
        )
        f.quotes = self.books.get(ticker, Book()).quotes(now, c.book_max_age)
        f.input_ages["metadata"] = now - spec.metadata_received
        if not spec.synthetic and now - spec.metadata_received > 60:
            reasons.append("STALE_METADATA")
        if not spec.synthetic and spec.lifecycle not in (
            "active",
            "open",
            "closed",
            "determined",
            "finalized",
            "settled",
        ):
            reasons.append("UNSUPPORTED_MARKET_LIFECYCLE")
        f.verification_readiness = "VERIFIED" if spec.verified else "UNRESOLVED"
        f.verification_details = spec.verification
        if spec.target is None:
            reasons.append("MISSING_TARGET")
        if reference:
            f.reference = asdict(reference)
            f.input_ages.update(
                reference=now - reference.source_time, reference_receipt=now - reference.received_time
            )
        complete = bool(spec.sample_times) and len(known) == len(spec.sample_times) and spec.verified
        if not complete:
            if reference is None:
                reasons.append("NO_REFERENCE")
            elif (
                now - reference.source_time > c.reference_max_age
                or now - reference.received_time > c.reference_max_age
            ):
                reasons.append("STALE_REFERENCE")
            if reference and reference.source != "kalshi-cfbenchmarks-1hz" and not spec.synthetic:
                reasons.append("NONOFFICIAL_REFERENCE")
            if "reference" in self.flags:
                reasons.append(self.flags["reference"])
        f.input_readiness = "INVALID" if reasons else "VALID"
        research_rules_known = spec.synthetic or all(
            spec.verification.get("checks", {}).get(k) == "verified"
            for k in ("primary_template", "comparison", "observation_times", "reference")
        )
        if (
            not reasons
            and reference
            and spec.target is not None
            and not spec.verified
            and research_rules_known
        ):
            diagnostic_vol = select(c.provider, self.history, self.options, spec, reference, now, c)
            f.volatility = diagnostic_vol
            sigma = diagnostic_vol.get("sigma")
            if sigma is not None:
                f.research_diagnostic = dict(
                    label="ASSUMPTION-BASED TERMINAL RESEARCH; UNCALIBRATED; NOT SETTLEMENT PROBABILITY",
                    assumptions=["terminal price, no averaging or settlement rounding", "zero carry", "GBM"],
                    result=terminal(
                        reference.value,
                        spec.target,
                        max(0, spec.observation_end_time - reference.source_time),
                        sigma,
                        comparison=spec.comparison,
                        discount=c.discount,
                    ),
                )
                if spec.research_tie_policy == "user-model-lean-v1" and set(spec.unresolved) == {
                    "ROUNDING_TIE_UNSPECIFIED"
                }:
                    f.research_settlement = research_settlement(
                        spec,
                        reference,
                        known,
                        now,
                        sigma,
                        paths=c.paths,
                        seed=c.seed,
                        requested_half_width=c.requested_half_width,
                    )
            else:
                f.input_readiness = "NO_VOLATILITY"
        if not spec.verified:
            reasons += list(spec.unresolved) or ["UNVERIFIED_RULES"]
        if reasons:
            f.reasons = sorted(set(reasons))
            f.explanation = "Settlement estimate unavailable: " + ", ".join(f.reasons)
            return f
        if complete:
            sigma = 0
            f.volatility = dict(provider="not-required-deterministic", sigma=None)
            f.probability_kind = "deterministic"
        else:
            f.volatility = select(c.provider, self.history, self.options, spec, reference, now, c)
            sigma = f.volatility.get("sigma")
            if sigma is None:
                f.input_readiness = "NO_VOLATILITY"
                f.reasons = f.volatility.get("reasons", ["NO_VOLATILITY"])
                f.explanation = "Required volatility unavailable: " + ", ".join(f.reasons)
                return f
            f.probability_kind = "uncalibrated_forecast" if c.provider == "realized" else "pricing_proxy"
            elapsed_horizon = max(0, spec.observation_end_time - reference.source_time)
            f.integrated_variance = sigma * sigma * elapsed_horizon / YEAR
            f.terminal = terminal(
                reference.value,
                spec.target,
                elapsed_horizon,
                sigma,
                comparison=spec.comparison,
                discount=c.discount,
            )
        kwargs = dict(paths=c.paths, seed=c.seed, requested_half_width=c.requested_half_width)
        f.settlement = simulate(spec, reference, known, now, sigma, **kwargs)
        f.raw_p_yes, f.raw_p_no = f.settlement["p_yes"], f.settlement["p_no"]
        f.display_probability = f.raw_p_yes
        payout = float(spec.payout)
        f.model_value_yes = payout * c.discount * f.raw_p_yes
        f.model_value_no = payout * c.discount * f.raw_p_no
        f.display_cents = f.model_value_yes * 100
        f.readiness = "DETERMINISTIC" if complete else "AVAILABLE"
        f.model_readiness = "DETERMINISTIC" if complete else "RAW_UNCALIBRATED"
        f.probability_kind = "deterministic" if complete else "uncalibrated_forecast"
        if not complete:
            values = [
                simulate(spec, reference, known, now, sigma * factor, **kwargs)["p_yes"]
                for factor in (1 - c.sensitivity_fraction, 1 + c.sensitivity_fraction)
            ]
            f.sensitivity = dict(
                p_yes_range=[min(values + [f.raw_p_yes]), max(values + [f.raw_p_yes])],
                sigma_range=[sigma * (1 - c.sensitivity_fraction), sigma * (1 + c.sensitivity_fraction)],
                meaning="parameter sensitivity; not a statistical confidence interval",
            )
            if not f.settlement["precision_met"]:
                f.reasons.append("NUMERICAL_PRECISION_TARGET_UNMET")
        for key, value, quote in [
            ("yes_minus_ask", f.model_value_yes, "yes_ask"),
            ("no_minus_ask", f.model_value_no, "no_ask"),
            ("yes_minus_midpoint", f.model_value_yes, "midpoint"),
        ]:
            q = f.quotes[quote]
            f.comparisons[key] = None if q is None else value - float(q)
        distance = None if reference is None else float(reference.value) - float(spec.target)
        f.explanation = (
            f"{f.known_samples}/{f.expected_samples} settlement samples known; "
            f"{f.remaining_seconds:.1f}s remaining. "
            f"Reference minus target: ${distance:.2f}. "
            if distance is not None
            else ""
        )
        f.explanation += (
            f"Settlement mean ${f.settlement['mean']:.2f}, "
            f"standard deviation ${f.settlement['std']:.2f}; "
            f"raw YES {100 * f.raw_p_yes:.2f}%. "
        )
        f.explanation += (
            "Exact recorded settlement comparison."
            if complete
            else (
                f"Annualized volatility {100 * sigma:.2f}%, {f.volatility['provider']}; "
                "zero arithmetic drift, constant variance rate. No fitted calibration."
            )
        )
        return f
