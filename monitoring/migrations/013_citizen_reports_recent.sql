CREATE INDEX citizen_reports_mode_recent_idx
    ON citizen_reports (mode, created_at DESC, id DESC);
