CREATE TABLE bus_positions (
    run_id text NOT NULL REFERENCES runs(id),
    vehicle text NOT NULL,
    measured_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL,
    lon double precision NOT NULL,
    lat double precision NOT NULL,
    PRIMARY KEY (run_id, vehicle, measured_at)
);
CREATE INDEX bus_positions_by_time ON bus_positions (run_id, measured_at);

CREATE TABLE bus_passages (
    run_id text NOT NULL REFERENCES runs(id),
    section text NOT NULL,
    direction text NOT NULL,
    vehicle text NOT NULL,
    entered_at timestamptz NOT NULL,
    ended_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL,
    PRIMARY KEY (run_id, section, direction, vehicle, entered_at)
);
CREATE INDEX bus_passages_by_time ON bus_passages (run_id, ended_at);

CREATE TABLE bus_hints (
    run_id text NOT NULL REFERENCES runs(id),
    section text NOT NULL,
    direction text NOT NULL,
    opened_at timestamptz NOT NULL,
    closed_at timestamptz,
    valid_until timestamptz NOT NULL,
    evidence jsonb NOT NULL,
    PRIMARY KEY (run_id, section, direction, opened_at)
);
CREATE UNIQUE INDEX bus_hints_open ON bus_hints (run_id, section, direction) WHERE closed_at IS NULL;

CREATE TABLE bus_snapshots (
    run_id text PRIMARY KEY REFERENCES runs(id),
    observed_at timestamptz NOT NULL,
    state text NOT NULL,
    rows jsonb NOT NULL
);

ALTER TABLE dispatch_reports ADD COLUMN passage_obstructed boolean NOT NULL DEFAULT false;
