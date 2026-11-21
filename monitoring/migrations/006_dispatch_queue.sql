ALTER TABLE cameras ADD COLUMN district text;
ALTER TABLE cameras ADD COLUMN section_id text;
ALTER TABLE cameras ADD COLUMN section_name text;

CREATE VIEW dispatch_camera_locations AS
SELECT run_id, id AS camera_id,
       CASE WHEN section_id IS NULL THEN 'camera:' || id ELSE 'section:' || section_id END AS location_key,
       COALESCE(district, area) AS district, COALESCE(section_name, name) AS address
FROM cameras
WHERE road;

CREATE TABLE dispatch_reports (
    run_id text NOT NULL REFERENCES runs(id),
    id uuid NOT NULL,
    location_key text NOT NULL,
    district text NOT NULL,
    address text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('snowfall', 'coverage', 'drift', 'other')),
    description text NOT NULL,
    channel text NOT NULL,
    reference text NOT NULL,
    severity text NOT NULL CHECK (severity IN ('urgent', 'watch')),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    closed_at timestamptz,
    PRIMARY KEY (run_id, id)
);
CREATE INDEX dispatch_reports_open ON dispatch_reports (run_id, location_key, kind) WHERE closed_at IS NULL;

CREATE TABLE dispatch_tasks (
    run_id text NOT NULL REFERENCES runs(id),
    id uuid NOT NULL,
    location_key text NOT NULL,
    district text NOT NULL,
    address text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('snowfall', 'coverage', 'drift', 'other')),
    work_kind text NOT NULL CHECK (work_kind IN ('inspect', 'clear')),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    closed_at timestamptz,
    PRIMARY KEY (run_id, id)
);
CREATE UNIQUE INDEX dispatch_tasks_open ON dispatch_tasks (run_id, location_key, kind) WHERE closed_at IS NULL;

ALTER TABLE events ADD CONSTRAINT events_run_id_id_unique UNIQUE (run_id, id);
CREATE TABLE dispatch_task_events (
    run_id text NOT NULL,
    task_id uuid NOT NULL,
    event_id uuid NOT NULL,
    PRIMARY KEY (run_id, task_id, event_id),
    FOREIGN KEY (run_id, task_id) REFERENCES dispatch_tasks (run_id, id),
    FOREIGN KEY (run_id, event_id) REFERENCES events (run_id, id)
);
CREATE TABLE dispatch_task_reports (
    run_id text NOT NULL,
    task_id uuid NOT NULL,
    report_id uuid NOT NULL,
    PRIMARY KEY (run_id, task_id, report_id),
    FOREIGN KEY (run_id, task_id) REFERENCES dispatch_tasks (run_id, id),
    FOREIGN KEY (run_id, report_id) REFERENCES dispatch_reports (run_id, id)
);
CREATE INDEX dispatch_task_events_by_event ON dispatch_task_events (run_id, event_id);
CREATE INDEX dispatch_task_reports_by_report ON dispatch_task_reports (run_id, report_id);
