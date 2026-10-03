from __future__ import annotations
import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
APP = PROJECT / "patent-agent"

def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")

def encode(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

def digest(value):
    if not isinstance(value, bytes):
        value = encode(value).encode("utf-8")
    return hashlib.sha256(value).hexdigest()

class Store:
    def __init__(self, root=None):
        self.root = Path(root or PROJECT / "data").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "workflow.sqlite3"
        with self.connect() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS trace (seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                at TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db, timeout=30)
        db.execute("PRAGMA journal_mode=WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def run_dir(self, rid):
        if not re.fullmatch(r"R-[A-Za-z0-9-]+", rid):
            raise ValueError("任务编号格式错误")
        return self.root / "runs" / rid

    def list(self):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT payload FROM runs ORDER BY rowid DESC")]

    def load(self, rid):
        self.run_dir(rid)
        with self.connect() as db:
            row = db.execute("SELECT revision,payload FROM runs WHERE id=?", (rid,)).fetchone()
        if row is None:
            raise ValueError(f"任务不存在：{rid}")
        run = json.loads(row[1])
        run["_revision"] = row[0]
        return run

    def save(self, run, action, detail=None, new=False):
        rid = run["run_id"]
        revision = run.get("_revision", 0)
        run["updated_at"] = now()
        run["_revision"] = revision + 1
        with self.connect() as db:
            if new:
                db.execute("INSERT INTO runs VALUES(?,?,?)", (rid, revision + 1, encode(run)))
            else:
                changed = db.execute("UPDATE runs SET revision=?,payload=? WHERE id=? AND revision=?",
                                     (revision + 1, encode(run), rid, revision)).rowcount
                if changed != 1:
                    raise ValueError("任务已被另一个操作更新，请刷新后重试")
            db.execute("INSERT INTO trace(run_id,at,action,detail) VALUES(?,?,?,?)",
                       (rid, now(), action, encode(detail or {})))
        return run

    def traces(self, rid):
        with self.connect() as db:
            return [{"seq": r[0], "at": r[1], "action": r[2], "detail": json.loads(r[3])}
                    for r in db.execute("SELECT seq,at,action,detail FROM trace WHERE run_id=? ORDER BY seq", (rid,))]

    def check_artifact(self, run, relative, content, raw=False):
        directory = self.run_dir(run["run_id"])
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ValueError("输出文件必须位于本任务目录")
        blob = content if isinstance(content, bytes) else content.encode("utf-8")
        hashes = run.setdefault("artifact_hashes", {})
        if path.exists():
            old_hash = digest(path.read_bytes())
            if old_hash == digest(blob):
                return path
            if raw or old_hash != hashes.get(relative):
                raise ValueError(f"文件已由用户修改，已停止覆盖：{path}")
        return path

    def artifact(self, run, relative, content, raw=False):
        path = self.check_artifact(run, relative, content, raw)
        blob = content if isinstance(content, bytes) else content.encode("utf-8")
        hashes = run.setdefault("artifact_hashes", {})
        if path.exists() and digest(path.read_bytes()) == digest(blob):
            hashes[relative] = digest(blob)
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".writing")
        with temp.open("wb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
        hashes[relative] = digest(blob)
        return path
