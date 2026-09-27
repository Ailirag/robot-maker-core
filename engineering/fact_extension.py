"""Read-only compatibility of a historical view with a fact manifest that moved on.

Not an authority override, candidate re-pin or release verdict. What decides
compatibility is the set the consumer declares it consumed: a record it never read
cannot invalidate it, whether that record is new or was merely filled in later.
Neither is enumerated. Earlier versions demanded a list - first of every addition,
then of every update - which turned each new measurement into a code change and
refused the historical view until someone edited the list.

Refused are a change to a record the consumer did consume, the disappearance of any
record the snapshot had, and any change outside ``parameters``, gates and holds
included. A caller that declares no consumed set is refused outright: without it
there is nothing to protect and the check would wave everything through.
"""
import hashlib
import json

def verify_extension(original_bytes,original_sha,current,*,consumed_ids=()):
    consumed=set(consumed_ids)
    if not consumed:return ["NO_CONSUMED_CONTRACT"]
    if hashlib.sha256(original_bytes).hexdigest()!=original_sha:return ["SNAPSHOT_HASH"]
    original=json.loads(original_bytes)
    if not isinstance(current,dict) or not isinstance(current.get("parameters"),dict):
        return ["MANIFEST_SCHEMA"]
    before=original.get("parameters",{})
    after=current["parameters"]
    if set(before)-set(after):return ["EXISTING_FACT_REMOVED"]
    if not consumed <= set(before) & set(after):return ["CONSUMED_FACT_MISSING"]
    moved=sorted(fact_id for fact_id in consumed if before[fact_id]!=after[fact_id])
    if moved:return ["CONSUMED_FACT_CHANGED: "+", ".join(moved)]
    # Everything outside the fact table is authority, not measurement: gates, holds
    # and acceptance flags must stand exactly as the snapshot had them.
    return [] if dict(current,parameters=before)==dict(original,parameters=before) else ["AUTHORITY_CHANGED"]
