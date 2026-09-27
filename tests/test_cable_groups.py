import copy
import pytest
from engineering import cable_groups as G


def v(x):
    return {'value': x, 'obtained_by': 'independent test fixture'}


def fixture():
    cables = [{'id': 'a', 'diameter_mm': v(2), 'bend_radius_mm': v(3)},
              {'id': 'b', 'diameter_mm': v(2), 'bend_radius_mm': v(3)}]
    group = dict(id='pair', members=['a','b'], packing_mm=v({'a':[-1,0],'b':[1,0]}),
                 wrap_mm=v(0), clearance_mm=v(0), channel_diameter_mm=v(4),
                 route_radius_mm=v(5))
    return group,cables


def test_packing_envelope_and_channel_limits():
    group,cables=fixture()
    r=G.assess(group,cables)
    assert r['envelope_diameter_mm']==4
    assert r['status']=='SECTION_CALCULATED'
    assert r['physical_approval'] is False
    group['channel_diameter_mm']=v(3.9)
    assert 'CHANNEL_TOO_SMALL' in G.assess(group,cables)['issues']
    group['channel_diameter_mm']=v(4)
    group['route_radius_mm']=v(4.9)
    assert 'BUNDLE_BEND_TOO_TIGHT' in G.assess(group,cables)['issues']


def test_unknown_never_becomes_a_fitting_bundle():
    group,cables=fixture(); cables[0]['diameter_mm']=v(None)
    r=G.assess(group,cables)
    assert r['envelope_diameter_mm'] is None and r['status']=='INCOMPLETE'
    assert r['unknowns']['a.diameter_mm']['obtained_by']
    group,cables=fixture();group['packing_mm']=v(None)
    assert G.assess(group,cables)['status']=='INCOMPLETE'


def test_overlapping_packing_and_ribbon_are_not_accepted():
    group,cables=fixture();group['packing_mm']=v({'a':[0,0],'b':[0,0]})
    assert 'PACKING_OVERLAP:a:b' in G.assess(group,cables)['issues']
    group,cables=fixture();cables[0]['section']='ribbon'
    assert 'UNSUPPORTED_BUNDLE_SECTION:a' in G.assess(group,cables)['issues']


@pytest.mark.parametrize('change',[lambda g:g.update(members=['a','a']),lambda g:g.update(members=['absent']),
    lambda g:g.update(packing_mm=v({'a':[float('nan'),0],'b':[1,0]}))])
def test_invalid_input_is_rejected(change):
    group,cables=fixture(); change(group)
    with pytest.raises(ValueError): G.assess(group,cables)


def test_scene_retains_unrouted_and_rejects_unmapped_geometry():
    rows=[dict(id='a',body='wire_a',group='connector',category='power',source='p.1',target='q.1'),
          dict(id='b',body=None,group='connector',category='signal',source='p.2',target='q.2')]
    index={'steps':[{'number':1,'adds':['wire_a']}],'base':['host']}
    groups=[dict(id='connector',label='Connector',members=['a','b'])]
    result=G.scene(rows,groups,index)
    assert len(result['cables'])==2 and result['unrouted']==['b']
    assert result['physical_approval'] is False
    bad=copy.deepcopy(rows);bad[0]['body']='missing'
    with pytest.raises(ValueError):G.scene(bad,groups,index)
    bad=copy.deepcopy(groups);bad[0]['members']=['a']
    with pytest.raises(ValueError):G.scene(rows,bad,index)
