# Sustained lead and late entries

The active v5 paper configuration enables shared sustained-lead checks and a late
settlement entry path. These are testing parameters, not calibrated guarantees.

| Rule | Standard entry | Late settlement entry |
| --- | --- | --- |
| Seconds remaining | 120 < remaining <= 600 | 15 < remaining <= 120 |
| Minimum settlement lead / modeled standard deviation | 1 | 2 |
| Confirmation | Three consecutive official one-second reference samples | Five consecutive official one-second reference samples |
| Side selection | Latest reference relative to strike | Expected settlement average relative to strike |

The lead is the expected settlement average's distance from the strike in the
chosen direction, divided by modeled settlement uncertainty (with a one-cent
floor). It accounts for the contract's comparison operator. Confirmation counts
distinct consecutive source seconds, not book updates or repeated evaluations.
A reference gap, side change, specification change or unavailable model resets
confirmation. Restarting also requires rebuilding confirmation.

With entry probability deductions enabled, both paths repeat the largest adverse one-second reference move observed
in approximately the preceding minute, then rerun the settlement simulation
from that shifted price with the original volatility. Samples already observed
inside the settlement window remain fixed. Entry pricing uses the lower of the
original and stressed conservative probabilities, with the existing disagreement
penalty, fee and slippage deductions. The minimum probability is 80% for standard entries and 85% for late entries.
The minimum purchase price is $0.70 and minimum net edge/EV is one cent.
If no adverse move was observed, the price shift is zero; modeled volatility
still applies. This stress is a scenario, not an estimated worst-case bound.

Late entries also expose the remaining reference average needed to reach the
strike, before settlement rounding, as a diagnostic. They cannot override missing
settlement samples, stale data, liquidity, shock or risk checks. Orders share the
same position and attempt limits, and revalidate against newly received reference
data before a fill. An order cannot fill at or below 15 seconds remaining.
Crossing into the late window requires its stronger confirmation.

Decisions and order evidence record `entry_path` and lead diagnostics. Analytics
include late-window predictions and group calibration and P&L by entry path.
The dashboard displays confirmation, normalized lead, stress probability and
settlement progress. Exit rules are unchanged; hold-value exits remain disabled
in this paper configuration.

The standard path may reject more signals because of the new checks; the late
window adds opportunities. Neither higher fill counts nor better returns are
established until evaluated on subsequent paper results. Historical configurations
keep their original behavior and hashes because the new checks default off.

Late probability and confirmation overrides use zero to inherit the standard
thresholds in historical configurations. The active configuration explicitly sets
85% and five samples, retaining stricter late-entry requirements.

## Active v5 raw-probability entries

`entry_probability_deductions=false` selects raw `p_yes`/`p_no` for entry floors,
pricing, net value and fill revalidation. None of the fixed calibration, simulation,
rounding, reversal-stress or disagreement deductions reduce the entry estimate.
Stress remains diagnostic and cannot veto submission. Lead magnitude and sample
confirmation remain required. Fees and slippage are still deducted from net value.
Exits retain their existing probability calculation. The option defaults true to
preserve historical behavior and configuration hashes.
