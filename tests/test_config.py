from yacron import config


def test_mergedicts():
	assert dict(config.mergedicts({"a": 1}, {"b": 2})) == {"a": 1, "b": 2}


def test_mergedicts_nested():
	assert dict(config.mergedicts(
	    {"a": {'x': 1, 'y': 2, 'z': 3}},
	    {'a': {'y': 10}, "b": 2})) == \
		{"a": {'x': 1, 'y': 10, 'z': 3}, "b": 2}


def test_mergedicts_right_none():
	assert dict(config.mergedicts(
	    {"a": {'x': 1}},
	    {"a": None, "b": 2})) == \
		{"a": {'x': 1}, "b": 2}


def test_mergedicts_lists():
	assert dict(config.mergedicts(
	    {"env": [{'key': 'FOO'}]},
	    {"env": [{'key': 'BAR'}]})) \
		== \
		{"env": [{'key': 'FOO'}, {'key': 'BAR'}]}
