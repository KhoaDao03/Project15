# Entry economics reports

New evaluations and submitted-order records include `entry_economics` (`both-leg-v1`). The dashboard shows these under **Decision details → Entry cost scenarios**, and in opportunity replay. Historical records are not backfilled.

These are reporting scenarios. Entry acceptance, risk sizing, the adjusted 70% probability exit, and the stop-confirmation observer are unchanged.

For quantity q, selected-side entry probability p, entry price a, entry allowance s, and quantity-aware buy fee Fb, the entry debit is C = q(a+s)+Fb.

| Scenario | Net calculation |
| --- | --- |
| Settlement EV | p q − C; no settlement exit fee |
| Sale at target t | q t − sell fee(t,q) − C |
| Target-sale probability proxy | p [q t − sell fee(t,q)] − C |
| Sale at stop threshold z | q z − sell fee(z,q) − C |
| Immediate unwind | Current bid-depth proceeds − sell fees − C |

The target proxy assumes settlement winners sell at the target and losers pay zero. It is a sensitivity calculation, **not a forecast of the strategy's dynamic exits**. Its exit-fee column shows the fee on the winning sale; the proxy weights that fee by p. Target and stop scenarios use supported ticks (target rounded up, stop down). The stop scenario assumes full execution at that price: future gaps, latency and liquidity can produce larger losses. It is not a loss cap.

Fees use the existing execution fee accumulator, intended quantity and configured account precision, assuming taker execution on both legs. Each assumed leg is one fill; immediate unwind shares fee rounding across its depth fills as one order. Actual passive fills or partial fills can produce different fees. Evaluation quantity previews current risk sizing; it does not mean the entry was accepted. Zero permitted quantity is reported as unavailable.

The evaluation includes its existing entry slippage allowance once. The submitted-order report instead uses the actual quantity and selected limit price, with zero added allowance: the limit already bounds the entry price. Neither is an actual-fill result. No additional slippage is subtracted from sale prices. Immediate unwind includes spread through actual bids, reports insufficient depth without inventing a full-position result, and requires a fresh valid book. The separate execution stress haircut remains separate from these reports and realized P&L.

The existing entry-filter EV retains its previous conservative per-contract fee bound. The new settlement report uses quantity-aware fees, so the values may differ slightly. Neither target nor stop scenarios become acceptance gates in this update.
