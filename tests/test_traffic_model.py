"""Model estimates must remain distinguishable from observations and respect topology."""
import pytest
pytest.importorskip('numpy')
from monitoring import traffic_image


def road(road_id, points):
    return {'id': road_id, 'name': road_id, 'coordinates': [[158+x/100000,53+y/100000] for x,y in points]}


def observation(road_id, status):
    return {'id':road_id,'properties':{'traffic_status':status,'coverage_fraction':1}}


def test_graph_estimate_does_not_turn_a_nearby_disconnected_road_into_an_observation():
    roads=[road('source',[(0,0),(100,0)]),road('connected',[(100,0),(200,0)]),
           road('crossing',[(150,-40),(150,40)])]
    result={'features':[observation('source','congested'),observation('connected','unknown'),
                        observation('crossing','unknown')],
            'metadata':{'captured_at':'2026-11-22T10:00:00Z'}}
    original=repr(result)
    estimates=traffic_image.estimate_traffic(roads,result)
    features={f['id']:f['properties'] for f in estimates['features']}
    assert features['connected']['estimated_status']=='congested'
    assert features['connected']['basis']=='graph_neighbors'
    assert features['connected']['nearest_observation_m']>0
    assert features['crossing']['basis']=='sample_prior_only'
    assert features['crossing']['source_road_ids']==[]
    assert features['crossing']['estimated_status']=='unknown', 'A prior alone is not a supported road estimate'
    assert 'source' not in features, 'A model must not replace an observed full-road class'
    assert all(f['source']=='traffic_graph_model' for f in features.values())
    assert all(abs(sum(f['class_scores'].values())-1)<1e-5 for f in features.values())
    assert repr(result)==original, 'Original observations must remain intact'


def test_no_observations_cannot_generate_a_free_road_estimate():
    roads=[road('unseen',[(0,0),(100,0)])]
    result={'features':[observation('unseen','unknown')], 'metadata':{}}
    estimates=traffic_image.estimate_traffic(roads,result)
    assert estimates['features']==[], 'An empty evidence set must not silently mean free traffic'


def test_nearby_congestion_is_not_outvoted_by_many_more_distant_free_roads():
    roads=[road('target',[(100,0),(200,0)]), road('red-west',[(0,0),(100,0)]),
           road('red-east',[(200,0),(300,0)])]
    for i in range(8):
        roads.append(road(f'green-{i}',[(300+100*i,0),(400+100*i,0)]))
    result={'features':[observation(r['id'],'unknown' if r['id']=='target' else
                                   'congested' if r['id'].startswith('red') else 'free') for r in roads]}
    model=traffic_image.estimate_traffic(roads,result)
    prop=next(f['properties'] for f in model['features'] if f['id']=='target')
    assert prop['estimated_status']=='congested', 'Local congestion must not be drowned by distant road density'


def test_splitting_one_osm_way_does_not_multiply_its_prior_vote():
    roads=[road('free-a',[(0,0),(100,0)]),road('free-b',[(100,0),(200,0)]),
           road('red',[(1000,0),(1100,0)]),road('target',[(2000,0),(2100,0)])]
    roads[0]['osm_way']=roads[1]['osm_way']=123
    result={'features':[observation(r['id'],'unknown' if r['id']=='target' else
                                  'congested' if r['id']=='red' else 'free') for r in roads]}
    model=traffic_image.estimate_traffic(roads,result)
    assert model['metadata']['prior_osm_way_count']==2
    assert model['metadata']['prior_scores']['free']==model['metadata']['prior_scores']['congested']


def test_network_range_is_not_silently_extended_by_an_arbitrarily_long_road():
    roads=[road('source',[(0,0),(100,0)]),road('long-target',[(100,0),(10000,0)])]
    result={'features':[observation('source','free'),observation('long-target','unknown')]}
    model=traffic_image.estimate_traffic(roads,result)
    prop=model['features'][0]['properties']
    assert prop['estimated_status']=='unknown'
    assert prop['nearest_observation_m'] is None


def test_a_tiny_graph_contribution_does_not_turn_the_sample_prior_into_a_supported_estimate():
    roads=[road('source',[(0,0),(100,0)]),road('distant',[(100,0),(2700,0)])]
    result={'features':[observation('source','congested'),observation('distant','unknown')]}
    prop=traffic_image.estimate_traffic(roads,result)['features'][0]['properties']
    assert 0 < prop['graph_evidence_share'] < .1
    assert prop['estimated_status']=='unknown', 'Prior-dominated estimates must abstain'
