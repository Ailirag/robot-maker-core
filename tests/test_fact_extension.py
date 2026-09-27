"""A historical view stays valid while the facts it read stand still.

Read-only compatibility, never an authority override or a release verdict. What
decides it is the set the consumer declares it consumed: a record it never read
cannot invalidate it, whether that record is new or merely filled in later, and
neither is enumerated. Enumerating them made every future measurement a code
change, which is what the rule was supposed to avoid. Refused are a change to a
consumed record, the disappearance of any record the snapshot had, and any change
outside `parameters`, gates and holds included.
"""
import copy, hashlib, json
from engineering.fact_extension import verify_extension


def test_a_record_the_consumer_never_read_may_be_filled_in_later():
    """A null the owner measures afterwards is the ordinary case, not an exception."""
    old = {"parameters": {"used": {"value": 4}, "unused": {"value": None}}, "holds": {"power": True}}
    raw = json.dumps(old).encode(); sha = hashlib.sha256(raw).hexdigest()
    new = copy.deepcopy(old); new["parameters"]["unused"] = {"value": 8.02}
    assert not verify_extension(raw, sha, new, consumed_ids=["used"])
    new["holds"]["power"] = False
    assert verify_extension(raw, sha, new, consumed_ids=["used"])


def test_a_consumed_record_may_not_move():
    old = {"parameters": {"used": {"value": 4}, "unused": {"value": None}}}
    raw = json.dumps(old).encode(); sha = hashlib.sha256(raw).hexdigest()
    new = copy.deepcopy(old); new["parameters"]["used"]["value"] = 5
    assert verify_extension(raw, sha, new, consumed_ids=["used"])


def test_an_empty_consumed_set_closes_nothing():
    """Without a declared consumed set there is nothing to protect, so nothing passes."""
    old = {"parameters": {"used": {"value": 4}}}
    raw = json.dumps(old).encode(); sha = hashlib.sha256(raw).hexdigest()
    assert verify_extension(raw, sha, copy.deepcopy(old))
    assert verify_extension(raw, sha, copy.deepcopy(old), consumed_ids=[])


def test_extension_preserves_every_old_value_and_gate():
    old = {"parameters": {"old": {"value": 4}}, "holds": {"power": True}}
    raw = json.dumps(old).encode(); sha = hashlib.sha256(raw).hexdigest()
    new = copy.deepcopy(old); new["parameters"]["added"] = {"value": 34.45}
    assert not verify_extension(raw, sha, new, consumed_ids=["old"])
    new["holds"]["power"] = False
    assert verify_extension(raw, sha, new, consumed_ids=["old"])
    new = copy.deepcopy(old); new["parameters"]["old"]["value"] = 5
    assert verify_extension(raw, sha, new, consumed_ids=["old"])


def test_any_number_of_new_records_is_compatible_and_none_is_enumerated():
    """The snapshot's reader could not have read a record that did not exist."""
    old = {"parameters": {"old": {"value": 4}}}
    raw = json.dumps(old).encode(); sha = hashlib.sha256(raw).hexdigest()
    new = copy.deepcopy(old)
    for i in range(10):
        new["parameters"][f"measured_later_{i}_mm"] = {"value": float(i), "unit": "mm"}
    assert not verify_extension(raw, sha, new, consumed_ids=["old"])


def test_a_removed_record_is_not_an_extension():
    old = {"parameters": {"old": {"value": 4}, "other": {"value": 1}}}
    raw = json.dumps(old).encode(); sha = hashlib.sha256(raw).hexdigest()
    new = {"parameters": {"old": {"value": 4}}}
    assert verify_extension(raw, sha, new, consumed_ids=["old"])


def test_extension_requires_the_exact_snapshot():
    raw = b'{"parameters":{"used":{"value":1}}}'
    assert verify_extension(raw, "0" * 64, json.loads(raw), consumed_ids=["used"])


def test_a_consumed_fact_must_exist_in_both_images():
    old = {"parameters": {"used": {"value": 4}}}
    raw = json.dumps(old).encode(); sha = hashlib.sha256(raw).hexdigest()
    new = {"parameters": {"used": {"value": 4}, "fresh": {"value": 1}}}
    assert verify_extension(raw, sha, new, consumed_ids=["absent_everywhere"])
