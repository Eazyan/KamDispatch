"""Read-only projections of existing exports; no source DB migrations or planner calls.

Callers own retrieval/authentication and hand in complete, scope-authorized snapshots.
Never treat a paginated/limited dashboard response as a complete event registry.
"""
import json
from pathlib import Path
from typing import Iterable, Protocol

from .model import DynamicState, Entity


class Adapter(Protocol):
    source: str

    def entities(self) -> Iterable[Entity]: ...

    def states(self) -> Iterable[DynamicState]: ...


class JsonAdapter:
    """Canonical JSON: {entities: [Entity fields], states: [DynamicState fields]}."""
    def __init__(self, source, path):
        self.source, self.path = source, Path(path)

    def _load(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def entities(self):
        for row in self._load()['entities']:
            yield Entity(**row)

    def states(self):
        for row in self._load().get('states', []):
            yield DynamicState(**row)


class CameraCatalogAdapter:
    """Join sources.json names to monitoring/cameras.json location metadata by ID.

    Original stream URLs deliberately do not enter the index. Location notes remain
    aliases: an approximate panorama viewpoint must not become a fabricated address.
    """
    def __init__(self, source, catalog_path, locations_path):
        self.source = source
        self.catalog_path, self.locations_path = Path(catalog_path), Path(locations_path)

    def entities(self):
        catalog = json.loads(self.catalog_path.read_text(encoding='utf-8'))
        locations = {r['id']: r for r in json.loads(self.locations_path.read_text(encoding='utf-8'))}
        for row in catalog:
            location = locations.get(row['id'], {})
            yield Entity(row['id'], 'camera', row['name'], self.source,
                         location.get('coordinates'), (location.get('location_note', ''),), origin='real')


class MonitoringAdapter:
    """Already-authorized dashboard payload. source MUST include run_id/scope.

    Camera/event static text and their current state are projected separately.
    API only returns up to 100 events: this adapter indexes that exported window,
    not the complete PostgreSQL event history. No inference of camera coordinates
    for events (the dashboard event row does not itself carry coordinates).
    """
    def __init__(self, source, payload):
        self.source, self.payload = source, payload
        if not payload.get('run_id'):
            raise ValueError('run_id required')
        if not source.endswith('/' + payload['run_id']):
            raise ValueError('source must end with /run_id to isolate demo/live/replay')

    def entities(self):
        mode = self.payload['mode']
        origin = 'demo' if mode == 'demo' else 'real' if mode in {'live', 'preview'} else 'unknown'
        for row in self.payload['cameras']:
            yield Entity(row['id'], 'camera', row['name'], self.source, row.get('coordinates'),
                         tuple(filter(None, (row.get('area'), row.get('location_note')))), origin=origin)
        for row in self.payload['events']:
            yield Entity(str(row['id']), 'event', row['reason'], self.source,
                         aliases=tuple(filter(None, (row.get('kind'), row.get('name'), row.get('area')))),
                         origin=origin)

    def states(self):
        for row in self.payload['cameras']:
            yield DynamicState(row['id'], 'camera', row.get('coordinates'), row.get('status'), row.get('last_poll'))
        for row in self.payload['events']:
            yield DynamicState(str(row['id']), 'event', status=row.get('state'), observed_at=row.get('last_seen'))


class WinterShiftAdapter:
    """Workspace v2 snapshot (or ShiftRepository.read response), no simulation.

    Jobs and equipment are known; realtime positions are not in this contract.
    Append-only event list has no persistent event ID: use its index scoped by shift.
    Origin must be supplied because a manually entered shift is not proof of real data.
    """
    def __init__(self, source, payload, *, origin='unknown'):
        self.source = source
        self.snapshot = payload.get('snapshot', payload)
        self.origin = origin
        if self.snapshot.get('version') != 2 or not self.snapshot.get('id'):
            raise ValueError('workspace v2 snapshot required')
        if not source.endswith('/' + self.snapshot['id']):
            raise ValueError('source must end with /shift_id')

    def entities(self):
        for vehicle_id in self.snapshot['vehicleIds']:
            yield Entity(vehicle_id, 'equipment', vehicle_id, self.source, origin=self.origin)
        for job in self.snapshot['jobs']:
            yield Entity(job['id'], 'task', job.get('street') or job['id'], self.source,
                         aliases=(job.get('reason', ''),), origin=self.origin)
        for index, event in enumerate(self.snapshot['events']):
            yield Entity(str(index), 'event', event['message'], self.source,
                         updated_at=event.get('at'), origin=self.origin)

    def states(self):
        for job in self.snapshot['jobs']:
            yield DynamicState(job['id'], 'task', status=job.get('state'))


class OsmExportAdapter:
    """winter-dispatch data/petropavlovsk.json, ODbL source attribution retained.

    Road section identity is the existing OSM way ID (not a routing arc).
    Representative coordinate is a source node, not a street centroid/geocoder.
    Streets are grouped only by exact name within this one city export. Address
    entities require an explicit addr:housenumber and addr:street or addr:place.
    """
    def __init__(self, source, path, *, updated_at=None):
        self.source, self.path, self.updated_at = source, Path(path), updated_at

    def entities(self):
        data = json.loads(self.path.read_text(encoding='utf-8'))
        nodes = data['nodes']
        streets = {}
        for way_id, (refs, tags) in data['ways'].items():
            coord = next((nodes[str(ref)] for ref in refs if str(ref) in nodes), None)
            name = tags.get('name')
            if tags.get('highway'):
                yield Entity('way/' + way_id, 'road_section', name or 'OSM way ' + way_id,
                             self.source, coord, updated_at=self.updated_at, origin='real')
                if name:
                    streets.setdefault(name, coord)
            address = self._address('way/' + way_id, tags, coord)
            if address:
                yield address
        for name, coord in streets.items():
            yield Entity('street/' + name, 'street', name, self.source, coord,
                         updated_at=self.updated_at, origin='real')
        for node_id, tags in data.get('points', {}).items():
            address = self._address('node/' + node_id, tags, nodes.get(node_id))
            if address:
                yield address

    def _address(self, id, tags, coord):
        street = tags.get('addr:street') or tags.get('addr:place')
        house = tags.get('addr:housenumber')
        if street and house:
            return Entity(id, 'address', f'{street} {house}', self.source, coord,
                          updated_at=self.updated_at, origin='real')
