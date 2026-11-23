UPDATE dispatch_reports AS dispatch
SET citizen_report_id = report.id,
    map_section = COALESCE(dispatch.map_section, jsonb_build_object(
        'coordinates', jsonb_build_array(jsonb_build_array(report.longitude, report.latitude))))
FROM citizen_reports AS report
WHERE (dispatch.run_id, dispatch.id) = (report.mode, report.id)
  AND dispatch.citizen_report_id IS NULL
  AND dispatch.created_by = report.submitted_by
  AND dispatch.channel = 'online';

INSERT INTO dispatch_result_photos (id, run_id, task_id, completed_at, position, photo_jpeg)
SELECT gen_random_uuid(), t.run_id, t.id, t.completed_at, free.position, legacy.photo_jpeg
FROM dispatch_task_photos AS legacy
JOIN dispatch_tasks AS t ON (t.run_id, t.id) = (legacy.run_id, legacy.task_id)
CROSS JOIN LATERAL (
    SELECT position FROM generate_series(1, 3) AS slot(position)
    WHERE NOT EXISTS (
        SELECT 1 FROM dispatch_result_photos AS p
        WHERE (p.run_id, p.task_id, p.completed_at, p.position) =
              (t.run_id, t.id, t.completed_at, slot.position)
    )
    ORDER BY position LIMIT 1
) AS free
WHERE t.completed_at IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM dispatch_result_photos AS p
      WHERE (p.run_id, p.task_id, p.completed_at) = (t.run_id, t.id, t.completed_at)
        AND p.photo_jpeg = legacy.photo_jpeg
  )
ON CONFLICT (run_id, task_id, completed_at, position) DO NOTHING;
