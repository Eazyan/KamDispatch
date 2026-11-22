CREATE TABLE citizen_reports (
    id uuid PRIMARY KEY,
    mode text NOT NULL CHECK (mode IN ('live', 'demo', 'preview', 'replay')),
    submitted_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    district text CHECK (char_length(district) BETWEEN 1 AND 200),
    district_id text NOT NULL,
    description text NOT NULL CHECK (char_length(description) BETWEEN 1 AND 2000),
    latitude double precision NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude double precision NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    tags text[] NOT NULL DEFAULT '{}',
    suggested_tags text[] NOT NULL DEFAULT '{}',
    photo_jpeg bytea NOT NULL CHECK (octet_length(photo_jpeg) BETWEEN 1 AND 5242880)
);

CREATE INDEX citizen_reports_district_period_idx
    ON citizen_reports (mode, district_id, created_at DESC, id DESC);
