from yacron import config


def test_mergedicts():
	assert dict(config.mergedicts({"a": 1}, {"b": 2})) == {"a": 1, "b": 2}
