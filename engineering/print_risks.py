"""Geometric print-risk screening and local sliced-support evidence.

This is a review gate, not a strength solver or a print-success certificate.
All tolerances below are screening policy; they are not measured material limits.
"""
from __future__ import annotations
from collections import defaultdict
from functools import lru_cache
import copy, hashlib, json, math, re, zipfile, io
from pathlib import Path
import xml.etree.ElementTree as ET
from engineering.print_package import inspect_project, mesh_report
from engineering.slicer_mass import MODEL_ROLES, WASTE_ROLES

SCHEMA="print-risk-report-v1"
POLICY=dict(bed_tolerance_mm=.05,downward_angle_deg=45.,angle_roundoff_deg=.0001,min_region_area_mm2=1.,
            low_contact_ratio=.10,arc_chord_mm=.2,xy_evidence_margin_mm=.5,
            support_z_tolerance_mm=.12)
RISK_ROLES=("Bridge","Overhang wall","Floating vertical shell")
SUPPORT_ROLES=("Support","Support interface","Support transition")

def _hash(raw):return hashlib.sha256(raw).hexdigest()
def _bounds(points):
    return [[min(p[i] for p in points) for i in range(3)],
            [max(p[i] for p in points) for i in range(3)]]

def geometry_risks(mesh):
    checked=mesh_report(mesh);vs=mesh["vertices"];candidates={};contact=0.
    for i,t in enumerate(mesh["triangles"]):
        a,b,c=(vs[j] for j in t)
        u=[b[k]-a[k] for k in range(3)];v=[c[k]-a[k] for k in range(3)]
        cross=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]]
        norm=math.sqrt(sum(x*x for x in cross))
        # The inclusive 45-degree boundary must survive decimal mesh export.
        # This numerical band is not a material overhang capability.
        angle=POLICY["downward_angle_deg"]+POLICY["angle_roundoff_deg"]
        if cross[2]>=-norm*math.sin(math.radians(angle)):continue
        if max(vs[j][2] for j in t)<=POLICY["bed_tolerance_mm"]:
            contact+=-cross[2]/2
        else:candidates[i]=(-cross[2]/2,t)
    by_edge=defaultdict(list)
    for i,(_,t) in candidates.items():
        for a,b in zip(t,t[1:]+t[:1]):by_edge[tuple(sorted((a,b)))].append(i)
    neighbors=defaultdict(set)
    for rows in by_edge.values():
        for i in rows:neighbors[i].update(rows)
    unseen=set(candidates);regions=[]
    while unseen:
        todo=[unseen.pop()];group=set(todo)
        while todo:
            new=neighbors[todo.pop()]&unseen;unseen-=new;group|=new;todo+=sorted(new)
        area=sum(candidates[i][0] for i in group)
        if area<POLICY["min_region_area_mm2"]:continue
        points=[vs[j] for i in group for j in candidates[i][1]]
        regions.append(dict(projected_area_mm2=round(area,5),bounds_mm=_bounds(points)))
    regions.sort(key=lambda r:(r["bounds_mm"][0][2],r["bounds_mm"][0][0],r["bounds_mm"][0][1]))
    for i,r in enumerate(regions):r["id"]=f"region-{i+1}"
    lo,hi=checked["bounds_mm"];footprint=(hi[0]-lo[0])*(hi[1]-lo[1])
    ratio=contact/footprint;risks=[]
    if regions:risks.append("elevated_underside")
    if contact<=1e-8:risks.append("no_flat_bed_contact")
    elif ratio<POLICY["low_contact_ratio"]:risks.append("small_bed_contact")
    return dict(name=mesh["name"],status="REVIEW_REQUIRED" if risks else "NO_SCREENING_FINDINGS",
        risks=risks,bed_contact_mm2=round(contact,5),bed_contact_ratio=round(ratio,6),
        bounds_mm=checked["bounds_mm"],regions=regions)

def toolpaths(text,*,default_object=None):
    """Compatibility list for small consumers; scanners use iter_toolpaths."""
    return list(iter_toolpaths(text,default_object=default_object))


