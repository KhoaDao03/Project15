def inputs(spot):
    return dict(
        reference=spot,
        rv_30=0.00001,
        rv_60=0.00001,
        ewma=0.00001,
        bleep=dict(
            atr=0.0,
            stoch_k=50.0,
            stoch_k_previous=50.0,
            bb_upper=spot + 50,
            bb_lower=spot - 50,
            bb_middle=spot,
        ),
    )
