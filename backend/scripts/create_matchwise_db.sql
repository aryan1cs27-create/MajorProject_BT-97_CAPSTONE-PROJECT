-- MatchWise — local MySQL database setup
-- Run once with:  mysql -u root -p < scripts/create_matchwise_db.sql
-- (or paste into MySQL Workbench / DBeaver and execute).

CREATE DATABASE IF NOT EXISTS matchwise_db
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

-- Optional: a dedicated least-privilege user for local development.
-- Uncomment and change the password before running.
-- CREATE USER IF NOT EXISTS 'matchwise'@'localhost' IDENTIFIED BY 'CHANGE_ME';
-- GRANT ALL PRIVILEGES ON matchwise_db.* TO 'matchwise'@'localhost';
-- FLUSH PRIVILEGES;

SHOW DATABASES LIKE 'matchwise_db';
