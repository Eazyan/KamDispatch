ALTER TABLE snow_forecasts
    ADD COLUMN scope text NOT NULL DEFAULT 'pilot' CHECK (scope IN ('pilot', 'city')),
    ADD COLUMN summary jsonb;

CREATE INDEX snow_forecasts_scope_recent ON snow_forecasts (run_id, scope, calculated_at DESC);