def iter_toolpaths(lines,*,default_object=None):
    """Actual positive deposition, streamed from text or an iterable of lines."""
    xyz=[None]*3;absolute=True;eabs=True;e=0.;debt=0.;role="Custom";obj=default_object;plane="G17"
    if isinstance(lines,str):lines=io.StringIO(lines)
    for line in lines:
        line=line.strip()
        if line.startswith("; FEATURE:"):role=line.split(":",1)[1].strip();continue
        start=re.fullmatch(r"; start printing object, unique label id: (\d+)",line)
        if start:obj=start[1];continue
        if line.startswith("; stop printing object"):obj=None;continue
        words=line.split(";",1)[0].split()
        if not words:continue
        cmd=words[0].upper()
        values={k:float(v) for k,v in re.findall(r"([XYZEFIJKR])([-+]?(?:\d+(?:\.\d*)?|\.\d+))"," ".join(words[1:]).upper())}
        if not all(math.isfinite(v) for v in values.values()):raise ValueError("PRINTABILITY: nonfinite G-code")
        if cmd in {"G20","M200"}:raise ValueError("PRINTABILITY: unsupported units/extrusion")
        if cmd in {"G17","G18","G19"}:plane=cmd
        if cmd=="G90":absolute=True;eabs=True
        if cmd=="G91":absolute=False;eabs=False
        if cmd=="M82":eabs=True
        if cmd=="M83":eabs=False
        if cmd=="G92":
            for i,a in enumerate("XYZ"):
                if a in values:xyz[i]=values[a]
            if "E" in values:e=values["E"]
        if cmd not in {"G0","G1","G2","G3"}:continue
        old=list(xyz)
        for i,a in enumerate("XYZ"):
            if a in values:xyz[i]=values[a] if absolute else None if xyz[i] is None else xyz[i]+values[a]
        delta=0.
        if "E" in values:
            target=values["E"] if eabs else e+values["E"];delta=target-e;e=target
        if delta<0:debt-=delta;continue
        recovered=min(delta,debt);debt-=recovered;delta-=recovered
        if delta<=0 or role=="Custom":continue
        if role not in MODEL_ROLES|WASTE_ROLES:raise ValueError("PRINTABILITY: unknown feature "+role)
        if any(v is None for v in old+xyz):raise ValueError("PRINTABILITY: unknown deposition coordinate")
        points=[old,list(xyz)]
        if cmd in {"G2","G3"}:
            if plane!="G17" or "R" in values or not {"I","J"}<=values.keys():
                raise ValueError("PRINTABILITY: unsupported arc")
            cx=old[0]+values["I"];cy=old[1]+values["J"];radius=math.hypot(values["I"],values["J"])
            if radius<=0 or abs(math.hypot(xyz[0]-cx,xyz[1]-cy)-radius)>max(.02,radius*.001):
                raise ValueError("PRINTABILITY: invalid arc")
            a=math.atan2(old[1]-cy,old[0]-cx);b=math.atan2(xyz[1]-cy,xyz[0]-cx)
            sweep=(b-a)%(2*math.pi) if cmd=="G3" else -((a-b)%(2*math.pi))
            if abs(sweep)<1e-12:sweep=2*math.pi if cmd=="G3" else -2*math.pi
            n=max(1,math.ceil(abs(sweep)*radius/POLICY["arc_chord_mm"]))
            points=[old]+[[cx+radius*math.cos(a+sweep*j/n),cy+radius*math.sin(a+sweep*j/n),
                old[2]+(xyz[2]-old[2])*j/n] for j in range(1,n)]+[list(xyz)]
        for a,b in zip(points,points[1:]):
            length=math.dist(a,b)
            if length>1e-8:yield dict(a=a,b=b,role=role,object=obj,length_mm=length)

def _clip_length(a,b,lo,hi):
    left=0.;right=1.
    for k in range(2):
        delta=b[k]-a[k]
        if abs(delta)<1e-12:
            if not lo[k]<=a[k]<=hi[k]:return 0.
        else:
            u=(lo[k]-a[k])/delta;v=(hi[k]-a[k])/delta
            left=max(left,min(u,v));right=min(right,max(u,v))
            if right<=left:return 0.
    return math.dist(a,b)*(right-left)

