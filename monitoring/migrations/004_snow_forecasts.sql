CREATE TABLE snow_forecasts (
    run_id text REFERENCES runs(id) ON DELETE CASCADE,
    calculated_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('ready', 'partial', 'unavailable')),
    payload jsonb NOT NULL,
    PRIMARY KEY (run_id, calculated_at)
);
