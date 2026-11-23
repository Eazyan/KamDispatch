"""SQLite read model: atomic source snapshots, independent telemetry, local search."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
import sqlite3

from .model import DynamicState, Entity, TYPES, coordinates, timestamp
from .text import edit_distance, interpret, terms

SCHEMA = '''
CREATE TABLE IF NOT EXISTS entities (
 rowid INTEGER PRIMARY KEY, source TEXT NOT NULL, type TEXT NOT NULL, id TEXT NOT NULL,
 payload TEXT NOT NULL, text TEXT NOT NULL, indexed_at TEXT NOT NULL,
 UNIQUE(source,type,id));
CREATE VIRTUAL TABLE IF NOT EXISTS fulltext USING fts5(text, tokenize='unicode61', prefix='2 3 4');
CREATE TABLE IF NOT EXISTS vocabulary (term TEXT PRIMARY KEY, size INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS vocabulary_size ON vocabulary(size);
CREATE TABLE IF NOT EXISTS postings (
 term TEXT NOT NULL, entity INTEGER NOT NULL REFERENCES entities(rowid) ON DELETE CASCADE,
 PRIMARY KEY(term,entity));
CREATE INDEX IF NOT EXISTS postings_entity ON postings(entity);
CREATE VIRTUAL TABLE IF NOT EXISTS spatial USING rtree(entity,min_lon,max_lon,min_lat,max_lat);
CREATE TABLE IF NOT EXISTS dynamic (
 entity INTEGER PRIMARY KEY REFERENCES entities(rowid) ON DELETE CASCADE, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources (
 source TEXT PRIMARY KEY, static_checked_at TEXT, dynamic_checked_at TEXT,
 static_error TEXT, dynamic_error TEXT);
'''


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class SearchIndex:
    """One instance per thread. Use close() or a context manager to release it."""

    def __init__(self, path=':memory:'):
        self.db = sqlite3.connect(path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1):
            self.close()
            raise ValueError('unsupported search schema version')
        try:
            self.db.executescript(SCHEMA)
            self.db.execute('PRAGMA user_version=1')
        except sqlite3.OperationalError as exc:
            self.close()
            raise RuntimeError('SQLite must support FTS5 and R*Tree') from exc

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _source_status(self, source, layer, error=None):
        self.db.execute('INSERT OR IGNORE INTO sources(source) VALUES (?)', (source,))
        self.db.execute(f'UPDATE sources SET {layer}_checked_at=?, {layer}_error=? WHERE source=?',
                        (utcnow(), error, source))

    def _spatial(self, rowid, coord):
        self.db.execute('DELETE FROM spatial WHERE entity=?', (rowid,))
        if coord is not None:
            lon, lat = coord
            self.db.execute('INSERT INTO spatial VALUES (?,?,?,?,?)', (rowid, lon, lon, lat, lat))

    def _remove(self, rowid):
        self.db.execute('DELETE FROM fulltext WHERE rowid=?', (rowid,))
        self.db.execute('DELETE FROM spatial WHERE entity=?', (rowid,))
        self.db.execute('DELETE FROM entities WHERE rowid=?', (rowid,))

    def replace_source(self, source, entities):
        """Authoritative complete snapshot. Missing IDs are removed, atomically.

        Identical repeated rows coalesce; conflicting duplicate IDs reject the batch.
        Different sources/types are never silently conflated.
        """
        if not isinstance(source, str) or not source.strip():
            raise ValueError('source required')
        incoming = {}
        for entity in entities:
            if not isinstance(entity, Entity) or entity.source != source:
                raise ValueError('entity source mismatch')
            key = (entity.type, entity.id)
            if key in incoming and incoming[key] != entity:
                raise ValueError('conflicting duplicate entity')
            incoming[key] = entity
        changed = 0
        with self.db:
            existing = {(r['type'], r['id']): r for r in self.db.execute(
                'SELECT * FROM entities WHERE source=?', (source,))}
            for key, row in existing.items():
                if key not in incoming:
                    self._remove(row['rowid'])
                    changed += 1
            for key, entity in incoming.items():
                payload = encode(entity.dict())
                old = existing.get(key)
                if old and old['payload'] == payload:
                    continue
                text = ' '.join(terms(' '.join((entity.name, entity.id, *entity.aliases))))
                if old:
                    rowid = old['rowid']
                    self.db.execute('UPDATE entities SET payload=?,text=?,indexed_at=? WHERE rowid=?',
                                    (payload, text, utcnow(), rowid))
                    self.db.execute('DELETE FROM fulltext WHERE rowid=?', (rowid,))
                    self.db.execute('DELETE FROM postings WHERE entity=?', (rowid,))
                else:
                    cursor = self.db.execute(
                        'INSERT INTO entities(source,type,id,payload,text,indexed_at) VALUES (?,?,?,?,?,?)',
                        (source, entity.type, entity.id, payload, text, utcnow()))
                    rowid = cursor.lastrowid
                self.db.execute('INSERT INTO fulltext(rowid,text) VALUES (?,?)', (rowid, text))
                for term in set(text.split()):
                    self.db.execute('INSERT OR IGNORE INTO vocabulary VALUES (?,?)', (term, len(term)))
                    self.db.execute('INSERT INTO postings VALUES (?,?)', (term, rowid))
                state = self.db.execute('SELECT payload FROM dynamic WHERE entity=?', (rowid,)).fetchone()
                coord = json.loads(state[0])['coordinates'] if state else entity.coordinates
                self._spatial(rowid, coord)
                changed += 1
            self.db.execute('DELETE FROM vocabulary WHERE NOT EXISTS '
                            '(SELECT 1 FROM postings WHERE postings.term=vocabulary.term)')
            self._source_status(source, 'static')
        return changed

    def update_dynamic(self, source, states):
        """Incremental state updates, including explicit coordinates=None (unlocated).

        Older observations cannot overwrite newer ones. Text and indexed_at are untouched.
        """
        batch = list(states)
        if any(not isinstance(s, DynamicState) for s in batch):
            raise ValueError('DynamicState required')
        with self.db:
            for state in batch:
                row = self.db.execute('SELECT rowid FROM entities WHERE source=? AND type=? AND id=?',
                                      (source, state.type, state.id)).fetchone()
                if row is None:
                    raise ValueError('dynamic state references unknown entity')
                rowid = row[0]
                old = self.db.execute('SELECT payload FROM dynamic WHERE entity=?', (rowid,)).fetchone()
                if old:
                    previous = timestamp(json.loads(old[0])['observed_at'])
                    current = timestamp(state.observed_at)
                    if previous and (current is None or current < previous):
                        continue
                self.db.execute('INSERT INTO dynamic VALUES (?,?) ON CONFLICT(entity) '
                                'DO UPDATE SET payload=excluded.payload', (rowid, encode(asdict(state))))
                self._spatial(rowid, state.coordinates)
            self._source_status(source, 'dynamic')

    def sync(self, adapter, *, dynamic=False):
        """Adapters own IO/timeouts/auth. A failure preserves the previous read model."""
        layer = 'dynamic' if dynamic else 'static'
        try:
            if dynamic:
                self.update_dynamic(adapter.source, adapter.states())
                changed = None
            else:
                changed = self.replace_source(adapter.source, adapter.entities())
            return {'source': adapter.source, 'ok': True, 'changed': changed}
        except Exception as exc:
            # Do not publish raw exception messages: upstream errors can contain secrets.
            with self.db:
                self._source_status(adapter.source, layer, type(exc).__name__)
            return {'source': adapter.source, 'ok': False, 'error': type(exc).__name__}

    def _expansions(self, token):
        # Numbers/house identifiers must match exactly: 20 is never a typo for 21/200.
        if any(c.isdigit() for c in token):
            return {token: 1.0}
        matches = {token: 1.0}
        if len(token) >= 2:
            for row in self.db.execute('SELECT term FROM vocabulary WHERE term LIKE ?', (token + '%',)):
                matches[row[0]] = max(matches.get(row[0], 0), .85)
        if len(token) >= 3:
            for row in self.db.execute('SELECT term FROM vocabulary WHERE term LIKE ?', ('%' + token + '%',)):
                matches[row[0]] = max(matches.get(row[0], 0), .7)
        if len(token) >= 4:
            threshold = 2 if len(token) >= 8 else 1
            for row in self.db.execute('SELECT term FROM vocabulary WHERE size BETWEEN ? AND ?',
                                       (len(token)-threshold, len(token)+threshold)):
                term = row[0]
                if term not in matches and not any(c.isdigit() for c in term):
                    distance = edit_distance(token, term)
                    if distance <= threshold:
                        matches[term] = .65 if distance == 1 else .5
        return matches

    def search(self, query, **options):
        """Read one consistent SQLite snapshot, even during concurrent ingestion."""
        with self.db:
            self.db.execute('BEGIN')
            return self._search(query, **options)

    def _search(self, query, *, types=None, sources=None, bbox=None, limit=20,
               max_age_seconds=300, now=None):
        """Return ranked hits, interpretation, source warnings and explicit freshness.

        bbox=(west,south,east,north), effective coordinates only. No coordinate guessing.
        No hidden candidate limit: all expanded postings are considered before top-k.
        """
        if not isinstance(query, str) or len(query) > 256:
            raise ValueError('query must be a string up to 256 characters')
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError('limit must be 1..100')
        if max_age_seconds < 0:
            raise ValueError('max_age_seconds must be nonnegative')
        if types is not None and (isinstance(types, str) or not set(types) <= TYPES):
            raise ValueError('unsupported types')
        if sources is not None and isinstance(sources, str):
            raise ValueError('sources must be a collection')
        content, intent, explicit = interpret(query)
        if len(content) > 12:
            raise ValueError('query must have at most 12 terms')
        source_rows = [dict(row) for row in self.db.execute('SELECT * FROM sources ORDER BY source')
                       if sources is None or row['source'] in sources]
        warnings = [{'source': s['source'], 'layer': layer, 'error': s[layer+'_error']}
                    for s in source_rows for layer in ('static', 'dynamic') if s[layer+'_error']]
        response = {'query': query, 'intent': intent, 'hits': [], 'warnings': warnings,
                    'sources': source_rows, 'total': 0}
        spatial_ids = None
        if bbox is not None:
            if len(bbox) != 4:
                raise ValueError('bbox requires west,south,east,north')
            west, south = coordinates(bbox[:2])
            east, north = coordinates(bbox[2:])
            if west > east or south > north:
                raise ValueError('bbox must not cross antimeridian; split into two queries')
            spatial_ids = {r[0] for r in self.db.execute(
                'SELECT entity FROM spatial WHERE max_lon>=? AND min_lon<=? AND max_lat>=? AND min_lat<=?',
                (west, east, south, north))}
        if not content:
            return response
        token_matches = []
        # FTS provides exact/prefix candidates. Vocabulary expansions add substring/fuzzy hits.
        fts_query = ' AND '.join('"' + t + '"' + ('' if any(c.isdigit() for c in t) else '*')
                                 for t in content)
        candidates = {r[0] for r in self.db.execute('SELECT rowid FROM fulltext WHERE fulltext MATCH ?',
                                                 (fts_query,))}
        for token in content:
            expansions = self._expansions(token)
            rows = {}
            for term, weight in expansions.items():
                for row in self.db.execute('SELECT entity FROM postings WHERE term=?', (term,)):
                    rows[row[0]] = max(rows.get(row[0], 0), weight)
            token_matches.append(rows)
        expanded = set(token_matches[0])
        for match in token_matches[1:]:
            expanded.intersection_update(match)
        candidates.update(expanded)
        candidates.intersection_update(expanded)  # enforce whole numeric/house tokens after FTS tokenization
        if spatial_ids is not None:
            candidates.intersection_update(spatial_ids)
        clock = timestamp(now) if now else datetime.now(timezone.utc)
        for rowid in candidates:
            row = self.db.execute('SELECT * FROM entities WHERE rowid=?', (rowid,)).fetchone()
            entity = json.loads(row['payload'])
            if types is not None and entity['type'] not in types:
                continue
            if sources is not None and entity['source'] not in sources:
                continue
            state_row = self.db.execute('SELECT payload FROM dynamic WHERE entity=?', (rowid,)).fetchone()
            state = json.loads(state_row[0]) if state_row else None
            coord = state['coordinates'] if state else entity['coordinates']
            # R*Tree rounds outwards; exact check excludes boundary false positives.
            if bbox is not None and (coord is None or not west <= coord[0] <= east
                                     or not south <= coord[1] <= north):
                continue
            weight = sum(m.get(rowid, 0) for m in token_matches) / len(content)
            score = .8 * weight
            if intent == entity['type']:
                score += .15
            if row['text'] == ' '.join(content):
                score += .05
            static_time = timestamp(entity['updated_at'])
            observed = timestamp(state['observed_at']) if state else None
            def age_status(value):
                if value is None:
                    return 'unknown'
                age = (clock-value).total_seconds()
                return 'future' if age < 0 else 'fresh' if age <= max_age_seconds else 'stale'
            response['hits'].append({**entity, 'coordinates': coord,
                'relevance': round(min(score, 1), 6), 'status': state['status'] if state else None,
                'freshness': {'static_updated_at': entity['updated_at'],
                              'static': age_status(static_time), 'indexed_at': row['indexed_at'],
                              'dynamic_observed_at': state['observed_at'] if state else None,
                              'dynamic': age_status(observed),
                              'source_available': not any(w['source'] == entity['source'] for w in warnings)}})
        response['hits'].sort(key=lambda h: (-h['relevance'], h['source'], h['type'], h['id']))
        response['total'] = len(response['hits'])
        response['hits'] = response['hits'][:limit]
        return response