class _SupportEvidence:
    def __init__(self,region,*,top_gap_mm,layer_height_mm,initial_layer_height_mm=None):
        if not isinstance(top_gap_mm,(int,float)) or not math.isfinite(top_gap_mm) or top_gap_mm<0:
            raise ValueError("PRINTABILITY: invalid support gap")
        self.lo,self.hi=region["bounds_mm"];margin=POLICY["xy_evidence_margin_mm"]
        self.bottom=[self.lo[0]-margin,self.lo[1]-margin]
        self.top=[self.hi[0]+margin,self.hi[1]+margin]
        self.gap=top_gap_mm;self.layer=layer_height_mm
        self.lengths=defaultdict(float);self.heights=set()
        if initial_layer_height_mm is not None and (
                type(initial_layer_height_mm) not in (int,float)
                or not math.isfinite(initial_layer_height_mm) or initial_layer_height_mm<=0):
            raise ValueError("PRINTABILITY: resolved positive initial layer height required")
        self.initial=initial_layer_height_mm
        self.in_first=(self.initial is not None and self.lo[2]>=-1e-4
                       and self.hi[2]<=self.initial+1e-4)
        self.first_length=0.

    def add(self,s):
        if (self.in_first and s["role"] in MODEL_ROLES
                and all(abs(p[2]-self.initial)<=1e-4 for p in (s["a"],s["b"]))):
            # No XY expansion: nearby brim/model paths cannot explain this face.
            self.first_length+=_clip_length(s["a"],s["b"],self.lo,self.hi)
        if s["role"] not in (*SUPPORT_ROLES,*RISK_ROLES):return
        z=(s["a"][2]+s["b"][2])/2
        if z>self.hi[2]+2*self.layer+1e-4:return
        length=_clip_length(s["a"],s["b"],self.bottom,self.top)
        if length<=0:return
        if s["role"] in RISK_ROLES:
            key="bridge_length_mm" if s["role"]=="Bridge" else "overhang_length_mm"
            if z>=self.lo[2]-.05:self.lengths[key]+=length
            return
        if z>self.hi[2]+1e-4:return
        self.lengths["below_length_mm"]+=length
        if (s["role"]=="Support interface" and
            self.lo[2]-self.gap-POLICY["support_z_tolerance_mm"]<=z<=self.hi[2]+1e-4):
            self.lengths["interface_length_mm"]+=length;self.heights.add(round(z,5))

    def finish(self):
        result=dict(**{key:round(self.lengths[key],4) for key in
            ("below_length_mm","interface_length_mm","bridge_length_mm","overhang_length_mm")},
            interface_z_mm=sorted(self.heights),
            method="deposited support clipped to region XY bounds; local evidence, not full-area coverage")
        if self.initial is not None:
            result["first_layer"]=dict(height_mm=self.initial,bounds_within_layer=self.in_first,
                local_model_length_mm=round(self.first_length,4))
        return result


def support_evidence(region,segments,*,top_gap_mm,layer_height_mm=.2,initial_layer_height_mm=None):
    """Local interface below the XY bounds, not proof of full footprint coverage."""
    result=_SupportEvidence(region,top_gap_mm=top_gap_mm,layer_height_mm=layer_height_mm,
                            initial_layer_height_mm=initial_layer_height_mm)
    for segment in segments:result.add(segment)
    return result.finish()


def _add_summary(roles,s):
    r=roles.setdefault(s["role"],dict(length_mm=0.,z_min_mm=s["a"][2],z_max_mm=s["a"][2]))
    r["length_mm"]+=s["length_mm"]
    r["z_min_mm"]=min(r["z_min_mm"],s["a"][2],s["b"][2])
    r["z_max_mm"]=max(r["z_max_mm"],s["a"][2],s["b"][2])


def _finish_summary(roles):
    return dict(roles={key:dict(row,length_mm=round(row["length_mm"],4)) for key,row in roles.items()})


def _summary(segments):
    roles={}
    for s in segments:_add_summary(roles,s)
    return _finish_summary(roles)


