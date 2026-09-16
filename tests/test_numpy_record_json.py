import json

import numpy as np

from btc15.domain import dumps


def test_model_numpy_integer_can_be_published_without_precision_loss():
    value = np.int64(9223372036854775807)
    result = json.loads(dumps({'evaluation': {'count': value}}))
    assert result['evaluation']['count'] == int(value)
