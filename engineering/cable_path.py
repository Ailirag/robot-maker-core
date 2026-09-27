"""Deterministic 3D path geometry; independent of harness and fabrication reports."""
import heapq
import itertools
import math
EPS=1e-8

def number(value, *, positive=False):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError("FINITE_NUMBER required")
    if value < 0 or (positive and value == 0): raise ValueError("POSITIVE_DIMENSION required")
    return float(value)

def point(value):
    if not isinstance(value,(list,tuple)) or len(value)!=3:
        raise ValueError("POINT_3D required")
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in value):
        raise ValueError("FINITE_POINT required")
    return tuple(float(v) for v in value)

def add(a,b): return tuple(x+y for x,y in zip(a,b))

def sub(a,b): return tuple(x-y for x,y in zip(a,b))

def mul(a,s): return tuple(x*s for x in a)

def dot(a,b): return sum(x*y for x,y in zip(a,b))

def norm(a): return math.sqrt(dot(a,a))

def unit(a):
    n=norm(a)
    if n<EPS: raise ValueError("ZERO_SEGMENT")
    return mul(a,1/n)

def fillet(points, radius):
    """Exact tangent arcs; refuse reversals and bends that cannot fit."""
    radius=number(radius,positive=True)
    p=[point(v) for v in points]
    if len(p)<2: raise ValueError("TWO_ENDPOINTS required")
    # Remove only forward collinear intermediate vertices.
    clean=[p[0]]
    for i in range(1,len(p)-1):
        u,v=unit(sub(p[i],clean[-1])),unit(sub(p[i+1],p[i]))
        if dot(u,v)<1-1e-10: clean.append(p[i])
    p=clean+[p[-1]]
    lengths=[norm(sub(b,a)) for a,b in zip(p,p[1:])]
    if any(v<EPS for v in lengths): raise ValueError("ZERO_SEGMENT")
    corners={}
    for i in range(1,len(p)-1):
        u,v=unit(sub(p[i],p[i-1])),unit(sub(p[i+1],p[i]))
        cosine=max(-1,min(1,dot(u,v)))
        if cosine<=-1+1e-10: raise ValueError("REVERSAL")
        angle=math.acos(cosine)
        cut=radius*math.tan(angle/2)
        start= sub(p[i],mul(u,cut))
        end=add(p[i],mul(v,cut))
        inward=unit(sub(v,mul(u,cosine)))
        center=add(start,mul(inward,radius))
        corners[i]=dict(kind="arc",start=start,end=end,center=center,
            axis_u=unit(sub(start,center)),axis_v=u,radius_mm=radius,
            angle_rad=angle,length_mm=radius*angle,cut_mm=cut)
    for i,length in enumerate(lengths):
        cuts=sum(corners[j]["cut_mm"] for j in (i,i+1) if j in corners)
        if cuts>length+EPS: raise ValueError("BENDS_OVERLAP")
    primitives=[]
    for i in range(len(p)-1):
        a=corners[i]["end"] if i in corners else p[i]
        b=corners[i+1]["start"] if i+1 in corners else p[i+1]
        if norm(sub(b,a))>EPS:
            primitives.append(dict(kind="line",start=a,end=b,length_mm=norm(sub(b,a))))
        if i+1 in corners: primitives.append(corners[i+1])
    return dict(points=p,primitives=primitives,length_mm=sum(s["length_mm"] for s in primitives))

def sample(route, sagitta=.02):
    """Polyline with certified maximum arc/chord deviation in millimetres."""
    sagitta=number(sagitta,positive=True)
    points=[]
    for s in route["primitives"]:
        if not points: points.append(s["start"])
        if s["kind"]=="line": points.append(s["end"]);continue
        radius=s["radius_mm"]
        angle_step=2*math.acos(max(-1,1-min(sagitta/radius,1)))
        count=max(1,math.ceil(s["angle_rad"]/angle_step))
        for i in range(1,count+1):
            t=s["angle_rad"]*i/count
            points.append(add(s["center"],mul(add(mul(s["axis_u"],math.cos(t)),
                mul(s["axis_v"],math.sin(t))),radius)))
    return points

def _bounds(value):
    lo,hi=(point(p) for p in value)
    if any(b<a for a,b in zip(lo,hi)): raise ValueError("BOUNDS_ORDER")
    return lo,hi

