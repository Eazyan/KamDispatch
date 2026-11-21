ALTER TABLE dispatch_tasks
    ADD COLUMN assignee text,
    ADD COLUMN due_at timestamptz,
    ADD COLUMN assigned_at timestamptz,
    ADD COLUMN assigned_by text,
    ADD COLUMN accepted_at timestamptz,
    ADD COLUMN accepted_by text,
    ADD COLUMN response_seconds integer,
    ADD COLUMN assignment_version integer NOT NULL DEFAULT 0;
