"""One pinned watch revision per id. sqlite3 transactions include observations and alert rows."""
from contextlib import contextmanager
from dataclasses import asdict
import json
import sqlite3
from core import Rule, Seen, decide, digest, rule_hash
from proof import verify

DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS watch(
 id TEXT PRIMARY KEY,rule TEXT NOT NULL,binding TEXT NOT NULL,bundle TEXT NOT NULL,
 proof TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS reading(
 watch_id TEXT NOT NULL REFERENCES watch(id), attempt TEXT NOT NULL,
 t INTEGER NOT NULL, body TEXT NOT NULL, receipt TEXT NOT NULL, reason TEXT NOT NULL,
 PRIMARY KEY(watch_id,attempt), UNIQUE(watch_id,t)
);
CREATE TABLE IF NOT EXISTS alert(
 watch_id TEXT NOT NULL, attempt TEXT NOT NULL, previous_attempt TEXT NOT NULL,
 PRIMARY KEY(watch_id,attempt), FOREIGN KEY(watch_id,attempt) REFERENCES reading(watch_id,attempt),
 FOREIGN KEY(watch_id,previous_attempt) REFERENCES reading(watch_id,attempt)
);
"""


class Ledger:
    def __init__(self, path):
        self.db = sqlite3.connect(path, isolation_level=None, timeout=5)
        self.db.executescript(DDL)

    @contextmanager
    def write(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def activate(self, watch_id, draft, answers, rule, binding, capture_hash, approved_hash):
        # Input must come from a server-owned draft revision; route owns session access and human approval.
        if (binding["seller"],binding["variant"]) != (rule.seller,rule.variant):
            raise ValueError("binding changed")
        proof = verify(draft, answers, rule)
        bundle = approval_digest(binding, capture_hash, proof)
        if bundle != approved_hash:
            raise ValueError("approval is stale")
        with self.write():
            row = self.db.execute("SELECT bundle,active FROM watch WHERE id=?", (watch_id,)).fetchone()
            if row:
                if row != (bundle,1):
                    raise ValueError("watch id belongs to another or paused revision")
                return bundle
            self.db.execute("INSERT INTO watch(id,rule,binding,bundle,proof) VALUES(?,?,?,?,?)",
                            (watch_id,json.dumps(asdict(rule)),json.dumps(binding),bundle,json.dumps(proof)))
        return bundle

    def ingest(self, watch_id, attempt, seen, receipt):
        body = json.dumps(asdict(seen),sort_keys=True)
        evidence = json.dumps(receipt,sort_keys=True)
        with self.write():
            record = self.db.execute("SELECT rule,active FROM watch WHERE id=?", (watch_id,)).fetchone()
            if not record or not record[1]:
                raise ValueError("inactive revision")
            rule = Rule(**json.loads(record[0]))
            duplicate = self.db.execute("SELECT body,receipt FROM reading WHERE watch_id=? AND attempt=?",
                                        (watch_id,attempt)).fetchone()
            if duplicate:
                if duplicate != (body,evidence):
                    raise ValueError("conflicting duplicate")
                return "duplicate"
            last = self.db.execute("SELECT t,body,attempt FROM reading WHERE watch_id=? ORDER BY t DESC LIMIT 1",
                                   (watch_id,)).fetchone()
            if last and last[0] >= seen.t:
                return "out_of_order"
            previous = Seen(**json.loads(last[1])) if last else None
            verdict = decide(rule, previous, seen)
            self.db.execute("INSERT INTO reading VALUES(?,?,?,?,?,?)", (watch_id,attempt,seen.t,body,evidence,verdict.reason))
            if verdict.alert:
                self.db.execute("INSERT INTO alert VALUES(?,?,?)", (watch_id,attempt,last[2]))
            return verdict.reason

    def pause(self, watch_id):
        with self.write():
            self.db.execute("UPDATE watch SET active=0 WHERE id=?", (watch_id,))

    def close(self):
        self.db.close()


def approval_digest(binding, capture_hash, proof):
    return digest({"format":1,"binding":binding,"capture":capture_hash,
                   "rule":proof["rule_hash"],"proof":proof["proof_hash"]})
