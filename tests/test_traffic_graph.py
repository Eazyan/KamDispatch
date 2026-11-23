import hashlib
import json
import unittest
from monitoring.traffic_graph import export_graph, assign

class GraphTrafficTests(unittest.TestCase):
    def setUp(self):
        self.raw = json.dumps({'edges': [dict(id='a',u=1,v=2,geometry=[[0,0],[1,0]],length_m=100,reverse_edge_id='b'),dict(id='b',u=2,v=1,geometry=[[1,0],[0,0]],length_m=100,reverse_edge_id='a')]}).encode()
        self.export = export_graph(self.raw)
    def run_case(self, lines=None, **kwargs):
        args = dict(graph_sha256=self.export['graph_sha256'],source_sha256='synthetic',captured_at='2026-11-23T10:00:00+12:00',now='2026-11-23T10:01:00+12:00')
        args.update(kwargs)
        return assign(self.export, lines if lines is not None else [dict(id='red',pixels=[[20,0],[40,0]],traffic_status='congested')], [[[0,0],[100,0]]], [-1,-10,102,20], **args)
    def test_reverse_and_local_interval(self):
        out=self.run_case()['observations']; red=[r for r in out if r['traffic_status']=='congested'];self.assertTrue(red)
        for a,b in zip(red[::2],red[1::2]):
            self.assertEqual(b['interval_m'],[100-a['interval_m'][1],100-a['interval_m'][0]])
            self.assertLess(a['interval_m'][1],50)
            self.assertEqual(a['direction'],'unresolved')
        self.assertTrue(any(r['traffic_status']=='unknown' for r in out))
    def test_no_color_unknown(self):
        self.assertEqual({r['traffic_status'] for r in self.run_case([])['observations']},{'unknown'})
    def test_stale(self):
        self.assertTrue(self.run_case(now='2026-11-24T10:00:00+12:00')['stale'])
        self.assertEqual({r['traffic_status'] for r in self.run_case(now='2026-11-24T10:00:00+12:00')['observations']},{'unknown'})
    def test_hash_and_determinism(self):
        with self.assertRaises(ValueError): self.run_case(graph_sha256='changed')
        self.assertEqual(self.run_case(),self.run_case())
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(),self.export['graph_sha256'])
    def test_mixed(self):
        lines=[dict(id=str(i),pixels=[[20,0],[40,0]],traffic_status=s) for i,s in enumerate(['free','congested'])]
        self.assertTrue(any(r['reason']=='mixed_colors' for r in self.run_case(lines)['observations']))
    def test_parallel_same_way_and_crossing(self):
        self.export['segments'].append(dict(self.export['segments'][0],id='parallel',directed_edges=[dict(id='p',length_m=100,reverse=False)]))
        args=dict(graph_sha256=self.export['graph_sha256'],source_sha256='synthetic',captured_at='2026-11-23T10:00:00+12:00',now='2026-11-23T10:01:00+12:00')
        out=assign(self.export,[dict(id='r',pixels=[[20,1],[40,1]],traffic_status='congested')],[[[0,0],[100,0]],[[0,2],[100,2]]],[-1,-10,102,20],**args)
        self.assertEqual({r['traffic_status'] for r in out['observations']},{'unknown'})
        self.export['segments'].pop()
        self.assertEqual({r['traffic_status'] for r in self.run_case([dict(id='x',pixels=[[30,-10],[30,10]],traffic_status='free')])['observations']},{'unknown'})
    def test_bad_reverse(self):
        x=json.loads(self.raw);x['edges'][1]['geometry'][0]=[2,0]
        with self.assertRaises(ValueError):export_graph(json.dumps(x).encode())

    def test_partial_visibility(self):
        out=assign(self.export,[],[[[0,0],[100,0]]],[0,-5,50,10],graph_sha256=self.export['graph_sha256'],source_sha256='synthetic',captured_at='2026-11-23T10:00:00+12:00',now='2026-11-23T10:01:00+12:00')
        self.assertTrue(any(r['reason']=='outside_capture' for r in out['observations']))
        self.assertEqual({r['traffic_status'] for r in out['observations']},{'unknown'})

    def test_offset_ribbon_is_continuous_without_extending_endpoints(self):
        out=self.run_case([dict(id='offset',pixels=[[20,9],[80,9]],traffic_status='slow')])
        chosen=[r for r in out['observations'] if r['edge_id']=='a' and r['traffic_status']=='slow']
        self.assertTrue(chosen)
        self.assertLessEqual(chosen[0]['interval_m'][0],20)
        self.assertGreaterEqual(chosen[-1]['interval_m'][1],80)
        for a,b in zip(chosen,chosen[1:]):self.assertAlmostEqual(a['interval_m'][1],b['interval_m'][0])
        self.assertGreaterEqual(chosen[0]['interval_m'][0],18)
        self.assertLessEqual(chosen[-1]['interval_m'][1],82)
