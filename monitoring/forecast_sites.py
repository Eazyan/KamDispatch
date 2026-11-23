"""Reference points for city forecasts and the district pilot."""

CITY_POINTS = {"Петропавловск-Камчатский": (53.045, 158.648), "Елизово": (53.189, 158.381)}
PILOT_DISTRICTS = ("pk-center", "pk-seroglazka", "pk-severo-vostok", "pk-zavoyko")


def forecast_sites(districts):
    points = CITY_POINTS.copy()
    by_id = {district.id: district for district in districts}
    for district_id in PILOT_DISTRICTS:
        if district := by_id.get(district_id):
            point = district.boundary.representative_point()
            points[f"Петропавловск-Камчатский · {district.name}"] = (point.y, point.x)
    return points
