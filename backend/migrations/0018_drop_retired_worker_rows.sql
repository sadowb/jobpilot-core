-- Retired thread-pool scheduler heartbeats are not Celery capacity or liveness.
DELETE FROM worker_processes WHERE name ~ '^worker-[0-9]+$';
