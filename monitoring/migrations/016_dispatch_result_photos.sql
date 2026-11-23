CREATE TABLE dispatch_result_photos (
    id uuid PRIMARY KEY,
    run_id text NOT NULL,
    task_id uuid NOT NULL,
    completed_at timestamptz NOT NULL,
    position smallint NOT NULL CHECK (position BETWEEN 1 AND 3),
    photo_jpeg bytea NOT NULL CHECK (octet_length(photo_jpeg) BETWEEN 1 AND 5242880),
    FOREIGN KEY (run_id, task_id) REFERENCES dispatch_tasks (run_id, id) ON DELETE CASCADE,
    UNIQUE (run_id, task_id, completed_at, position)
);
