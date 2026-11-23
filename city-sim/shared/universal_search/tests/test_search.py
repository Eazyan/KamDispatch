"""All data in this test suite is explicitly synthetic."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from universal_search import DynamicState, Entity, SearchIndex
from universal_search.adapters import (CameraCatalogAdapter, JsonAdapter, MonitoringAdapter,
                                       OsmExportAdapter, WinterShiftAdapter)

NOW = '2026-11-23T10:00:00+00:00'


def entity(id, type, name, **kwargs):
    return Entity(id, type, name, 'fixture', origin='synthetic', **kwargs)


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.index = SearchIndex()
        self.rows = [entity('bus20', 'bus', 'Автобус 20'),
                     entity('bus200', 'bus', 'Автобус 200'),
                     entity('a20', 'address', 'улица Академика Королёва, 20', coordinates=(158.6, 53.1)),
                     entity('a21', 'address', 'Королёва 21'),
                     entity('street', 'street', 'улица Академика Королёва'),
                     entity('camera20', 'camera', 'Камера 20', coordinates=(158.61, 53.1)),
                     entity('split', 'address', 'Королёва 20/1')]
        self.index.replace_source('fixture', self.rows)
        self.addCleanup(self.index.close)

    def hits(self, query, **kw):
        return self.index.search(query, **kw)['hits']

    def test_ambiguous_numeric_keeps_multiple_types_without_fuzzy_numbers(self):
        response = self.index.search('20')
        self.assertEqual(response['intent'], 'ambiguous_number')
        self.assertEqual({h['type'] for h in response['hits']}, {'address', 'camera', 'bus'})
        self.assertNotIn('bus200', [h['id'] for h in response['hits']])
        self.assertNotIn('a21', [h['id'] for h in response['hits']])
        self.assertNotIn('split', [h['id'] for h in response['hits']])

    def test_explicit_bus_prioritized_and_type_filter(self):
        self.assertEqual(self.hits('автобус 20')[0]['id'], 'bus20')
        self.assertEqual([h['id'] for h in self.hits('20', types=['bus'])], ['bus20'])

    def test_number_sign_and_common_abbreviations(self):
        self.assertEqual(self.hits('автобус №20')[0]['id'], 'bus20')
        self.assertEqual(self.hits('ул. акад. Королева д. 20')[0]['id'], 'a20')
        from universal_search.text import normalize
        self.assertEqual(normalize('пр-т Победы'), normalize('пр-кт Победы'))

    def test_address_context(self):
        response = self.index.search('Королёва 20')
        self.assertEqual(response['intent'], 'address')
        self.assertEqual([h['id'] for h in response['hits']], ['a20'])

    def test_normalization_abbreviations_and_yo(self):
        self.assertEqual(self.hits('ул. Академика Королева д.20'), self.hits('улица академика королёва 20'))

    def test_typo_and_transposition(self):
        self.assertEqual(self.hits('Королва 20')[0]['id'], 'a20')
        self.assertEqual(self.hits('Кроолева 20')[0]['id'], 'a20')
        self.assertEqual(self.hits('Королвеа 20')[0]['id'], 'a20')

    def test_prefix_and_infix(self):
        self.assertTrue(self.hits('Корол'))
        self.assertTrue(self.hits('ролева'))
        self.assertEqual(self.hits('несуществующая улица'), [])

    def test_fulltext_order_independent(self):
        self.assertEqual(self.hits('20 Академика Королева')[0]['id'], 'a20')

    def test_missing_coordinates(self):
        self.assertIsNone(self.hits('автобус 20')[0]['coordinates'])
        self.assertEqual(self.hits('автобус 20', types=['bus'], bbox=(158, 53, 159, 54)), [])

    def test_bbox(self):
        hits = self.hits('20', bbox=(158.59, 53.09, 158.605, 53.11))
        self.assertEqual([h['id'] for h in hits], ['a20'])

    def test_dynamic_moves_without_text_reindex_and_null_clears_position(self):
        before = self.hits('20', types=['camera'])[0]['freshness']['indexed_at']
        self.index.update_dynamic('fixture', [DynamicState('camera20', 'camera', (159, 54), 'online', NOW)])
        hit = self.hits('20', types=['camera'])[0]
        self.assertEqual(hit['coordinates'], [159, 54])
        self.assertEqual(hit['freshness']['indexed_at'], before)
        self.assertEqual(self.hits('20', types=['camera'], bbox=(158.5, 53, 158.7, 53.2)), [])
        self.assertEqual(len(self.hits('20', types=['camera'], bbox=(158.9, 53.9, 159.1, 54.1))), 1)
        self.index.update_dynamic('fixture', [DynamicState('camera20', 'camera', None, 'offline', NOW)])
        self.assertIsNone(self.hits('20', types=['camera'])[0]['coordinates'])

    def test_old_dynamic_does_not_overwrite(self):
        self.index.update_dynamic('fixture', [DynamicState('camera20', 'camera', (159, 54), 'online', NOW)])
        self.index.update_dynamic('fixture', [DynamicState('camera20', 'camera', (158, 53), 'old', '2026-11-22T00:00:00Z')])
        self.assertEqual(self.hits('20', types=['camera'])[0]['status'], 'online')

    def test_freshness_unknown_stale_future(self):
        hit = self.hits('20', types=['bus'], now=NOW)[0]
        self.assertEqual(hit['freshness']['static'], 'unknown')
        self.index.update_dynamic('fixture', [DynamicState('bus20', 'bus', observed_at=NOW)])
        self.assertEqual(self.hits('20', types=['bus'], now=NOW)[0]['freshness']['dynamic'], 'fresh')
        self.assertEqual(self.hits('20', types=['bus'], now='2026-11-24T00:00:00Z')[0]['freshness']['dynamic'], 'stale')
        self.assertEqual(self.hits('20', types=['bus'], now='2026-11-22T00:00:00Z')[0]['freshness']['dynamic'], 'future')

    def test_snapshot_updates_deletes_and_idempotence(self):
        self.assertEqual(self.index.replace_source('fixture', self.rows), 0)
        self.index.replace_source('fixture', [entity('a20', 'address', 'Мишенная 116')])
        self.assertEqual(self.hits('Королева'), [])
        self.assertEqual(self.hits('Мишенная')[0]['id'], 'a20')
        self.assertEqual(self.hits('20', bbox=(158, 53, 159, 54)), [])

    def test_duplicate_policy(self):
        row = self.rows[0]
        self.assertEqual(self.index.replace_source('fixture', [row, row]), 6)
        with self.assertRaises(ValueError):
            self.index.replace_source('fixture', [row, entity('bus20', 'bus', 'Другое имя')])
        self.assertEqual(self.hits('20')[0]['name'], 'Автобус 20')
        self.index.replace_source('other', [Entity('bus20', 'bus', 'Автобус 20', 'other')])
        self.assertEqual(len(self.hits('20')), 2)
        self.assertEqual(len(self.hits('20', sources=['other'])), 1)

    def test_unavailable_source_preserves_data_and_reports_recovery(self):
        class Broken:
            source = 'fixture'
            def entities(self):
                yield entity('temp', 'street', 'Не должна сохраниться')
                raise TimeoutError('do not expose secret URL')
        self.assertFalse(self.index.sync(Broken())['ok'])
        response = self.index.search('20')
        self.assertEqual(response['warnings'][0]['error'], 'TimeoutError')
        self.assertFalse(response['hits'][0]['freshness']['source_available'])
        self.assertEqual(self.hits('сохраниться'), [])
        self.index.replace_source('fixture', self.rows)
        self.assertEqual(self.index.search('20')['warnings'], [])

    def test_dynamic_transaction_rolls_back(self):
        with self.assertRaises(ValueError):
            self.index.update_dynamic('fixture', [DynamicState('camera20', 'camera', (159, 54)),
                                                  DynamicState('missing', 'camera')])
        self.assertEqual(self.hits('20', types=['camera'])[0]['coordinates'], [158.61, 53.1])

    def test_filters_before_limit_and_no_hidden_candidate_cap(self):
        rows = [entity(str(i), 'street', 'Королёва') for i in range(150)]
        rows.append(entity('target', 'address', 'Королёва 20'))
        self.index.replace_source('fixture', rows)
        self.assertEqual(self.hits('Королева', types=['address'], limit=1)[0]['id'], 'target')

    def test_all_entity_types_supported(self):
        from universal_search.model import TYPES
        self.index.replace_source('fixture', [entity(t, t, 'Объект 20') for t in TYPES])
        self.assertEqual({h['type'] for h in self.hits('Объект 20')}, TYPES)

    def test_empty_and_unsafe_inputs(self):
        for query in ('', '  ', '" OR * - ;', 'улица'):
            self.assertEqual(self.hits(query), [])
        self.assertEqual(self.hits('20 OR 1=1'), [])
        for kwargs in ({'limit': 0}, {'types': ['unknown']}, {'bbox': (159, 54, 158, 53)}):
            with self.assertRaises(ValueError):
                self.hits('20', **kwargs)
        with self.assertRaises(ValueError):
            self.hits('x' * 257)

    def test_validation(self):
        for coord in ((float('nan'), 53), (181, 53), (True, 53)):
            with self.assertRaises(ValueError):
                entity('bad', 'camera', 'Bad', coordinates=coord)
        with self.assertRaises(ValueError):
            entity('bad', 'camera', 'Bad', updated_at='2026-11-23T10:00:00')


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.index = SearchIndex(self.root / 'search.sqlite')
        self.addCleanup(self.index.close)

    def write(self, name, data):
        path = self.root / name
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return path

    def test_camera_catalog_join_missing_location(self):
        names = self.write('names.json', [{'id': 'one', 'name': 'Чехова'}, {'id': 'two', 'name': 'Ленинская'}])
        locations = self.write('locations.json', [{'id': 'one', 'coordinates': [158, 53], 'location_note': 'Чехова 1А'}])
        self.assertTrue(self.index.sync(CameraCatalogAdapter('catalog', names, locations))['ok'])
        self.assertEqual(self.index.search('Чехова 1А')['hits'][0]['coordinates'], [158, 53])
        self.assertIsNone(self.index.search('Ленинская')['hits'][0]['coordinates'])

    def test_monitoring_separate_state_and_run(self):
        payload = {'run_id': 'demo', 'mode': 'demo', 'cameras': [
            {'id': 'c1', 'name': 'Камера 20', 'coordinates': [158, 53], 'status': 'online', 'last_poll': NOW}],
            'events': [{'id': 'e1', 'reason': 'Снежный нанос', 'kind': 'snow', 'state': 'new', 'last_seen': NOW}]}
        adapter = MonitoringAdapter('monitoring/demo', payload)
        self.assertTrue(self.index.sync(adapter)['ok'])
        self.assertTrue(self.index.sync(adapter, dynamic=True)['ok'])
        hit = self.index.search('20')['hits'][0]
        self.assertEqual((hit['origin'], hit['status']), ('demo', 'online'))
        self.assertIsNone(self.index.search('нанос')['hits'][0]['coordinates'])
        with self.assertRaises(ValueError):
            MonitoringAdapter('monitoring/live', payload)

    def test_winter_snapshot_no_invented_vehicle_coordinates(self):
        snapshot = {'version': 2, 'id': 'shift1', 'vehicleIds': ['М20'],
                    'jobs': [{'id': 'j1', 'street': 'Королёва 20', 'state': 'done'}],
                    'events': [{'message': 'Смена создана', 'at': NOW}]}
        adapter = WinterShiftAdapter('winter/shift1', {'snapshot': snapshot}, origin='demo')
        self.assertTrue(self.index.sync(adapter)['ok'])
        self.assertTrue(self.index.sync(adapter, dynamic=True)['ok'])
        self.assertEqual(self.index.search('Королева 20')['hits'][0]['status'], 'done')
        self.assertIsNone(self.index.search('М20')['hits'][0]['coordinates'])

    def test_osm_source_ids_and_explicit_address_only(self):
        path = self.write('osm.json', {'nodes': {'1': [158, 53]}, 'ways': {
            '10': [[1], {'name': 'Королёва', 'highway': 'residential'}],
            '11': [[1], {'name': 'Королёва', 'highway': 'residential'}],
            '12': [[1], {'addr:street': 'Королёва', 'addr:housenumber': '20'}]}, 'points': {}})
        self.assertTrue(self.index.sync(OsmExportAdapter('OSM/ODbL', path))['ok'])
        hits = self.index.search('Королева')['hits']
        self.assertEqual(len(hits), 4)
        self.assertEqual({h['id'] for h in hits if h['type'] == 'road_section'}, {'way/10', 'way/11'})
        self.assertEqual(len([h for h in hits if h['type'] == 'street']), 1)
        self.assertEqual(self.index.search('Королева 20')['hits'][0]['id'], 'way/12')

    def test_persistence_canonical_adapter_and_cli(self):
        path = self.write('canonical.json', {'entities': [entity('bus20', 'bus', 'Автобус 20').dict()]})
        self.assertTrue(self.index.sync(JsonAdapter('fixture', path))['ok'])
        result = subprocess.run([sys.executable, '-m', 'universal_search', '--db',
                                 str(self.root / 'search.sqlite'), 'search', 'автобус 20'],
                                capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)['hits'][0]['id'], 'bus20')
        path.write_text('{ broken', encoding='utf-8')
        self.assertFalse(self.index.sync(JsonAdapter('fixture', path))['ok'])
        self.assertTrue(self.index.search('20')['hits'])


if __name__ == '__main__':
    unittest.main()
