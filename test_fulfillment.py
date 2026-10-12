from fulfillment import should_grant

def test_success():
    assert should_grant({"status":"succeeded"}) is True

def test_non_success():
    assert should_grant({"status":"pending"}) is False
    assert should_grant({"status":"failed"}) is False
