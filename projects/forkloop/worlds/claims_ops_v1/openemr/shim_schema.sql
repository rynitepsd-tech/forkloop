-- forkloop / claims-ops-v1 -- SQLite "shim" of the OpenEMR 8.3.0 tables forkloop touches.
--
-- Purpose: local tests cannot run MariaDB, so the *same* seeding SQL that the
-- controller sends to the VM (contract §5: portable INSERT/UPDATE/DELETE only)
-- is executed here instead.  Table and column names are copied verbatim from
-- OpenEMR 8.3.0 sql/database.sql
-- (https://raw.githubusercontent.com/openemr/openemr/v8_3_0/sql/database.sql);
-- types are simplified to INTEGER / TEXT / REAL.  Column case matters on Linux
-- MariaDB (DOB, providerID, subscriber_DOB, pc_eventDate ...) and is preserved.
--
-- Column subset = docs/contracts.md §8 plus a few real columns that a portable
-- INSERT must (or should) set on MariaDB:
--   openemr_postcalendar_events.pc_multiple      NOT NULL, no default on MariaDB
--   openemr_postcalendar_events.pc_eventstatus, pc_sharing, pc_alldayevent,
--     pc_recurrtype, pc_informant                 set by OpenEMR's own InsertEvent()
--   documents.revision                            TIMESTAMP NOT NULL, no default
--   documents.date                                 set by Document::createDocument()
--   openemr_postcalendar_categories.pc_constant_id, pc_cattype   (NOT NULL / UNIQUE)
--   users.npi, users.specialty                     provider identity shared with the portal
-- Nothing else is modelled; a real OpenEMR has ~60 more columns per table with
-- defaults, which the portable INSERTs simply leave alone.
--
-- To add a table: copy its CREATE TABLE from database.sql for the v8_3_0 tag,
-- keep the exact column names, drop everything but the columns forkloop reads
-- or writes, simplify the types, add the PRIMARY KEY, and (if OpenEMR ships
-- reference rows in database.sql that the world relies on) copy those rows
-- with their original ids.  Then extend tests/test_openemr_layer.py.

PRAGMA foreign_keys = OFF;

CREATE TABLE users (
  id            INTEGER PRIMARY KEY,
  username      TEXT,
  password      TEXT,
  authorized    INTEGER,
  fname         TEXT,
  lname         TEXT,
  facility_id   INTEGER NOT NULL DEFAULT 0,
  calendar      INTEGER NOT NULL DEFAULT 0,
  active        INTEGER NOT NULL DEFAULT 1,
  npi           TEXT,
  specialty     TEXT
);

CREATE TABLE patient_data (
  id            INTEGER PRIMARY KEY,
  pid           INTEGER NOT NULL DEFAULT 0 UNIQUE,
  pubpid        TEXT NOT NULL DEFAULT '',
  fname         TEXT NOT NULL DEFAULT '',
  lname         TEXT NOT NULL DEFAULT '',
  DOB           TEXT,
  sex           TEXT NOT NULL DEFAULT '',
  street        TEXT NOT NULL DEFAULT '',
  city          TEXT NOT NULL DEFAULT '',
  state         TEXT NOT NULL DEFAULT '',
  postal_code   TEXT NOT NULL DEFAULT '',
  phone_home    TEXT NOT NULL DEFAULT '',
  providerID    INTEGER
);

CREATE TABLE insurance_companies (
  id            INTEGER PRIMARY KEY,
  name          TEXT
);

CREATE TABLE insurance_data (
  id                      INTEGER PRIMARY KEY,
  type                    TEXT CHECK (type IN ('primary', 'secondary', 'tertiary')),
  provider                TEXT,
  plan_name               TEXT,
  policy_number           TEXT,
  group_number            TEXT,
  subscriber_fname        TEXT,
  subscriber_lname        TEXT,
  subscriber_DOB          TEXT,
  subscriber_relationship TEXT,
  subscriber_sex          TEXT,
  subscriber_street       TEXT,
  subscriber_city         TEXT,
  subscriber_state        TEXT,
  subscriber_postal_code  TEXT,
  pid                     INTEGER NOT NULL DEFAULT 0,
  date                    TEXT,
  UNIQUE (pid, type, date)
);

CREATE TABLE openemr_postcalendar_categories (
  pc_catid        INTEGER PRIMARY KEY,
  pc_constant_id  TEXT UNIQUE,
  pc_catname      TEXT,
  pc_catcolor     TEXT,
  pc_duration     INTEGER NOT NULL DEFAULT 0,
  pc_cattype      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE openemr_postcalendar_events (
  pc_eid          INTEGER PRIMARY KEY,
  pc_catid        INTEGER NOT NULL DEFAULT 0,
  pc_multiple     INTEGER NOT NULL,
  pc_aid          TEXT,
  pc_pid          TEXT,
  pc_title        TEXT,
  pc_hometext     TEXT,
  pc_informant    TEXT,
  pc_eventDate    TEXT NOT NULL,
  pc_endDate      TEXT,
  pc_duration     INTEGER NOT NULL DEFAULT 0,
  pc_recurrtype   INTEGER NOT NULL DEFAULT 0,
  pc_startTime    TEXT,
  pc_endTime      TEXT,
  pc_alldayevent  INTEGER NOT NULL DEFAULT 0,
  pc_apptstatus   TEXT NOT NULL DEFAULT '-',
  pc_eventstatus  INTEGER NOT NULL DEFAULT 0,
  pc_sharing      INTEGER NOT NULL DEFAULT 0,
  pc_facility     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE documents (
  id            INTEGER PRIMARY KEY,
  type          TEXT CHECK (type IN ('file_url', 'blob', 'web_url')),
  size          INTEGER,
  date          TEXT,
  url           TEXT,
  mimetype      TEXT,
  revision      TEXT NOT NULL,
  foreign_id    INTEGER,
  docdate       TEXT,
  hash          TEXT,
  name          TEXT
);

CREATE TABLE categories (
  id            INTEGER PRIMARY KEY,
  name          TEXT,
  parent        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE categories_to_documents (
  category_id   INTEGER NOT NULL DEFAULT 0,
  document_id   INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (category_id, document_id)
);

CREATE TABLE log (
  id            INTEGER PRIMARY KEY,
  date          TEXT,
  event         TEXT,
  category      TEXT,
  user          TEXT,
  patient_id    INTEGER,
  comments      TEXT,
  success       INTEGER DEFAULT 1
);

-- ---------------------------------------------------------------------------
-- Reference rows OpenEMR 8.3.0 ships in sql/database.sql (ids are the real ones).
-- ---------------------------------------------------------------------------

-- Calendar categories (pc_duration in seconds).  5 = Office Visit is the
-- default appointment category; 9/10 are the other patient-visit types.
INSERT INTO openemr_postcalendar_categories (pc_catid, pc_constant_id, pc_catname, pc_catcolor, pc_duration, pc_cattype) VALUES
  (1,  'no_show',                          'No Show',                          '#dee2e6', 0,     0),
  (2,  'in_office',                        'In Office',                        '#cce5ff', 0,     1),
  (3,  'out_of_office',                    'Out Of Office',                    '#fdb172', 0,     1),
  (4,  'vacation',                         'Vacation',                         '#e9ecef', 0,     1),
  (5,  'office_visit',                     'Office Visit',                     '#ffecb4', 900,   0),
  (6,  'holidays',                         'Holidays',                         '#8663ba', 86400, 2),
  (7,  'closed',                           'Closed',                           '#2374ab', 86400, 2),
  (8,  'lunch',                            'Lunch',                            '#ffd351', 3600,  1),
  (9,  'established_patient',              'Established Patient',              '#93d3a2', 900,   0),
  (10, 'new_patient',                      'New Patient',                      '#a2d9e2', 1800,  0),
  (11, 'reserved',                         'Reserved',                         '#b02a37', 900,   1),
  (12, 'health_and_behavioral_assessment', 'Health and Behavioral Assessment', '#ced4da', 900,   0),
  (13, 'preventive_care_services',         'Preventive Care Services',         '#d3c6ec', 900,   0),
  (14, 'ophthalmological_services',        'Ophthalmological Services',        '#febe89', 900,   0),
  (15, 'group_therapy',                    'Group Therapy',                    '#adb5bd', 3600,  3);

-- Document category tree (categories.id / name / parent).  1 is the root;
-- forkloop files patient documents under 3 'Medical Record' by default.
INSERT INTO categories (id, name, parent) VALUES
  (1,  'Categories',                0),
  (2,  'Lab Report',                1),
  (3,  'Medical Record',            1),
  (4,  'Patient Information',       1),
  (5,  'Patient ID card',           4),
  (6,  'Advance Directive',         1),
  (7,  'Do Not Resuscitate Order',  6),
  (8,  'Durable Power of Attorney', 6),
  (9,  'Living Will',               6),
  (10, 'Patient Photograph',        4),
  (11, 'CCR',                       1),
  (12, 'CCD',                       1),
  (13, 'CCDA',                      1),
  (14, 'Eye Module',                1),
  (15, 'Communication - Eye',       14),
  (16, 'Encounters - Eye',          14),
  (17, 'Imaging - Eye',             14),
  (18, 'OCT - Eye',                 17),
  (19, 'FA/ICG - Eye',              17),
  (20, 'External Photos - Eye',     17),
  (21, 'AntSeg Photos - Eye',       17),
  (22, 'Optic Disc - Eye',          17),
  (23, 'Fundus - Eye',              17),
  (24, 'Radiology - Eye',           17),
  (25, 'VF - Eye',                  17),
  (26, 'Drawings - Eye',            17),
  (27, 'Onsite Portal',             1),
  (28, 'Patient',                   27),
  (29, 'Reviewed',                  27),
  (30, 'FHIR Export Document',      1),
  (31, 'Invoices',                  1),
  (32, 'AntSeg Laser - Eye',        14),
  (33, 'Retina Laser - Eye',        14),
  (34, 'Injections - Eye',          14);
