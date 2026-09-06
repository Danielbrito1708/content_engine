-- Creates the per-service databases alongside blender_worker.
-- Runs automatically on first PostgreSQL container start ONLY — on an existing
-- volume it is skipped, so new databases added here must also be created by hand:
--   docker compose exec db psql -U postgres -c "CREATE DATABASE content_scout"
SELECT 'CREATE DATABASE orchestrator'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'orchestrator')\gexec

SELECT 'CREATE DATABASE content_scout'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'content_scout')\gexec

SELECT 'CREATE DATABASE tiktok_poster'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'tiktok_poster')\gexec
