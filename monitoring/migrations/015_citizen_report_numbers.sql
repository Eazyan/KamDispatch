ALTER TABLE citizen_reports ADD COLUMN report_number bigint;

WITH numbered AS (
    SELECT id, row_number() OVER (
        PARTITION BY mode, submitted_by ORDER BY created_at, id
    ) AS report_number
    FROM citizen_reports
)
UPDATE citizen_reports AS report
SET report_number = numbered.report_number
FROM numbered
WHERE report.id = numbered.id;

ALTER TABLE citizen_reports
    ALTER COLUMN report_number SET NOT NULL,
    ADD CHECK (report_number > 0),
    ADD UNIQUE (mode, submitted_by, report_number);
