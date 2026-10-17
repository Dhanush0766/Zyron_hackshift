"""SQLite persistence (stdlib only)."""
from __future__ import annotations

import json
import sqlite3
import threading

SCHEMA_VERSION = 5

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS devices(
  id TEXT PRIMARY KEY, name TEXT, kind TEXT NOT NULL CHECK(kind IN ('SIMULATOR','HARDWARE')),
  last_seen REAL, fw TEXT, rssi INTEGER, status_json TEXT, created_at REAL,
  patient_name TEXT, patient_tag TEXT, language TEXT NOT NULL DEFAULT 'en');
CREATE TABLE IF NOT EXISTS medications(
  id INTEGER PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(id), name TEXT NOT NULL,
  compartment INTEGER NOT NULL CHECK(compartment BETWEEN 1 AND 6),
  unit_weight_g REAL NOT NULL, weight_tol_g REAL NOT NULL,
  rules_json TEXT NOT NULL, notes TEXT, created_at REAL,
  stock INTEGER NOT NULL DEFAULT 30 CHECK(stock >= 0), refill_threshold INTEGER NOT NULL DEFAULT 5,
  drug_class TEXT NOT NULL DEFAULT 'GENERAL', requires_meal INTEGER NOT NULL DEFAULT 0,
  min_interval_h REAL NOT NULL DEFAULT 4, late_window_min REAL NOT NULL DEFAULT 50,
  expiry_date TEXT,
  UNIQUE(device_id, compartment));
CREATE TABLE IF NOT EXISTS schedules(
  id INTEGER PRIMARY KEY, medication_id INTEGER NOT NULL REFERENCES medications(id),
  time_hhmm TEXT NOT NULL, food TEXT NOT NULL DEFAULT 'ANY' CHECK(food IN ('BEFORE_FOOD','AFTER_FOOD','ANY')),
  session TEXT NOT NULL, days TEXT NOT NULL DEFAULT 'MON,TUE,WED,THU,FRI,SAT,SUN',
  active INTEGER NOT NULL DEFAULT 1, created_at REAL);
CREATE TABLE IF NOT EXISTS doses(
  id INTEGER PRIMARY KEY, schedule_id INTEGER REFERENCES schedules(id),
  medication_id INTEGER NOT NULL REFERENCES medications(id), device_id TEXT NOT NULL,
  due_at REAL NOT NULL, state TEXT NOT NULL, state_since REAL NOT NULL,
  gate_attempts INTEGER NOT NULL DEFAULT 0, gate_decision TEXT, gate_reasons TEXT,
  physio_hold INTEGER NOT NULL DEFAULT 0,
  ir_beam INTEGER, tray_delta_g REAL, delivery_verdict TEXT, collection_verdict TEXT,
  escalation_level INTEGER NOT NULL DEFAULT 0, escalation_since REAL,
  reminder_lead_s INTEGER NOT NULL DEFAULT 0,
  reviewer TEXT, review_note TEXT, fault TEXT,
  session TEXT NOT NULL DEFAULT 'MORNING', food TEXT NOT NULL DEFAULT 'ANY',
  tighten_steps INTEGER NOT NULL DEFAULT 0, gate_fail_rate REAL NOT NULL DEFAULT 0,
  escalation_window_s REAL, alert_volume INTEGER, plan_json TEXT,
  dispensed_at REAL, delivered_at REAL, collected_at REAL, created_at REAL,
  UNIQUE(schedule_id, due_at));
CREATE TABLE IF NOT EXISTS vitals(
  id INTEGER PRIMARY KEY, device_id TEXT NOT NULL, hr REAL, spo2 REAL, bp_sys REAL, bp_dia REAL,
  quality REAL, measured_at REAL, received_at REAL, status TEXT, reasons TEXT, source TEXT,
  patient_tag TEXT, meal_taken INTEGER);
CREATE TABLE IF NOT EXISTS commands(
  id INTEGER PRIMARY KEY, device_id TEXT NOT NULL, dose_id INTEGER NOT NULL UNIQUE REFERENCES doses(id),
  kind TEXT NOT NULL, payload TEXT, status TEXT NOT NULL, created_at REAL, sent_at REAL, acked_at REAL);
CREATE TABLE IF NOT EXISTS device_events(
  id INTEGER PRIMARY KEY, device_id TEXT NOT NULL, seq INTEGER NOT NULL, type TEXT NOT NULL,
  device_ts REAL, payload TEXT, received_at REAL, result TEXT, UNIQUE(device_id, seq));
CREATE TABLE IF NOT EXISTS escalations(
  id INTEGER PRIMARY KEY, dose_id INTEGER NOT NULL REFERENCES doses(id), level INTEGER NOT NULL,
  channel TEXT NOT NULL, reason TEXT, created_at REAL, delivery TEXT);
CREATE TABLE IF NOT EXISTS gate_tightening(
  medication_id INTEGER NOT NULL REFERENCES medications(id), session TEXT NOT NULL,
  steps INTEGER NOT NULL DEFAULT 0, updated_at REAL, PRIMARY KEY(medication_id, session));
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, device_id TEXT, medication_id INTEGER,
  message TEXT NOT NULL, value REAL, created_at REAL NOT NULL, resolved_at REAL, resolved_by TEXT);
CREATE TABLE IF NOT EXISTS env_readings(
  id INTEGER PRIMARY KEY, device_id TEXT NOT NULL, temp_c REAL, humidity REAL,
  measured_at REAL, received_at REAL, status TEXT);
CREATE TABLE IF NOT EXISTS prompts(
  id INTEGER PRIMARY KEY, device_id TEXT NOT NULL, dose_id INTEGER, key TEXT NOT NULL, lang TEXT NOT NULL,
  text TEXT NOT NULL, clip TEXT NOT NULL, fallback INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'PENDING', created_at REAL, sent_at REAL);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
  dose_id INTEGER, detail TEXT, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
"""


class DB:
    def __init__(self, path: str = ":memory:"):
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        ver = self.conn.execute("PRAGMA user_version").fetchone()[0]
        has_tables = self.conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        if has_tables and ver != SCHEMA_VERSION:
            # Prototype database from an older version: rebuild it (demo data only).
            print(f"medgate: database schema v{ver} is outdated (need v{SCHEMA_VERSION}); rebuilding demo database")
            self.conn.execute("PRAGMA foreign_keys=OFF")
            for (t,) in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
                self.conn.execute(f"DROP TABLE IF EXISTS {t}")
        self.conn.executescript(SCHEMA)
        self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def q(self, sql: str, args=()) -> list:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args=()):
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql: str, args=()) -> int:
        with self.lock:
            return self.conn.execute(sql, args).lastrowid


def dumps(o) -> str:
    return json.dumps(o, separators=(",", ":"), sort_keys=True, default=str)
