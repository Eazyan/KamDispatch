ALTER TABLE dispatch_tasks
    ADD COLUMN claimed_by text,
    ADD COLUMN assignment_notice_version integer NOT NULL DEFAULT 0;

UPDATE dispatch_tasks SET assignment_notice_version=assignment_version
WHERE assignee IS NOT NULL AND closed_at IS NULL AND stage IN ('sent','accepted');

CREATE TABLE dispatch_task_notice_receipts (
    run_id text NOT NULL,
    task_id uuid NOT NULL,
    recipient text NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    PRIMARY KEY (run_id,task_id,recipient),
    FOREIGN KEY (run_id,task_id) REFERENCES dispatch_tasks (run_id,id) ON DELETE CASCADE
);

CREATE TABLE dispatch_task_photos (
    run_id text NOT NULL,
    task_id uuid NOT NULL,
    photo_jpeg bytea NOT NULL CHECK (octet_length(photo_jpeg) BETWEEN 1 AND 5242880),
    PRIMARY KEY (run_id,task_id),
    FOREIGN KEY (run_id,task_id) REFERENCES dispatch_tasks (run_id,id) ON DELETE CASCADE
);
