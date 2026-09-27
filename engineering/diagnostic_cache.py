"""Reuse geometric diagnostics only for identical, located individual STEP bodies.

The complete assembly can change around the part. Every finding remains open;
neither model review decisions nor mechanical/physical approvals are cached here.
"""
import copy,hashlib,json,re,tempfile
from pathlib import Path
from engineering.delivery import read_bound
from engineering.geometry_feedback import METHOD_SCHEMAS

METHODS={"sections","walls","connections"}
CONTEXT=("part","scope","role","coordinate_system","method_options")

def _digest(raw):return hashlib.sha256(raw).hexdigest()

def _canonical(raw):
    header,sep,data=raw.partition(b"ENDSEC;")
    if not sep or b"HEADER;" not in header or b"DATA;" not in data:
        raise ValueError("CACHE: unsupported STEP envelope")
    header,count=re.subn(rb"(?m)^FILE_NAME\([\s\S]*?\);\r?\n",b"FILE_NAME();\n",header)
    if count!=1:raise ValueError("CACHE: ambiguous STEP metadata")
    data=re.sub(rb"(?m)^(#[0-9]+\s*=\s*NEXT_ASSEMBLY_USAGE_OCCURRENCE\()'[0-9]+'",
        rb"\1'EXPORT_OCCURRENCE'",data)
    return header+sep+data

def _part_image(raw,part):
    from build123d import import_step,export_step
    with tempfile.TemporaryDirectory(prefix="diagnostic-body-") as directory:
        base=Path(directory);source=base/"assembly.step";source.write_bytes(raw)
        assembly=import_step(source)
        matches=[s for s in assembly.children if s.label==part]
        if len(matches)!=1:raise ValueError("CACHE: missing or ambiguous part")
        shape=matches[0]
        if not shape.is_valid or len(shape.solids())!=1:raise ValueError("CACHE: invalid single body")
        shape=copy.deepcopy(shape.solids()[0]);shape.label=part
        target=base/"part.step";export_step(shape,target)
        return _canonical(target.read_bytes())

class GeometrySession:
    """Bounded, in-process STEP reuse; never trusts an unverified file read."""
    def __init__(self,root):
        self.root=Path(root).resolve()
        self._assemblies={}
        self._images={}

    def image(self,raw,part):
        from build123d import import_step,export_step
        digest=_digest(raw);key=(digest,part)
        if key in self._images:return self._images[key]
        if digest not in self._assemblies:
            # A comparison needs two revisions. Keep memory bounded even if a
            # caller supplies more; eviction affects performance, not validity.
            if len(self._assemblies)>=2:
                oldest=next(iter(self._assemblies))
                del self._assemblies[oldest]
                self._images={k:v for k,v in self._images.items() if k[0]!=oldest}
            with tempfile.TemporaryDirectory(prefix="diagnostic-assembly-") as directory:
                source=Path(directory)/"assembly.step";source.write_bytes(raw)
                self._assemblies[digest]=import_step(source)
        matches=[s for s in self._assemblies[digest].children if s.label==part]
        if len(matches)!=1:raise ValueError("CACHE: missing or ambiguous part")
        shape=matches[0]
        if not shape.is_valid or len(shape.solids())!=1:
            raise ValueError("CACHE: invalid single body")
        shape=copy.deepcopy(shape.solids()[0]);shape.label=part
        with tempfile.TemporaryDirectory(prefix="diagnostic-body-") as directory:
            target=Path(directory)/"part.step";export_step(shape,target)
            result=_canonical(target.read_bytes())
        self._images[key]=result
        return result

def reuse_part(root,receipt_binding,new_input,*,expected_identity,session=None):
    if session is not None and session.root!=Path(root).resolve():
        raise ValueError("CACHE: session root differs")
    receipt=json.loads(read_bound(root,receipt_binding))
    old_input=receipt.get("input",{})
    if (receipt.get("identity")!=expected_identity or
            receipt.get("status") not in {"SCREENED","REVIEW_REQUIRED"} or
            receipt.get("coverage",{}).get("complete") is not True or
            receipt.get("physical_approval") is not False or receipt.get("strength_n") is not None or
            receipt.get("geometry",{}).get("valid") is not True or receipt.get("geometry",{}).get("solids")!=1 or
            set(receipt.get("methods",{}))!=METHODS or
            any(m.get("complete") is not True for m in receipt.get("methods",{}).values())):
        raise ValueError("CACHE: incomplete, approved or stale diagnostic")
    findings=[]
    for method,item in receipt["methods"].items():
        report=item.get("report")
        if (not isinstance(report,dict) or report.get("schema")!=METHOD_SCHEMAS[method] or
                not isinstance(report.get("findings"),list) or
                (method!="sections" and report.get("coverage",{}).get("complete") is not True)):
            raise ValueError("CACHE: missing or incomplete method report")
        findings+=report["findings"]
    if (receipt["status"]=="SCREENED")!=(not findings):
        raise ValueError("CACHE: status differs from findings")
    if (not isinstance(new_input,dict) or not isinstance(new_input.get("part"),str) or
            any(old_input.get(k)!=new_input.get(k) for k in CONTEXT) or
            receipt.get("method_options",{})!=old_input.get("method_options",{})):
        raise ValueError("CACHE: analysis context changed")
    # Adapter changes may change what is selected or how policy is interpreted.
    if old_input.get("adapter")!=new_input.get("adapter"):
        raise ValueError("CACHE: scope adapter changed")
    if new_input.get("adapter"):read_bound(root,new_input["adapter"])
    for entry in (old_input,new_input):
        for binding in entry.get("inventory",[]):read_bound(root,binding)
    image=_part_image if session is None else session.image
    before=image(read_bound(root,old_input["step"]),new_input["part"])
    after=image(read_bound(root,new_input["step"]),new_input["part"])
    if before!=after:raise ValueError("CACHE: individual geometry changed; recompute")
    result=copy.deepcopy(receipt);result["input"]=copy.deepcopy(new_input)
    result["reuse_evidence"]=dict(source_receipt=copy.deepcopy(receipt_binding),
        old_assembly=copy.deepcopy(old_input["step"]),new_assembly=copy.deepcopy(new_input["step"]),
        identical_body_sha256=_digest(before),verifier_sha256=_digest(Path(__file__).read_bytes()),
        ignored_records="Exported STEP FILE_NAME header and numeric occurrence labels only; units, topology, location and coordinates remain exact.",
        physical_approval=False)
    # Binding reads supplied the exact bytes imported above; recheck external
    # inputs before returning a record to its immutable writer.
    for binding in (receipt_binding,old_input["step"],new_input["step"]):read_bound(root,binding)
    return result
