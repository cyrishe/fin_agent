-- SUPERSEDED: current deployment uses stock_agent; do not run for this migration.
-- Historical separate-schema option, retained for reference only.
-- Run by a DBA on 47.94.1.2:3312, not by the application account.
-- Reuse the existing cubeyz@% account; do not create a user or change its password.
-- This grants rights only on the dedicated Fin Agent system schema.
CREATE DATABASE IF NOT EXISTS aiia_system
  CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;

GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES
  ON aiia_system.* TO 'cubeyz'@'%';
