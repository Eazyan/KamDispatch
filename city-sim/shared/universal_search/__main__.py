"""Offline JSON CLI. Run with PYTHONPATH=shared python3 -m universal_search."""
import argparse
import json

from .adapters import CameraCatalogAdapter, JsonAdapter, OsmExportAdapter
from .engine import SearchIndex


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    ingest = commands.add_parser('index-json')
    ingest.add_argument('--source', required=True)
    ingest.add_argument('path')
    dynamic = commands.add_parser('update-state')
    dynamic.add_argument('--source', required=True)
    dynamic.add_argument('path')
    osm = commands.add_parser('index-osm')
    osm.add_argument('--source', default='OpenStreetMap/ODbL-1.0/pkc')
    osm.add_argument('path')
    cameras = commands.add_parser('index-cameras')
    cameras.add_argument('--source', default='kamchatka-cam/catalog')
    cameras.add_argument('catalog')
    cameras.add_argument('locations')
    search = commands.add_parser('search')
    search.add_argument('query')
    search.add_argument('--type', action='append', dest='types')
    search.add_argument('--source', action='append', dest='sources')
    search.add_argument('--bbox', nargs=4, type=float)
    search.add_argument('--limit', type=int, default=20)
    args = parser.parse_args()
    with SearchIndex(args.db) as index:
        if args.command == 'search':
            result = index.search(args.query, types=args.types, sources=args.sources,
                                  bbox=args.bbox, limit=args.limit)
        else:
            if args.command == 'index-osm':
                adapter = OsmExportAdapter(args.source, args.path)
            elif args.command == 'index-cameras':
                adapter = CameraCatalogAdapter(args.source, args.catalog, args.locations)
            else:
                adapter = JsonAdapter(args.source, args.path)
            result = index.sync(adapter, dynamic=args.command == 'update-state')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get('ok', True) else 1


if __name__ == '__main__':
    raise SystemExit(main())
