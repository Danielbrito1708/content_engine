-- Creates the orchestrator database alongside blender_worker.
-- Runs automatically on first PostgreSQL container start.
SELECT 'CREATE DATABASE orchestrator'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'orchestrator')\gexec
