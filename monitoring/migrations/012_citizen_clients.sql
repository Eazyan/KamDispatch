ALTER TABLE citizen_reports
    ADD COLUMN address text NOT NULL DEFAULT '' CHECK (char_length(address) <= 240),
    ADD COLUMN request_id uuid;

CREATE UNIQUE INDEX citizen_reports_request_idx
    ON citizen_reports (mode, submitted_by, request_id);

CREATE INDEX citizen_reports_owner_idx
    ON citizen_reports (mode, submitted_by, created_at DESC, id DESC);
