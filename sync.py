"""
sync.py — GitHub backup bridge for MockTest.pro
Run locally or as a Render Background Worker to persist data across restarts.

Usage:
  python sync.py backup          # push DB → GitHub (JSON snapshots)
  python sync.py backup --force  # push even if DB looks empty
  python sync.py restore         # pull snapshots → DB
  python sync.py status          # show config + row counts

Environment variables (set in Render → Environment):
  GITHUB_TOKEN   Personal Access Token with 'repo' scope
  GITHUB_REPO    "owner/repo" (can be private)
  GITHUB_BRANCH  optional, defaults to "main"
"""

import argparse
import base64
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request

BACKUP_DIR = "backup"
SNAPSHOTS = ["questions", "users", "attempts", "user_stats", "weak_questions", "sessions"]
TIMEOUT = 20

def resolve_db_path():
    base = os.path.dirname(os.path.abspath(__file__))
    for candidate in (os.path.join(base, "mocktest.db"), "/tmp/mocktest.db"):
        try:
            if not os.path.exists(candidate):
                with open(candidate, "a"):
                    pass
            return candidate
        except OSError:
            continue
    return os.path.join(base, "mocktest.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT NOT NULL,
    topic       TEXT NOT NULL,
    question    TEXT NOT NULL,
    options     TEXT NOT NULL,
    correct     INTEGER NOT NULL,
    explanation TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    email         TEXT UNIQUE NOT NULL,
    first_name    TEXT NOT NULL,
    last_name     TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    salt          TEXT NOT NULL,
    is_admin      INTEGER DEFAULT 0,
    banned        INTEGER DEFAULT 0,
    created_at    INTEGER
);
CREATE TABLE IF NOT EXISTS attempts (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    category TEXT,
    topic    TEXT,
    total    INTEGER,
    correct  INTEGER,
    wrong    INTEGER,
    skipped  INTEGER,
    pct      REAL,
    time_sec INTEGER,
    mode     TEXT,
    ts       INTEGER
);
CREATE TABLE IF NOT EXISTS weak_questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT,
    question_id INTEGER,
    wrong_count INTEGER DEFAULT 1,
    last_wrong  INTEGER
);
CREATE TABLE IF NOT EXISTS user_stats (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT UNIQUE,
    xp          INTEGER DEFAULT 0,
    streak      INTEGER DEFAULT 0,
    last_active INTEGER,
    level       INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    created_at INTEGER
);
"""

TABLES = ["questions", "users", "attempts", "weak_questions", "user_stats", "sessions"]

def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    return db

def ensure_schema(path):
    with connect(path) as db:
        db.executescript(SCHEMA)
        db.commit()

def count_rows(path, table):
    with connect(path) as db:
        return db.execute("SELECT COUNT(*) AS n FROM " + table).fetchone()["n"]

def is_fresh(path):
    ensure_schema(path)
    return count_rows(path, "questions") == 0 and count_rows(path, "attempts") == 0

def export_db(path):
    ensure_schema(path)
    data = {}
    with connect(path) as db:
        for table in TABLES:
            data[table] = [dict(r) for r in db.execute("SELECT * FROM " + table).fetchall()]
    return data

def import_db(path, data):
    ensure_schema(path)
    with connect(path) as db:
        for table in TABLES:
            db.execute("DELETE FROM " + table)
        for table in TABLES:
            rows = data.get(table) or []
            if not rows:
                continue
            cols = list(rows[0].keys())
            placeholders = ",".join(["?"] * len(cols))
            db.executemany(
                "INSERT INTO " + table + " (" + ",".join(cols) + ") VALUES (" + placeholders + ")",
                [[r[c] for c in cols] for r in rows],
            )
        db.commit()
    return {t: len(data.get(t) or []) for t in TABLES}

def env_config():
    return {
        "token": os.getenv("GITHUB_TOKEN", "").strip(),
        "repo": os.getenv("GITHUB_REPO", "").strip(),
        "branch": os.getenv("GITHUB_BRANCH", "main").strip() or "main",
        "api": os.getenv("GITHUB_API", "https://api.github.com").strip(),
    }

def configured():
    cfg = env_config()
    return bool(cfg["token"] and cfg["repo"])

def _gh_request(method, url_path, cfg, payload=None):
    url = cfg["api"] + url_path
    headers = {
        "Authorization": "Bearer " + cfg["token"],
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception as e:
        raise RuntimeError("GitHub request failed: " + str(e)) from e

def _snapshot_path(name):
    return BACKUP_DIR + "/" + name + ".json"

def gh_get_file(cfg, name):
    path = _snapshot_path(name)
    status, body = _gh_request(
        "GET", "/repos/" + cfg["repo"] + "/contents/" + path + "?ref=" + cfg["branch"], cfg
    )
    if status == 200:
        return json.loads(base64.b64decode(body["content"]).decode("utf-8")), body.get("sha")
    if status == 404:
        return None, None
    raise RuntimeError("GET " + path + " -> HTTP " + str(status))

def gh_put_file(cfg, name, data, sha=None):
    path = _snapshot_path(name)
    payload = {
        "message": "MockTest.pro backup " + name + " (" + str(len(data)) + " rows)",
        "content": base64.b64encode(json.dumps(data, ensure_ascii=False).encode("utf-8")).decode("ascii"),
        "branch": cfg["branch"],
    }
    if sha:
        payload["sha"] = sha
    status, _ = _gh_request("PUT", "/repos/" + cfg["repo"] + "/contents/" + path, cfg, payload)
    if status not in (200, 201):
        raise RuntimeError("PUT " + path + " -> HTTP " + str(status))
    return status

def do_backup(path=None, force=False):
    if not configured():
        raise RuntimeError("GitHub sync not configured — set GITHUB_TOKEN and GITHUB_REPO")
    path = path or resolve_db_path()
    if not force and is_fresh(path):
        return {"skipped": True, "reason": "database is empty; nothing to back up"}
    data = export_db(path)
    pushed = {}
    for name in SNAPSHOTS:
        _, sha = gh_get_file(cfg=env_config(), name=name)
        gh_put_file(cfg=env_config(), name=name, data=data[name], sha=sha)
        pushed[name] = len(data[name])
    return {"skipped": False, "pushed": pushed}

def do_restore(path=None):
    if not configured():
        raise RuntimeError("GitHub sync not configured — set GITHUB_TOKEN and GITHUB_REPO")
    path = path or resolve_db_path()
    data = {}
    for name in SNAPSHOTS:
        content, _ = gh_get_file(cfg=env_config(), name=name)
        if content is not None:
            data[name] = content
    if not data:
        return {"restored": {}, "note": "no snapshots on GitHub yet"}
    counts = import_db(path, data)
    return {"restored": counts}

def restore_if_needed(path=None):
    path = path or resolve_db_path()
    if not configured():
        return {"restored": False, "reason": "not configured"}
    ensure_schema(path)
    has_user_data = (
        count_rows(path, "attempts") > 0
        or count_rows(path, "user_stats") > 0
        or count_rows(path, "weak_questions") > 0
    )
    if has_user_data:
        return {"restored": False, "reason": "local data already present"}
    return do_restore(path)

def status(path=None):
    cfg = env_config()
    path = path or resolve_db_path()
    ensure_schema(path)
    return {
        "configured": configured(),
        "repo": cfg["repo"] or "(not set)",
        "branch": cfg["branch"],
        "db": path,
        "counts": {t: count_rows(path, t) for t in TABLES},
    }

def main():
    parser = argparse.ArgumentParser(description="MockTest.pro GitHub backup bridge")
    parser.add_argument("action", choices=["backup", "restore", "status"])
    parser.add_argument("--db", help="path to the SQLite database (default: auto)")
    parser.add_argument("--force", action="store_true", help="backup even if the DB looks empty")
    args = parser.parse_args()

    path = args.db or resolve_db_path()
    try:
        if args.action == "backup":
            result = do_backup(path, force=args.force)
            if result.get("skipped"):
                print("skipped:", result["reason"])
            else:
                print("pushed:", json.dumps(result["pushed"]))
        elif args.action == "restore":
            print("restored:", json.dumps(do_restore(path)))
        else:
            print(json.dumps(status(path), indent=2))
    except Exception as e:
        print("error:", e, file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
