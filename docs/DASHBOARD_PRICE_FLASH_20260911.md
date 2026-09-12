# Bitcoin reference display flashing — September 11, 2026

The price renderer treated a reference timestamp more than 500 ms ahead of the
dashboard host as invalid and replaced the number with an em dash. In a 30-second
live capture, source timestamps led their local receipt timestamps by 571–665 ms.
The samples repeatedly aged through the 500 ms display cutoff, then the next
sample crossed it again. This describes the relative timestamp difference; it
does not establish which clock differs from UTC.

Of 527 dashboard stream events, 141 hid the price and there were 169 transitions
between visible and hidden. Every rejection came from the source-time cutoff.
Receipt ages stayed below 430 ms and projection ages below 212 ms. The collector
was READY, so these flashes did not indicate repeated collector disconnects.

The browser now permits at most two seconds of source clock lead, matching the
normal/current collector clock tolerance. Projection and local receipt ages must
still be nonnegative and less than two seconds; source age must remain less than
two seconds. Missing/invalid values, larger future offsets, stale data, disconnects
and the browser stream watchdog still clear the price. Trading freshness, sequence,
risk and recovery checks are unchanged.

The price text is also replaced only when its formatted value changes. Replaying
the same 527 captured events through the actual JavaScript handler produced zero
hidden prices and zero visibility transitions; text replacements fell from 527
to 134. The regression reproduced the old dash/number failure and now passes,
including stale data, disconnect, watchdog and entry-blocked display cases.
JavaScript syntax, test lint/format and whitespace checks passed.

The running dashboard was verified to serve the corrected static asset. Neither
service was restarted; the collector and dashboard retained PIDs 129477 and 129478.
An already-open browser tab needs a reload to load the new JavaScript. Evidence
is retained under `data/runtime/dashboard-flash-20260911/`.
