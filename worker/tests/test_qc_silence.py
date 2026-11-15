from clipper import qc


def test_silence_limit_from_mode():
    assert qc.silence_limit({"silence": "off"}) is None
    assert qc.silence_limit({"silence": "low"}) == 5.3
    assert qc.silence_limit({"silence": "medium"}) == 3.8
    assert qc.silence_limit({"silence": "high"}) == 2.6
    assert qc.silence_limit({}) == 2.0
    assert qc.silence_limit(None) == 2.0
