"""Optional worker for one pre-authorized merchant feed delivered as an atomic local JSON export."""
import argparse
import json
from pathlib import Path
import time
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError
from core import Seen
from ledger import Ledger

def source_deadline(db):
    db.execute("CREATE TABLE IF NOT EXISTS source_limit(source TEXT PRIMARY KEY, until_s INTEGER NOT NULL)")
    row = db.execute("SELECT until_s FROM source_limit WHERE source='approved_feed'").fetchone()
    return row[0] if row else 0

def retry_seconds(value, now):
    if value.isdecimal():
        return max(120,int(value))
    try:
        return max(120,int(parsedate_to_datetime(value).timestamp())-now)
    except (TypeError,ValueError,OverflowError):
        return 300

def read_export(path, config, now):
    with Path(path).open("rb") as f:
        data = f.read(1_000_001)
    if len(data) > 1_000_000:
        raise ValueError("feed too large")
    doc = json.loads(data)
    if doc["feed_id"] != config["feed_id"] or config["source"] != "approved_feed":
        raise ValueError("source mismatch")
    matches = [r for r in doc["observations"] if (r["seller"],r["variant"]) == (config["seller"],config["variant"])]
    if len(matches) != 1:
        raise ValueError("missing or conflicting observation")
    seen = Seen(**matches[0])
    if type(seen.t) is not int or not 0 <= now-seen.t <= 120:
        raise ValueError("stale merchant observation")
    return seen, {"feed_id": doc["feed_id"], "observed_at":seen.t}

def sample(ledger, watch_id, config, read):
    now = int(time.time())
    if config.get("source") != "approved_feed" or not config.get("feed_id"):
        raise ValueError("no authorized watch feed bound")
    until = source_deadline(ledger.db)
    if until > now:
        token = f"cooldown-{until}"
        if not ledger.db.execute("SELECT 1 FROM reading WHERE watch_id=? AND attempt=?", (watch_id,token)).fetchone():
            ledger.ingest(watch_id,token,Seen(config["seller"],config["variant"],None,None,"",now,"backoff"),{"until":until})
        return until-now
    try:
        row, receipt = read(config,now)
    except (ValueError,OSError,KeyError,TypeError) as err:
        wait = 60
        if isinstance(err,HTTPError) and err.code == 429:
            wait = retry_seconds(err.headers.get("Retry-After",""),now)
            ledger.db.execute("INSERT INTO source_limit VALUES('approved_feed',?) ON CONFLICT(source) DO UPDATE SET until_s=max(until_s,excluded.until_s)",(now+wait,))
        ledger.ingest(watch_id,f"error-{now}",Seen(config["seller"],config["variant"],None,None,"",now,"source_error"),{"error":type(err).__name__})
        return wait
    ledger.ingest(watch_id,f"source-{row.t}",row,receipt)
    return 60

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db")
    parser.add_argument("--authorized-feed",required=True,type=Path)
    parser.add_argument("--once",action="store_true")
    args=parser.parse_args()
    ledger=Ledger(args.db)
    due={}
    try:
        while True:
            for wid,raw in ledger.db.execute("SELECT id,binding FROM watch WHERE active=1").fetchall():
                if time.monotonic() < due.get(wid,0):
                    continue
                config=json.loads(raw)
                if config.get("source") != "approved_feed":
                    continue
                delay=sample(ledger,wid,config,lambda cfg,t:read_export(args.authorized_feed,cfg,t))
                due[wid]=time.monotonic()+delay
            if args.once:
                return
            time.sleep(1)
    finally:
        ledger.close()

if __name__ == "__main__":
    main()

