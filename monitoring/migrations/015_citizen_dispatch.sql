ALTER TABLE citizen_reports ALTER COLUMN photo_jpeg DROP NOT NULL;
CREATE INDEX citizen_reports_actor_recent_idx ON citizen_reports (mode, submitted_by, created_at DESC);
ALTER TABLE dispatch_reports ADD COLUMN citizen_report_id uuid REFERENCES citizen_reports(id) ON DELETE SET NULL;

CREATE FUNCTION sync_citizen_dispatch(report citizen_reports) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF report.mode = 'replay' THEN
        RETURN;
    END IF;
    INSERT INTO runs (id, mode) VALUES (report.mode, report.mode) ON CONFLICT DO NOTHING;
    INSERT INTO dispatch_reports (
        run_id, id, location_key, district, address, kind, description, channel, reference,
        severity, created_by, created_at, map_section, citizen_report_id
    )
    VALUES (
        report.mode, report.id, 'citizen:' || report.id, COALESCE(report.district, 'Район не определён'),
        COALESCE(NULLIF(report.address, ''), format('%s, %s', round(report.latitude::numeric, 5), round(report.longitude::numeric, 5))),
        CASE WHEN 'сугроб' = ANY(report.tags) THEN 'drift'
             WHEN 'снег' = ANY(report.tags) OR (cardinality(report.tags) = 0
                  AND 'уборка_снега' = ANY(report.suggested_tags)) THEN 'coverage'
             ELSE 'other' END,
        report.description, 'online', report.id::text, 'watch', 'citizen', report.created_at,
        jsonb_build_object('coordinates', jsonb_build_array(jsonb_build_array(report.longitude, report.latitude))), report.id
    )
    ON CONFLICT (run_id, id) DO UPDATE SET district=EXCLUDED.district
    WHERE dispatch_reports.citizen_report_id=EXCLUDED.citizen_report_id;
END;
$$;

SELECT sync_citizen_dispatch(citizen_reports) FROM citizen_reports;

CREATE FUNCTION citizen_dispatch_trigger() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM sync_citizen_dispatch(NEW);
    RETURN NEW;
END;
$$;

CREATE TRIGGER citizen_report_to_dispatch
AFTER INSERT OR UPDATE OF district_id, district ON citizen_reports
FOR EACH ROW EXECUTE FUNCTION citizen_dispatch_trigger();
