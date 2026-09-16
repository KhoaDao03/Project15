# Duplicate market discovery correction

On September 10, 2026, six markets in the 05:00–09:00 Eastern period were
quarantined after a valid strike-bearing market record was followed in the same
metadata batch by a copy with no strike. The discovery client concatenated
separate `open` and `unopened` status queries. These queries are not an atomic
snapshot, so a market transitioning to active could appear in both.

Discovery now groups results by ticker. When a ticker occurs more than once,
it requests `markets/{ticker}` with the series exchange index and uses that
single response. It validates the response identity and does not merge fields
from conflicting versions. An extra detail request is made only for overlaps.

The runner records both original candidates and the resolved detail response
in the raw metadata event. The engine also saves a `metadata_resolution` audit
record with reason `OVERLAPPING_MARKET_LISTS`.

Existing contract validation, quarantine, and recovery safeguards remain intact.
An incomplete or changed canonical response is still subject to those checks;
identity mismatches fail discovery. No historical quarantine, missed trade,
portfolio balance, or trade result is rewritten. Expired opportunities cannot
be traded retroactively.

Validation: 108 related regression tests passed, plus the final five API tests,
including an overlapping-list discovery-to-engine test that verifies a valid
market is admitted without quarantine and the resolution evidence is retained.
A read-only check of the live detail endpoint confirmed ticker, exchange index,
status, and strike fields. This does not claim that every future metadata
problem or skipped trade has been eliminated.
