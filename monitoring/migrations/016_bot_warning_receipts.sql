CREATE TABLE bot_warning_receipts (
    run_id text NOT NULL,
    point text NOT NULL,
    recipient text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    PRIMARY KEY (run_id, point, recipient),
    FOREIGN KEY (run_id, point) REFERENCES warnings (run_id, point) ON DELETE CASCADE
);
