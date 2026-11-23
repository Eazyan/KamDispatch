"""The reference audit must measure length and preserve angle constraints."""
import pytest
pytest.importorskip('numpy')
import numpy as np
from scripts.evaluate_traffic import coverage, mapping


def test_reference_coverage_measures_length_and_rejects_crossing_geometry():
    reference=[[0,0],[100,0]]
    parallel=[(np.array([0,3]),np.array([100,3]))]
    hit,total=coverage(reference,parallel,5)
    assert hit==total==100
    # A nearby perpendicular line must not receive credit even with a wide radius.
    crossing=[(np.array([50,-100]),np.array([50,100]))]
    hit,total=coverage(reference,crossing,100)
    assert hit==0 and total==100


def test_reference_ids_cannot_silently_overwrite_another_capture():
    with pytest.raises(ValueError,match='unique'):
        mapping(['pilot=a','pilot=b'])


def test_target_coverage_excludes_prior_only_models_local_only_roads_and_missing_ids():
    from scripts.evaluate_traffic import target_coverage
    result={'features':[{'id':key,'properties':{'traffic_status':status}} for key,status in
                        [('observed','free'),('modeled','unknown'),('prior','unknown'),('local','unknown')]],
            'model_estimates':{'features':[{'id':key,'properties':{'estimated_status':status,'basis':basis}}
                              for key,status,basis in [('modeled','slow','graph_neighbors'),
                                                       ('prior','free','sample_prior_only')]]}}
    report=target_coverage(['observed','modeled','prior','local','missing'],result)
    assert report['target_count']==5 and report['present_count']==4
    assert report['total_assessed']==2
    assert report['unassessed_ids']==['local','missing','prior']
    assert report['missing_ids']==['missing']


def test_validation_hides_partial_evidence_and_all_siblings_of_an_osm_way(monkeypatch):
    from scripts import evaluate_traffic as evaluation
    roads=[{'id':'a','osm_way':7,'coordinates':[[158,53],[158.001,53]]},
           {'id':'a-partial','osm_way':7,'coordinates':[[158.001,53],[158.002,53]]},
           {'id':'b','osm_way':8,'coordinates':[[158.01,53],[158.011,53]]}]
    result={'features':[{'id':key,'properties':{'traffic_status':status,'coverage_fraction':.8}}
                        for key,status in [('a','free'),('a-partial','unknown'),('b','congested')]],
            'matched_sections':{'features':[{'properties':{'road_id':'a-partial','traffic_status':'free'},
                'geometry':{'coordinates':[[158.001,53],[158.002,53]]}}]}}
    original=evaluation.estimate_traffic
    training_sets=[]
    def capture_training(roads,masked):
        inferred=original(roads,masked)
        training_sets.append(set(inferred.get('metadata',{}).get('prior_source_road_ids',[])))
        return inferred
    monkeypatch.setattr(evaluation,'estimate_traffic',capture_training)
    report=evaluation.model_validation(roads,result)
    assert training_sets==[{'b'},{'a','a-partial'}]
    assert report['count']==2 and report['held_out_evidence_overlap']==0