def segment_box(a,b,bounds,margin=0):
    """Slab intersection, including contact; no gaps between sample stations."""
    a,b=point(a),point(b)
    lo,hi=_bounds(bounds);margin=number(margin)
    t0,t1=0.,1.
    for x,y,l,h in zip(a,b,lo,hi):
        l-=margin;h+=margin;d=y-x
        if abs(d)<EPS:
            if x<l or x>h:return False
        else:
            enter,leave=sorted(((l-x)/d,(h-x)/d))
            t0=max(t0,enter);t1=min(t1,leave)
            if t0>t1:return False
    return True

def polyline_collisions(points,obstacles,margin):
    return sorted({o["id"] for o in obstacles
        if any(segment_box(a,b,o["bounds"],margin) for a,b in zip(points,points[1:]))})

def search(start,end,obstacles,*,bounds,step,margin,max_nodes=50000):
    """Six-neighbour A* skeleton with exact endpoints and visibility reduction.

The caller must fillet and recheck the result: free sharp turns do not prove a
bent cable can fit. Inflated boxes are conservative keep-outs, not mesh solids.
"""
    start,end=point(start),point(end);lo,hi=_bounds(bounds)
    step=number(step,positive=True);margin=number(margin)
    if not isinstance(max_nodes,int) or max_nodes<1: raise ValueError("SEARCH_BUDGET")
    inside=lambda p: all(a-EPS<=x<=b+EPS for x,a,b in zip(p,lo,hi))
    free=lambda a,b: not any(segment_box(a,b,o["bounds"],margin) for o in obstacles)
    if not inside(start) or not inside(end) or not free(start,start) or not free(end,end):return None
    if free(start,end):return [start,end]
    origin=(0,0,0);queue=[(norm(sub(end,start)),0.,origin)]
    cost={origin:0.};parent={};seen=set()
    pos=lambda k:add(start,mul(k,step))
    found=None
    while queue and len(seen)<max_nodes:
        _,g,k=heapq.heappop(queue)
        if k in seen:continue
        seen.add(k);p=pos(k)
        if free(p,end): found=k;break
        for axis,sign in itertools.product(range(3),(-1,1)):
            other=list(k);other[axis]+=sign;other=tuple(other);q=pos(other)
            if not inside(q) or not free(p,q):continue
            ng=g+step
            if ng<cost.get(other,math.inf):
                parent[other]=k;cost[other]=ng
                heapq.heappush(queue,(ng+norm(sub(end,q)),ng,other))
    if found is None:return None
    keys=[found]
    while keys[-1]!=origin:keys.append(parent[keys[-1]])
    raw=[pos(k) for k in reversed(keys)]+[end]
    result=[raw[0]];i=0
    while i<len(raw)-1:
        j=len(raw)-1
        while j>i+1 and not free(raw[i],raw[j]):j-=1
        result.append(raw[j]);i=j
    return result

def segment_distance(a,b,c,d):
    """Closest distance between two closed 3D segments, including parallel ones."""
    u,v,w=sub(b,a),sub(d,c),sub(a,c)
    aa,bb,cc,dd,ee=dot(u,u),dot(u,v),dot(v,v),dot(u,w),dot(v,w)
    clamp=lambda x:max(0.,min(1.,x))
    if aa<EPS and cc<EPS:return norm(w)
    if aa<EPS:return norm(sub(w,mul(v,clamp(ee/cc))))
    if cc<EPS:return norm(add(w,mul(u,clamp(-dd/aa))))
    den=aa*cc-bb*bb
    s=clamp((bb*ee-cc*dd)/den) if den>EPS else 0.
    t=(bb*s+ee)/cc
    if t<0:t=0.;s=clamp(-dd/aa)
    elif t>1:t=1.;s=clamp((bb-dd)/aa)
    return norm(sub(add(w,mul(u,s)),mul(v,t)))

def self_interference(route,diameter):
    points=sample(route);segments=list(zip(points,points[1:]));stations=[0.]
    for a,b in segments:stations.append(stations[-1]+norm(sub(b,a)))
    for i,j in itertools.combinations(range(len(segments)),2):
        # Neighbouring portions belong to the same local swept cross-section.
        if stations[j]-stations[i+1]<=2*diameter+.04:continue
        if segment_distance(*segments[i],*segments[j])<=diameter+.04:return True
    return False

