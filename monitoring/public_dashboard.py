"""Read-only deployment proxy for the filtered public dashboard."""
import os

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

PUBLIC_FIELDS = ("mode", "run_id", "cameras", "forecasts", "weather_source", "forecast_points",
                 "warnings", "bus_observations", "bus_map", "snow_forecast")
PUBLIC_EMPTY = {
    "events": [], "summaries": [], "checks": [], "deliveries": [], "queue": {"pending": 0},
    "inspection_queue": [], "feedback_counts": {}, "dispatch_queue": [], "dispatch_locations": [],
    "dispatch_assignees": [],
}


def create_app(upstream, city_map_url):
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.get("/api/v1/dashboard")
    async def dashboard():
        headers = {"Cache-Control": "private, no-store"}
        try:
            async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
                response = await client.get(upstream + "/api/v1/dashboard")
            if response.status_code == 503 and response.json().get("code") == "database_unavailable":
                return JSONResponse(response.json(), status_code=503, headers={**headers, "Retry-After": "5"})
            response.raise_for_status()
            data = response.json()
            public = {key: data[key] for key in PUBLIC_FIELDS}
        except (httpx.HTTPError, ValueError, TypeError, AttributeError, KeyError):
            return JSONResponse(
                {"detail": "Панель временно недоступна", "code": "dashboard_unavailable",
                 "status_url": "/api/v1/status"}, status_code=503, headers={**headers, "Retry-After": "5"},
            )
        public.update(PUBLIC_EMPTY, role="viewer", city_map_url=city_map_url)
        return JSONResponse(public, headers=headers)

    return app


app = create_app(os.getenv("PUBLIC_DASHBOARD_UPSTREAM", "http://api:8080").rstrip("/"),
                 os.getenv("CITY_MAP_URL", "/city/"))
