# Settlement-lead confirmation

The settlement-average mean and standard deviation supply separate lead evidence. Standard
entries use the side favored by the current official reference; late entries use
the expected settlement average. A side change, missing second or incompatible
market specification resets confirmation history. Quotes and repeated samples
within a second do not create extra confirmations. Preloaded history cannot count
as a fresh confirmation.

The shared crypto presets require one fresh same-side sample in each entry window.
Their sigma floors are zero, so normalized distance is diagnostic. If configured,
`min_lead_sigma` and `late_min_lead_sigma` enforce a minimum margin divided by the
larger of one settlement unit and Bleep's settlement standard deviation.

There is no separate reversal-stress probability. Entry probability comes only
from the ATR-based Bleep finish estimate, capped against the book, and uses the standard/late floor in [active settings](ACTIVE_PAPER_SETTINGS.md).
Health, price, risk and execution checks remain independent requirements.
