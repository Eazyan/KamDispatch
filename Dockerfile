FROM node:22-bookworm-slim AS web
WORKDIR /src
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app \
    && install -o app -g app -d /app/runtime/media
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY monitoring/ ./monitoring/
COPY city-sim/data/pkc/city.osm.gz city-sim/data/pkc/depot-east.osm.gz ./city-sim/data/pkc/
COPY city-sim/weather_coverage/osm/*.osm.gz ./city-sim/weather_coverage/osm/
COPY city-sim/shared/universal_search ./city-sim/shared/universal_search/
COPY snegoplan/mapprep/out/graph_v3.json ./snegoplan/mapprep/out/
COPY resident_site/ ./resident_site/
COPY cameras/ ./cameras/
COPY --from=web /src/dist ./web/dist
USER app
EXPOSE 8080
CMD ["uvicorn", "monitoring.api:app", "--host", "0.0.0.0", "--port", "8080"]
