"""Shared finite pilot plan validation (Linux and Windows)."""
def validate_plan(plan):
    tiles = plan["tiles"]
    if not 1 <= len(tiles) <= 3:
        raise ValueError("Pilot requires 1–3 tiles")
    ids = set()
    for tile in tiles:
        name = tile["id"]
        if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in name) or name in ids:
            raise ValueError("Invalid/duplicate tile id")
        ids.add(name)
        from monitoring.traffic_image import mercator
        mercator(tile["center"])
    if not 14 <= plan["zoom"] <= 18 or plan["viewport"] != [4096, 4096]:
        raise ValueError("Pilot uses zoom14–18 and fixed 4096 viewport")
    return tiles

