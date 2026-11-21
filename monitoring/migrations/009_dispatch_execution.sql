ALTER TABLE dispatch_tasks
    ADD COLUMN stage text NOT NULL DEFAULT 'draft'
        CHECK (stage IN ('draft','sent','accepted','in_progress','done','returned','closed')),
    ADD COLUMN severity text NOT NULL DEFAULT 'watch' CHECK (severity IN ('urgent','watch')),
    ADD COLUMN instructions text NOT NULL DEFAULT '',
    ADD COLUMN result text NOT NULL DEFAULT '',
    ADD COLUMN completed_at timestamptz,
    ADD COLUMN crew_token text,
    ADD COLUMN previous_task_id uuid,
    ADD FOREIGN KEY (run_id, previous_task_id) REFERENCES dispatch_tasks (run_id,id);
UPDATE dispatch_tasks
SET stage=CASE WHEN closed_at IS NOT NULL THEN 'closed' WHEN accepted_at IS NOT NULL THEN 'accepted' ELSE 'sent' END
WHERE assignee IS NOT NULL OR closed_at IS NOT NULL;
UPDATE dispatch_tasks
SET crew_token=rtrim(translate(encode(uuid_send(gen_random_uuid()) || uuid_send(gen_random_uuid()),'base64'),'+/','-_'),'=')
WHERE assignee IS NOT NULL AND closed_at IS NULL;

ALTER TABLE dispatch_reports
    ADD COLUMN resolution text NOT NULL DEFAULT '',
    ADD COLUMN closed_by text;

CREATE UNIQUE INDEX dispatch_tasks_crew_token ON dispatch_tasks (crew_token) WHERE crew_token IS NOT NULL;
CREATE INDEX dispatch_reports_resolution ON dispatch_reports (run_id,closed_at DESC) WHERE closed_at IS NOT NULL;
CREATE INDEX dispatch_tasks_closed ON dispatch_tasks (run_id,closed_at DESC) WHERE closed_at IS NOT NULL;

CREATE TABLE dispatch_task_history (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id text NOT NULL,
    task_id uuid NOT NULL,
    action text NOT NULL,
    actor text NOT NULL,
    note text NOT NULL,
    assignee text,
    due_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (run_id,task_id) REFERENCES dispatch_tasks (run_id,id)
);
CREATE INDEX dispatch_task_history_lookup ON dispatch_task_history (run_id, task_id, id);
