-- Schema: netlog.db
-- Generated: 2026-06-04

CREATE TABLE members (
    callsign        TEXT PRIMARY KEY NOT NULL,
    operator_name   TEXT,
    city            TEXT,
    state           TEXT,
    email           TEXT,
    sector          TEXT,
    cell_phone      TEXT,
    ham_callsign    TEXT,
    shares_cs       TEXT,
    winlink_cs      TEXT,
    vara_hf_cs      TEXT,
    notes           TEXT,
    is_synthetic    INTEGER NOT NULL DEFAULT 0,
    Latitude        REAL CHECK(Latitude  IS NULL OR (Latitude  >= 0 AND Latitude  <= 90)),
    Lat_Dir         TEXT CHECK(Lat_Dir   IS NULL OR Lat_Dir   IN ('N','S')),
    Longitude       REAL CHECK(Longitude IS NULL OR (Longitude >= 0 AND Longitude <= 180)),
    Long_Dir        TEXT CHECK(Long_Dir  IS NULL OR Long_Dir  IN ('E','W'))
);

CREATE INDEX idx_members_upper ON members (UPPER(callsign));

CREATE TABLE sectors (
    Sector TEXT PRIMARY KEY NOT NULL
);

CREATE TABLE Net_Names (
    Nets TEXT PRIMARY KEY NOT NULL
);

CREATE TABLE frequencies (
    ID        INTEGER PRIMARY KEY,
    Frequency TEXT    NOT NULL
);

CREATE TABLE Digital_Modes (
    DigitalMode TEXT PRIMARY KEY NOT NULL
);

CREATE TABLE transceivers (
    Transceiver TEXT PRIMARY KEY NOT NULL
);