def toolpath_evidence(segments,identities,regions,*,top_gap_mm,layer_height_mm=.2,initial_layer_height_mm=None):
    """Single-pass local evidence; retain aggregates, never the deposition list."""
    if (not identities or len(set(identities.values()))!=len(identities)
        or set(identities.values())!=set(regions)):
        raise ValueError("PRINTABILITY: ambiguous object inventory")
    supports={};roles={name:{} for name in regions};seen=set()
    for name,rows in regions.items():
        if len({r["id"] for r in rows})!=len(rows):
            raise ValueError("PRINTABILITY: repeated region identity")
        supports[name]={r["id"]:_SupportEvidence(r,top_gap_mm=top_gap_mm,layer_height_mm=layer_height_mm,
                        initial_layer_height_mm=initial_layer_height_mm) for r in rows}
    for s in segments:
        if s["object"] not in identities:
            if s["role"] in MODEL_ROLES|set(SUPPORT_ROLES):
                raise ValueError("PRINTABILITY: deposition has no known object")
            continue
        name=identities[s["object"]];seen.add(name);_add_summary(roles[name],s)
        for region in supports[name].values():region.add(s)
    if seen!=set(regions):raise ValueError("PRINTABILITY: toolpath object inventory differs")
    return {name:dict(toolpath=_finish_summary(roles[name]),
        regions={key:r.finish() for key,r in supports[name].items()}) for name in regions}

def scan_project(path,*,step_path):
    raw=Path(path).read_bytes()
    section_source=Path(__file__).with_name("print_sections.py").read_bytes()
    fingerprint=_hash(Path(__file__).read_bytes()+section_source)
    return copy.deepcopy(_scan_project(str(Path(path).resolve()),_hash(raw),str(Path(step_path).resolve()),
                                      _hash(Path(step_path).read_bytes()),fingerprint))

@lru_cache(maxsize=12)
def _scan_project(path,project_hash,step_path,step_hash,analyzer_hash):
    project=inspect_project(path)
    if not project["gcodes"]:raise ValueError("PRINTABILITY: sliced project required")
    with zipfile.ZipFile(path) as z:
        cfg=ET.fromstring(z.read("Metadata/model_settings.config"))
        names={o.get("id"):next(x.get("value") for x in o.findall("metadata") if x.get("key")=="name")
            for o in cfg.findall("object")}
        identities={}
        for p in cfg.findall("plate"):
            for item in p.findall("model_instance"):
                m={x.get("key"):x.get("value") for x in item.findall("metadata")}
                label=m["identify_id"]
                if label in identities:raise ValueError("PRINTABILITY: repeated object identity")
                identities[label]=names[m["object_id"]]
        geometry={p["name"]:geometry_risks(p) for p in project["parts"]}
        def segments():
            for g in project["gcodes"]:
                text=z.read(g).decode("utf-8")
                default=next(iter(identities)) if len(identities)==1 and "; start printing object" not in text else None
                yield from iter_toolpaths(text,default_object=default)
        gap=float(project["settings"].get("support_top_z_distance",.2))
        evidence=toolpath_evidence(segments(),identities,{name:r["regions"] for name,r in geometry.items()},
            top_gap_mm=gap,layer_height_mm=float(project["settings"]["layer_height"]),
            initial_layer_height_mm=float(project["settings"]["initial_layer_print_height"]))
        gcodes={g:_hash(z.read(g)) for g in project["gcodes"]}
    from engineering.print_sections import match_parts
    width=float(project["settings"]["line_width"])
    if not math.isfinite(width) or width<=0:raise ValueError("PRINTABILITY: resolved line width required")
    necks=match_parts(step_path,project["parts"],min_wall_mm=2*width)
    parts=[];gap=float(project["settings"].get("support_top_z_distance",.2))
    for mesh in project["parts"]:
        r=geometry[mesh["name"]];local=evidence[mesh["name"]];r["toolpath"]=local["toolpath"]
        r["annular_necks"]=necks[mesh["name"]]
        if r["annular_necks"]:r["risks"].append("thin_annular_wall")
        for role in RISK_ROLES:
            if role in r["toolpath"]["roles"]:r["risks"].append("slicer_"+role.lower().replace(" ","_"))
        for region in r["regions"]:region["support"]=local["regions"][region["id"]]
        r["status"]="REVIEW_REQUIRED" if r["risks"] else "NO_SCREENING_FINDINGS";parts.append(r)
    if _hash(Path(path).read_bytes())!=project_hash:raise ValueError("PRINTABILITY: project changed during scan")
    if _hash(Path(step_path).read_bytes())!=step_hash:raise ValueError("PRINTABILITY: STEP changed during scan")
    return dict(schema=SCHEMA,project_sha256=project_hash,step_sha256=step_hash,analyzer_sha256=analyzer_hash,
        gcodes=gcodes,policy=POLICY,physical_approval=False,parts=parts,
        limits="Mesh screening, axis-aligned annular B-Rep walls and local toolpaths require engineering review; other wall types, adhesion, bridge/neck strength and full support-area coverage are not proved.")

