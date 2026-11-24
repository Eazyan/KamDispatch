import json
from urllib.request import urlopen
from fastapi import FastAPI
app = FastAPI(openapi_url=None)
@app.get("/api/v1/dashboard")
def dashboard():
    with urlopen("http://api:8080/api/v1/dashboard", timeout=15) as response:
        data = json.load(response)
    public = {k: data[k] for k in ("mode", "run_id", "cameras", "forecasts", "forecast_points", "warnings", "bus_observations", "bus_map", "snow_forecast")}
    public.update(role="viewer", city_map_url="__PUBLIC_CITY_MAP_URL__", events=[], summaries=[], checks=[], deliveries=[], queue={"pending":0}, inspection_queue=[], feedback_counts={}, dispatch_queue=[], dispatch_locations=[], dispatch_assignees=[])
    return public
