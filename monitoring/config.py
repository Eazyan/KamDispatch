import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
LOCAL_TZ = ZoneInfo("Asia/Kamchatka")


def messenger_ids(variable):
    return tuple(dict.fromkeys(item.strip() for item in os.getenv(variable, "").split(",") if item.strip()))


def messenger_crews():
    return json.loads(os.getenv("DISPATCH_BOT_CREWS") or "{}")


@dataclass(frozen=True)
class Settings:
    database: str = os.getenv("DATABASE_URL", "")
    mode: str = os.getenv("MONITOR_MODE", "demo")
    media_dir: Path = Path(os.getenv("MEDIA_DIR", str(ROOT / "runtime" / "media")))
    citizen_bot_api_key: str = os.getenv("CITIZEN_BOT_API_KEY", "")
    citizen_districts_path: Path | None = Path(os.environ["CITIZEN_DISTRICTS_FILE"]) if os.getenv("CITIZEN_DISTRICTS_FILE") else None
    s3_bucket: str = os.getenv("S3_BUCKET", "")
    s3_endpoint: str = os.getenv("S3_ENDPOINT", "")
    model_url: str = os.getenv("MODEL_URL", "").rstrip("/")
    model_contract: str = os.getenv("MODEL_CONTRACT", "scene")
    model_token: str = os.getenv("MODEL_TOKEN", "")
    weather_key: str = os.getenv("OPENWEATHER_KEY", "")
    snow_sim_url: str = os.getenv("SNOW_SIM_URL", "").strip().rstrip("/")
    snow_city_url: str = os.getenv("SNOW_CITY_URL", "").strip().rstrip("/")
    snow_city_remote_url: str = os.getenv("SNOW_CITY_REMOTE_URL", "").strip().rstrip("/")
    city_map_url: str = os.getenv("CITY_MAP_URL", "").strip()
    snow_segments_path: Path = Path(os.getenv("SNOW_SEGMENTS_FILE", str(ROOT / "monitoring" / "snow_segments.json")))
    cameras_path: Path = Path(os.getenv("MONITOR_CAMERAS", str(ROOT / "monitoring" / "cameras.json")))
    bus_rnis_enabled: bool | None = (os.getenv("BUS_RNIS_ENABLED") == "1") if os.getenv("BUS_RNIS_ENABLED") else None
    bus_sections_path: Path | None = Path(os.environ["BUS_SECTIONS_FILE"]) if os.getenv("BUS_SECTIONS_FILE") else None
    base_url: str = os.getenv("PUBLIC_URL", "http://localhost:8080").rstrip("/")
    oidc_issuer: str = os.getenv("OIDC_ISSUER", "").rstrip("/")
    oidc_jwks: str = os.getenv("OIDC_JWKS_URL", "")
    oidc_audience: str = os.getenv("OIDC_AUDIENCE", "")
    operators: tuple = tuple(filter(None, os.getenv("OPERATOR_SUBJECTS", "").split(",")))
    viewers: tuple = tuple(filter(None, os.getenv("VIEWER_SUBJECTS", "").split(",")))
    crew_subjects: dict[str, str] = field(default_factory=lambda: json.loads(os.getenv("DISPATCH_CREW_SUBJECTS") or "{}"))
    dispatch_bot_crews: dict[str, str] = field(default_factory=messenger_crews)
    dispatch_telegram_admins: tuple[str, ...] = field(
        default_factory=lambda: messenger_ids("DISPATCH_TELEGRAM_ADMINS"),
    )
    dispatch_max_operators: tuple[str, ...] = field(
        default_factory=lambda: messenger_ids("DISPATCH_MAX_OPERATORS"),
    )
    recipients: tuple = tuple(sorted(set(value.strip() for value in os.getenv("MAIL_TO", "").split(",") if value.strip())))
    mail_test: bool = os.getenv("MONITOR_MAIL_TEST") == "1"
    mail_host: str = os.getenv("SMTP_HOST", "")
    mail_port: int = int(os.getenv("SMTP_PORT", "587"))
    mail_security: str = os.getenv("SMTP_SECURITY", "starttls")
    mail_user: str = os.getenv("SMTP_USER", "")
    mail_password: str = os.getenv("SMTP_PASSWORD", "")
    mail_from: str = os.getenv("MAIL_FROM", "monitor@localhost")
    snow_probability: float = float(os.getenv("SNOW_PROBABILITY", "0.5"))
    summary_hour: int = int(os.getenv("SUMMARY_HOUR", "7"))
    freshness: int = int(os.getenv("FRAME_FRESHNESS_SECONDS", "90"))

    @property
    def collect_buses(self):
        return self.mode == "preview" if self.bus_rnis_enabled is None else self.bus_rnis_enabled

    @property
    def city_snow(self):
        return bool(self.snow_city_url or self.snow_city_remote_url)

    def validate(self):
        if not self.database:
            raise ValueError("DATABASE_URL is required")
        if self.mode not in {"demo", "replay", "preview", "live"}:
            raise ValueError("MONITOR_MODE must be demo, replay, preview, or live")
        if self.model_contract not in {"snow", "scene", "scene_v2"} or self.mode == "live" and self.model_contract == "snow":
            raise ValueError("Invalid MODEL_CONTRACT for this mode")
        if self.mail_security not in {"none", "starttls", "ssl"}:
            raise ValueError("Invalid SMTP_SECURITY")
        if self.mail_test and (
            self.mode != "preview" or (self.mail_host, self.mail_port, self.mail_security) != ("mailpit", 1025, "none")
        ):
            raise ValueError("MONITOR_MAIL_TEST requires preview and the local Mailpit service")
        if not 0 <= self.summary_hour < 24 or not 0 <= self.snow_probability <= 1 or self.freshness < 10:
            raise ValueError("Invalid monitoring threshold or schedule")
        if self.mode == "live" and not all([
            self.oidc_issuer.startswith("https://"), self.oidc_jwks.startswith("https://"),
            self.oidc_audience, self.operators or self.viewers,
            self.model_url.startswith("https://"), self.base_url.startswith("https://"),
        ]):
            raise ValueError("Live requires OIDC, HTTPS MODEL_URL, and HTTPS PUBLIC_URL")
        if self.mode == "live" and self.recipients and (not self.mail_host or self.mail_security == "none"):
            raise ValueError("Live mail requires a configured TLS SMTP transport")
        if not isinstance(self.crew_subjects, dict) or any(
            not subject or not isinstance(label, str) or not label.strip() or label != label.strip() or len(label) > 150
            for subject, label in self.crew_subjects.items()
        ):
            raise ValueError("DISPATCH_CREW_SUBJECTS must map OIDC subjects to assignee names")

        if not isinstance(self.dispatch_bot_crews, dict) or any(
            not re.fullmatch(r"(?:telegram|max):[1-9][0-9]{0,18}", actor)
            or not isinstance(label, str) or not label.strip() or label != label.strip() or len(label) > 150
            for actor, label in self.dispatch_bot_crews.items()
        ):
            raise ValueError("DISPATCH_BOT_CREWS must map messenger accounts to assignee names")
        for platform, ids in (("telegram", self.dispatch_telegram_admins), ("max", self.dispatch_max_operators)):
            if any(not re.fullmatch(r"[1-9][0-9]{0,18}", ident)
                   or f"{platform}:{ident}" in self.dispatch_bot_crews for ident in ids):
                raise ValueError("Administrative messenger roles require unique positive account IDs")

    def cameras(self):
        sources = {source["id"]: source for source in json.loads((ROOT / "cameras" / "sources.json").read_text())}
        items = json.loads(self.cameras_path.read_text())
        if len({item["id"] for item in items}) != len(items):
            raise ValueError("Camera ids must be unique")
        sections = {}
        for item in items:
            camera = {**sources.get(item["id"], {}), **item}
            if (camera.get("kind") not in {"hls", "jpeg"} or not camera.get("url")
                    or not isinstance(camera.get("interval_seconds"), int) or camera["interval_seconds"] < 10
                    or not isinstance(camera.get("road"), bool) or not camera.get("name") or not camera.get("area")):
                raise ValueError(f"Invalid camera configuration: {camera['id']}")
            weight = camera.get("road_priority", 0)
            if type(weight) is not int or not 0 <= weight <= 3 or weight and (
                not camera["road"] or not isinstance(camera.get("road_priority_reason"), str)
                or not camera["road_priority_reason"].strip()
            ):
                raise ValueError(f"Invalid road priority: {camera['id']}")
            for field in ("district", "section_id", "section_name"):
                value = camera.get(field)
                if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 200):
                    raise ValueError(f"Invalid {field}: {camera['id']}")
                if value is not None:
                    camera[field] = value.strip()
            section = camera.get("section_id")
            section_name = camera.get("section_name")
            if bool(section) != bool(section_name) or (section and not camera["road"]):
                raise ValueError(f"Invalid road section: {camera['id']}")
            if section:
                location = (camera.get("district") or camera["area"], section_name)
                if sections.setdefault(section, location) != location:
                    raise ValueError(f"Conflicting configuration for road section: {section}")
            coordinates = camera.get("coordinates")
            if coordinates is not None and (
                not isinstance(coordinates, list) or len(coordinates) != 2
                or any(type(value) not in (int, float) for value in coordinates)
                or not -180 <= coordinates[0] <= 180 or not -90 <= coordinates[1] <= 90
            ):
                raise ValueError(f"Invalid camera coordinates: {camera['id']}")
            yield camera