def verify_review(report,review):
    def refuse(message):raise ValueError("PRINTABILITY: "+message)
    if not isinstance(review,dict) or review.get("schema")!="print-risk-review-v1":
        refuse("bound review required")
    for k in ("project_sha256","step_sha256","analyzer_sha256"):
        if review.get(k)!=report.get(k):refuse("stale "+k)
    expected={p["name"]:p for p in report["parts"] if p["risks"]}
    if set(review.get("parts",{}))!=set(expected):refuse("unreviewed or extra parts")
    def reason(r):
        if not isinstance(r.get("reason"),str) or not r["reason"].strip():refuse("explicit engineering reason required")
    for name,p in expected.items():
        decision=review["parts"][name];reason(decision)
        if decision.get("treatment") not in {"supports","bridge","mixed"}:refuse("unresolved treatment: "+name)
        necks={r["id"]:r for r in p.get("annular_necks",[])}
        if set(decision.get("annular_necks",{}))!=set(necks):refuse("unreviewed thin neck: "+name)
        for key in necks:
            d=decision["annular_necks"][key];reason(d)
            if d.get("strength") is not None or not isinstance(d.get("obtained_by"),str) or not d["obtained_by"].strip():
                refuse("unqualified strength or missing acceptance procedure: "+name+"/"+key)
        regions={r["id"]:r for r in p["regions"]}
        if set(decision.get("regions",{}))!=set(regions):refuse("unreviewed or extra regions: "+name)
        roles=p["toolpath"]["roles"]
        for key,region in regions.items():
            d=decision["regions"][key];reason(d)
            if d.get("treatment")=="supports":
                if region["support"]["interface_length_mm"]<=0:refuse("no local interface under "+name+"/"+key)
            elif d.get("treatment")=="bridge":
                if region["support"].get("bridge_length_mm",0)<=0:refuse("no local bridge for "+name+"/"+key)
            elif d.get("treatment")=="overhang":
                if region["support"].get("overhang_length_mm",0)<=0:refuse("no local overhang for "+name+"/"+key)
            elif d.get("treatment")=="first_layer":
                e=region["support"].get("first_layer",{})
                height=e.get("height_mm")
                if (type(height) not in (int,float) or not math.isfinite(height) or height<=0
                        or e.get("bounds_within_layer") is not True
                        or e.get("local_model_length_mm",0)<=0
                        or region["bounds_mm"][0][2]<-1e-4
                        or region["bounds_mm"][1][2]>height+1e-4):
                    refuse("no local initial-layer model deposition for "+name+"/"+key)
            else:refuse("unresolved region "+name+"/"+key)
        if "small_bed_contact" in p["risks"] or "no_flat_bed_contact" in p["risks"]:
            reason(decision.get("bed_contact",{}))
            support=roles.get("Support",{})
            base=support.get("length_mm",0)>0 and support.get("z_min_mm",1e9)<=.25
            if roles.get("Brim",{}).get("length_mm",0)<=0 and not base:
                refuse("small contact without actual brim or support base: "+name)
    return dict(status="REVIEWED",physical_approval=False,reviewed_parts=sorted(expected))
