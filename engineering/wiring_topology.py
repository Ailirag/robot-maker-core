"""Portable conductor/terminal topology and pin resource checks.

The independent requirement contract belongs to the project. This validates a
harness against it; it does not infer voltages, wire sizes or physical acceptance.
"""
from collections import defaultdict, deque

def _fail(message):
    raise ValueError(message)

def _text(value):
    return isinstance(value, str) and bool(value.strip())

def _rows(rows, label):
    if not isinstance(rows, list):
        _fail(label + ": expected list")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not _text(row.get("id")) or row["id"] in result:
            _fail(label + ": missing or duplicate id")
        result[row["id"]] = row
    return result

def verify_assignments(required, assignments, available, occupied):
    """Resource names may include controller identity; no board is hardcoded."""
    def resource(value):
        return type(value) is int or _text(value)
    if (not isinstance(assignments, dict) or not isinstance(occupied, dict)
            or not required or len(required) != len(set(required))
            or set(assignments) != set(required)
            or any(not resource(v) for v in [*assignments.values(), *occupied.values(), *available])
            or len(set(assignments.values())) != len(assignments)
            or set(assignments.values()) & set(occupied.values())
            or not set(assignments.values()) <= set(available)):
        _fail("PIN_CONFLICT: incomplete, shared, occupied or unavailable resource")
    return dict(status="PASS", physical_approval=False)

def verify(contract, harness):
    """Refuse omissions, wrong same-net terminals and disconnected branches."""
    if contract.get("schema") != "wiring-topology-v1":
        _fail("WIRE_CONTRACT: unsupported schema")
    stage = contract.get("stage")
    if not _text(stage) or harness.get("stage") != stage:
        _fail("WIRE_STAGE")
    ports = contract.get("terminals")
    if not isinstance(ports, dict) or not ports or any(not _text(k) or not _text(v) for k,v in ports.items()):
        _fail("WIRE_TERMINAL_CATALOGUE")
    expected = _rows(contract.get("required"), "WIRE_REQUIREMENTS")
    actual = _rows(harness.get("cables"), "WIRE_COVERAGE")
    unresolved = contract.get("unresolved", [])
    for row in _rows(unresolved, "WIRE_UNRESOLVED").values():
        if row.get("value", "missing") is not None or not _text(row.get("obtained_by")):
            _fail("WIRE_UNRESOLVED: null and obtained_by required")
    if not expected and not unresolved and not _text(contract.get("empty_reason")):
        _fail("WIRE_REQUIREMENTS: unexplained empty harness")
    if set(actual) != set(expected):
        _fail("WIRE_COVERAGE: missing or unexpected conductor")
    unused = contract.get("unused", [])
    if (not isinstance(unused, list) or len(unused) != len(set(unused))
            or not set(unused) <= set(ports)):
        _fail("WIRE_UNUSED")
    graph = defaultdict(set)
    def connect(a,b):
        if a == b or a not in ports or b not in ports:
            _fail("WIRE_TERMINAL")
        if ports[a] != ports[b]:
            _fail("WIRE_NET_MISMATCH")
        if a in unused or b in unused:
            _fail("WIRE_UNUSED_CONNECTED")
        graph[a].add(b); graph[b].add(a)
    pairs = set()
    for name, required in expected.items():
        a,b = required.get("source"),required.get("target")
        if not _text(a) or not _text(b):
            _fail("WIRE_TERMINAL")
        connect(a,b)
        row = actual[name]
        if (row.get("source"),row.get("target")) != (a,b):
            _fail("HEADER_ENDPOINT: " + name)
        if row.get("net") != ports[a]:
            _fail("WIRE_NET_MISMATCH: " + name)
        pair = tuple(sorted((a,b)))
        if pair in pairs:
            _fail("WIRE_DUPLICATE_ENDPOINTS: parallel conductors require a separate declared model")
        pairs.add(pair)
    for pair in contract.get("internal_links", []):
        if not isinstance(pair, list) or len(pair) != 2:
            _fail("WIRE_INTERNAL_LINK")
        connect(*pair)
    for group in contract.get("connected_groups", []):
        if (not isinstance(group, list) or len(group) < 2 or len(group) != len(set(group))
                or not set(group) <= set(ports)):
            _fail("WIRE_CONNECTIVITY: invalid terminal group")
        seen = {group[0]}; queue = deque(seen)
        while queue:
            for neighbor in graph[queue.popleft()] - seen:
                seen.add(neighbor); queue.append(neighbor)
        if not set(group) <= seen:
            _fail("WIRE_CONNECTIVITY: disconnected required terminals")
    return dict(status="ACTION_REQUIRED" if unresolved else "PASS",stage=stage,
                conductors=len(actual),unresolved=unresolved,physical_approval=False)
