"""Explicit cable groups and conservative round-bundle section checks.

A logical group is not a shared physical route. A section calculation does not
prove installation, retention, connector passage or individual cutting lengths.
"""
import itertools
import math
from engineering.cable_path import number, point

SCHEMA = 'cable-groups-v1'


def assess(group, cables):
    by_id = {c['id']: c for c in cables}
    members = group['members']
    if (not members or len(by_id) != len(cables) or len(set(members)) != len(members)
            or not set(members) <= by_id.keys()):
        raise ValueError('BUNDLE_MEMBERS')
    unknowns, issues = {}, []

    def fact(data, key, name=None):
        name = name or key
        entry = data.get(key, {'value': None, 'obtained_by': 'Задать и подтвердить '+name+'.'})
        if not isinstance(entry, dict) or 'value' not in entry or not str(entry.get('obtained_by') or '').strip():
            raise ValueError('BUNDLE_FACT: '+name)
        if entry['value'] is None:
            unknowns[name] = entry
        return entry['value']

    dimensions, bends = {}, []
    for key in members:
        cable = by_id[key]
        if cable.get('section', 'round') != 'round':
            issues.append('UNSUPPORTED_BUNDLE_SECTION:'+key)
            continue
        diameter = fact(cable, 'diameter_mm', key+'.diameter_mm')
        radius = fact(cable, 'bend_radius_mm', key+'.bend_radius_mm')
        if diameter is not None: dimensions[key] = number(diameter, positive=True)
        if radius is not None: bends.append(number(radius, positive=True))
    packing = fact(group, 'packing_mm')
    scalar = {k: fact(group, k) for k in ('wrap_mm', 'clearance_mm', 'channel_diameter_mm', 'route_radius_mm')}
    for key, value in scalar.items():
        if value is not None: number(value, positive=key in ('channel_diameter_mm', 'route_radius_mm'))
    if packing is not None:
        if not isinstance(packing, dict) or set(packing) != set(members):
            raise ValueError('BUNDLE_PACKING_COVERAGE')
        centres = {}
        for key, xy in packing.items():
            if not isinstance(xy, (list, tuple)) or len(xy) != 2: raise ValueError('BUNDLE_POINT_2D')
            centres[key] = point([*xy, 0])
        for a, b in itertools.combinations(sorted(dimensions), 2):
            if math.dist(centres[a], centres[b]) < (dimensions[a]+dimensions[b])/2-1e-8:
                issues.append('PACKING_OVERLAP:'+a+':'+b)
    envelope = None
    if len(dimensions)==len(members) and packing is not None and scalar['wrap_mm'] is not None:
        envelope = 2*(max(math.hypot(*packing[k])+dimensions[k]/2 for k in members)+scalar['wrap_mm'])
        channel, clearance = scalar['channel_diameter_mm'], scalar['clearance_mm']
        if channel is not None and clearance is not None and envelope+2*clearance > channel+1e-8:
            issues.append('CHANNEL_TOO_SMALL')
        radius = scalar['route_radius_mm']
        if radius is not None and bends and radius-envelope/2 < max(bends)-1e-8:
            issues.append('BUNDLE_BEND_TOO_TIGHT')
    return dict(schema=SCHEMA, id=group['id'], members=list(members),
                status='BLOCKED' if issues else 'INCOMPLETE' if unknowns else 'SECTION_CALCULATED',
                envelope_diameter_mm=envelope, issues=issues, unknowns=unknowns,
                physical_approval=False,
                scope='Только сечение круглого жгута и консервативный изгиб; установка, крепления, выходы разъёмов и длины проверяются отдельно.')


def scene(cables, groups, index):
    """Bind all conductors, including those without geometry, to viewer bodies."""
    ids = [c['id'] for c in cables]
    group_ids = [g['id'] for g in groups]
    assigned = [key for g in groups for key in g['members']]
    bodies = [c['body'] for c in cables if c.get('body') is not None]
    rendered = [body for step in index['steps'] for body in step['adds']]
    if (len(ids)!=len(set(ids)) or len(group_ids)!=len(set(group_ids))
            or len(assigned)!=len(set(assigned)) or set(assigned)!=set(ids)
            or len(bodies)!=len(set(bodies)) or len(rendered)!=len(set(rendered))
            or set(bodies)!=set(rendered) or set(bodies)&set(index['base'])):
        raise ValueError('CABLE_SCENE_COVERAGE')
    membership = {key:g['id'] for g in groups for key in g['members']}
    for row in cables:
        if membership[row['id']] != row['group']:
            raise ValueError('CABLE_SCENE_GROUP')
        for field in ('id', 'source', 'target', 'category'):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError('CABLE_SCENE_FIELD: '+field)
    return dict(schema=SCHEMA, cables=cables, groups=groups,
                unrouted=[c['id'] for c in cables if c.get('body') is None], physical_approval=False)
