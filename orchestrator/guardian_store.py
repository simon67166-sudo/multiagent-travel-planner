"""Team authority and immutable itinerary versions. SQLite is the commit authority.

Browser credentials and invitations are stored only as SHA-256 digests. Transactions
use BEGIN IMMEDIATE, so independent server processes cannot accept stale proposals.
Chat snapshots remain private to each browser; the official itinerary lives here.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import secrets
import sqlite3
import trip_plan

class Conflict(ValueError): pass
class Forbidden(ValueError): pass

def stamp(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
def digest(value): return sha256(value.encode()).hexdigest()
def encode(value): return json.dumps(value,ensure_ascii=False,allow_nan=False)
def identifier(): return secrets.token_hex(16)

def canonical_itinerary(state):
    result={k:deepcopy(state[k]) for k in ("trip_plan","nearby_plan","city","legacy_nearby_plan","migration_notes","mode") if k in state}
    result["city"]=state.get("city","澳門")
    result["trip_plan"]=deepcopy(state.get("trip_plan") or trip_plan.new_trip_plan("trip-"+identifier()))
    result["nearby_plan"]=deepcopy(state.get("nearby_plan"))
    nearby=result["nearby_plan"]
    if nearby and nearby.get("crs") not in (None,"GCJ02"):
        result["legacy_nearby_plan"]=nearby
        result["migration_notes"]=["舊座標與路線已保留；請重新查詢高德路線，不能直接當成 GCJ02 使用。"]
        result["nearby_plan"]=None
    elif nearby:
        # nearby is a map projection of the same accepted version, not another store.
        result["city"]=nearby.get("request",{}).get("city",result["city"])
        date=nearby.get("request",{}).get("date","day-1")
        day={"date":date,"head_id":None,"nodes":{}}
        previous=None
        for i,stop in enumerate(nearby.get("stops",[])):
            ident=stop.get("id") or "stop-"+str(i)
            node_id=str(ident)+"-"+str(i)
            trip_plan.add_stop(day,node_id,"attraction",stop.get("name",stop.get("place","待確認地點")),
                nearby.get("request",{}).get("mode","walking"),stop.get("arrival_time","待確認"),
                stop.get("end_time","待確認"),after_id=previous,poi=deepcopy(stop))
            previous=node_id
        result["trip_plan"].setdefault("days", {})[date]=day
    return result

class GuardianStore:
    def __init__(self,path): self.path=Path(path)
    def connect(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        db=sqlite3.connect(self.path,timeout=30,isolation_level=None)
        db.row_factory=sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS guardian_teams (
          id TEXT PRIMARY KEY,name TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 0,
          revision INTEGER NOT NULL DEFAULT 0,itinerary TEXT NOT NULL,invite_hash TEXT,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS guardian_members (
          id TEXT PRIMARY KEY,team_id TEXT NOT NULL REFERENCES guardian_teams(id),credential_hash TEXT NOT NULL,
          name TEXT NOT NULL,role TEXT NOT NULL,preferences TEXT NOT NULL DEFAULT '{}',active INTEGER NOT NULL DEFAULT 1,
          UNIQUE(team_id,credential_hash));
        CREATE TABLE IF NOT EXISTS guardian_sessions (credential_hash TEXT PRIMARY KEY,member_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS guardian_versions (
          team_id TEXT NOT NULL,version INTEGER NOT NULL,itinerary TEXT NOT NULL,created_at TEXT NOT NULL,
          PRIMARY KEY(team_id,version));
        CREATE TABLE IF NOT EXISTS guardian_proposals (
          id TEXT PRIMARY KEY,team_id TEXT NOT NULL,member_id TEXT NOT NULL,base_version INTEGER NOT NULL,
          base_revision INTEGER NOT NULL,itinerary TEXT NOT NULL,reason TEXT NOT NULL,sources TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',created_at TEXT NOT NULL,event_key TEXT,
          UNIQUE(team_id,event_key));
        CREATE TABLE IF NOT EXISTS guardian_events (
          team_id TEXT NOT NULL,event_key TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL,
          PRIMARY KEY(team_id,event_key));
        CREATE TABLE IF NOT EXISTS guardian_checks (
          team_id TEXT NOT NULL,cache_key TEXT NOT NULL,checked_at REAL NOT NULL,payload TEXT NOT NULL,
          PRIMARY KEY(team_id,cache_key));
        """)
        return db
    @contextmanager
    def transaction(self):
        db=self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally: db.close()
    def _member(self,db,credential):
        row=db.execute("SELECT m.* FROM guardian_sessions s JOIN guardian_members m ON m.id=s.member_id WHERE s.credential_hash=? AND m.active=1",(digest(credential),)).fetchone()
        if not row: raise Forbidden("成員憑證已失效，請重新取得加入連結。")
        return row
    def _owner(self,db,credential):
        member=self._member(db,credential)
        if member["role"]!="owner": raise Forbidden("只有團隊發起人可以進行此操作。")
        return member
    def exists(self,credential):
        with self.transaction() as db:
            return db.execute("SELECT 1 FROM guardian_sessions WHERE credential_hash=?",(digest(credential),)).fetchone() is not None
    def ensure(self,credential,legacy):
        if self.exists(credential): return self.snapshot(credential)
        return self.create(credential,"我的旅行團隊","發起人",legacy,only_if_missing=True)
    def create(self,credential,name,member_name,legacy,only_if_missing=False):
        if not isinstance(name,str) or not 1<=len(name.strip())<=120: raise ValueError("團隊名稱長度須為 1–120 字")
        if not isinstance(member_name,str) or not 1<=len(member_name.strip())<=120: raise ValueError("成員名稱長度須為 1–120 字")
        with self.transaction() as db:
            present=db.execute("SELECT 1 FROM guardian_sessions WHERE credential_hash=?",(digest(credential),)).fetchone()
            if not only_if_missing or not present:
                team,member=identifier(),identifier()
                itinerary=encode(canonical_itinerary(legacy))
                db.execute("INSERT INTO guardian_teams(id,name,itinerary,created_at) VALUES(?,?,?,?)",(team,name.strip(),itinerary,stamp()))
                db.execute("INSERT INTO guardian_members(id,team_id,credential_hash,name,role) VALUES(?,?,?,?,?)",(member,team,digest(credential),member_name.strip(),"owner"))
                db.execute("INSERT OR REPLACE INTO guardian_sessions VALUES(?,?)",(digest(credential),member))
                db.execute("INSERT INTO guardian_versions VALUES(?,?,?,?)",(team,0,itinerary,stamp()))
        return self.snapshot(credential)
    def _snapshot(self,db,credential):
        me=self._member(db,credential)
        team=db.execute("SELECT * FROM guardian_teams WHERE id=?",(me["team_id"],)).fetchone()
        members=[{"id":r["id"],"name":r["name"],"role":r["role"],"preferences":json.loads(r["preferences"])} for r in db.execute("SELECT * FROM guardian_members WHERE team_id=? AND active=1 ORDER BY rowid",(team["id"],))]
        proposals=[self._proposal(r) for r in db.execute("SELECT * FROM guardian_proposals WHERE team_id=? ORDER BY rowid DESC LIMIT 100",(team["id"],))]
        events=[json.loads(r[0]) for r in db.execute("SELECT payload FROM guardian_events WHERE team_id=? ORDER BY rowid DESC LIMIT 100",(team["id"],))]
        return {"team_id":team["id"],"name":team["name"],"version":team["version"],"revision":team["revision"],"member_id":me["id"],"role":me["role"],"members":members,"itinerary":json.loads(team["itinerary"]),"proposals":proposals,"events":events,"invite_active":bool(team["invite_hash"])}
    def snapshot(self,credential):
        with self.transaction() as db: return self._snapshot(db,credential)
    def invite(self,credential,revoke=False):
        with self.transaction() as db:
            member=self._owner(db,credential)
            token=None if revoke else secrets.token_urlsafe(32)
            db.execute("UPDATE guardian_teams SET invite_hash=? WHERE id=?",(digest(token) if token else None,member["team_id"]))
            return token
    def join(self,credential,token,name):
        if not isinstance(token,str) or not 10<=len(token)<=150: raise Forbidden("無效或已撤銷的加入連結")
        if not isinstance(name,str) or not 1<=len(name.strip())<=120: raise ValueError("請填寫 1–120 字的名字")
        with self.transaction() as db:
            team=db.execute("SELECT id FROM guardian_teams WHERE invite_hash=?",(digest(token),)).fetchone()
            if not team: raise Forbidden("無效或已撤銷的加入連結")
            member=db.execute("SELECT id,active FROM guardian_members WHERE team_id=? AND credential_hash=?",(team["id"],digest(credential))).fetchone()
            if member and not member["active"]: raise Forbidden("你已被移出此團隊，請聯絡發起人")
            member_id=member["id"] if member else identifier()
            if not member:
                db.execute("INSERT INTO guardian_members(id,team_id,credential_hash,name,role) VALUES(?,?,?,?,?)",(member_id,team["id"],digest(credential),name.strip(),"member"))
                db.execute("UPDATE guardian_teams SET revision=revision+1 WHERE id=?",(team["id"],))
            db.execute("INSERT OR REPLACE INTO guardian_sessions VALUES(?,?)",(digest(credential),member_id))
        return self.snapshot(credential)
    def preferences(self,credential,preferences,expected_revision):
        if type(expected_revision) is not int: raise ValueError("請提供 expected_revision")
        with self.transaction() as db:
            member=self._member(db,credential)
            changed=db.execute("UPDATE guardian_teams SET revision=revision+1 WHERE id=? AND revision=?",(member["team_id"],expected_revision)).rowcount
            if not changed: raise Conflict("團隊資料已更新，請重新整理後提交偏好。")
            db.execute("UPDATE guardian_members SET preferences=? WHERE id=?",(encode(preferences),member["id"]))
        return self.snapshot(credential)
    def remove(self,credential,member_id):
        with self.transaction() as db:
            owner=self._owner(db,credential)
            if owner["id"]==member_id: raise ValueError("發起人不能移除自己")
            changed=db.execute("UPDATE guardian_members SET active=0 WHERE id=? AND team_id=? AND active=1",(member_id,owner["team_id"])).rowcount
            if not changed: raise ValueError("找不到成員")
            db.execute("UPDATE guardian_teams SET revision=revision+1 WHERE id=?",(owner["team_id"],))
        return self.snapshot(credential)
    @staticmethod
    def _proposal(row):
        return {"id":row["id"],"base_version":row["base_version"],"base_revision":row["base_revision"],"itinerary":json.loads(row["itinerary"]),"reason":row["reason"],"sources":json.loads(row["sources"]),"status":row["status"],"created_at":row["created_at"]}
    def propose(self,credential,itinerary,reason,base_version,base_revision,sources=None,event_key=None):
        itinerary=canonical_itinerary(itinerary)
        with self.transaction() as db:
            member=self._member(db,credential)
            team=db.execute("SELECT * FROM guardian_teams WHERE id=?",(member["team_id"],)).fetchone()
            if team["version"]!=base_version or team["revision"]!=base_revision: raise Conflict("規劃期間團隊資料已變更，請重新產生提案。")
            if event_key:
                old=db.execute("SELECT * FROM guardian_proposals WHERE team_id=? AND event_key=?",(team["id"],event_key)).fetchone()
                if old: return self._proposal(old)
            ident=identifier()
            db.execute("INSERT INTO guardian_proposals(id,team_id,member_id,base_version,base_revision,itinerary,reason,sources,created_at,event_key) VALUES(?,?,?,?,?,?,?,?,?,?)",(ident,team["id"],member["id"],base_version,base_revision,encode(itinerary),reason,encode(sources or []),stamp(),event_key))
            return self._proposal(db.execute("SELECT * FROM guardian_proposals WHERE id=?",(ident,)).fetchone())
    def accept(self,credential,proposal_id,expected_version):
        if type(expected_version) is not int: raise ValueError("請提供 expected_version")
        with self.transaction() as db:
            owner=self._owner(db,credential)
            p=db.execute("SELECT * FROM guardian_proposals WHERE id=? AND team_id=?",(proposal_id,owner["team_id"])).fetchone()
            if not p: raise ValueError("找不到提案")
            if p["status"]!="pending" or p["base_version"]!=expected_version: raise Conflict("提案已處理或版本已過期。")
            changed=db.execute("UPDATE guardian_teams SET itinerary=?,version=version+1,revision=revision+1 WHERE id=? AND version=? AND revision=?",(p["itinerary"],owner["team_id"],expected_version,p["base_revision"])).rowcount
            if not changed: raise Conflict("行程或成員偏好已更新，此提案不能覆蓋新資料。")
            db.execute("UPDATE guardian_proposals SET status='accepted' WHERE id=?",(proposal_id,))
            db.execute("UPDATE guardian_proposals SET status='stale' WHERE team_id=? AND status='pending'",(owner["team_id"],))
            db.execute("INSERT INTO guardian_versions VALUES(?,?,?,?)",(owner["team_id"],expected_version+1,p["itinerary"],stamp()))
        return self.snapshot(credential)
    def reject(self,credential,proposal_id):
        with self.transaction() as db:
            owner=self._owner(db,credential)
            changed=db.execute("UPDATE guardian_proposals SET status='rejected' WHERE id=? AND team_id=? AND status='pending'",(proposal_id,owner["team_id"])).rowcount
            if not changed: raise Conflict("提案不存在或已處理")
        return self.snapshot(credential)
    def record_events(self,credential,events):
        with self.transaction() as db:
            member=self._member(db,credential)
            for event in events:
                key=event.get("id") or digest(encode(event))
                db.execute("INSERT OR IGNORE INTO guardian_events VALUES(?,?,?,?)",(member["team_id"],str(key),encode(event),stamp()))
    def cached_check(self,credential,key,now,ttl=600):
        with self.transaction() as db:
            member=self._member(db,credential)
            row=db.execute("SELECT * FROM guardian_checks WHERE team_id=? AND cache_key=?",(member["team_id"],key)).fetchone()
            if row and now-row["checked_at"]<ttl: return json.loads(row["payload"])
    def save_check(self,credential,key,now,payload):
        with self.transaction() as db:
            member=self._member(db,credential)
            db.execute("INSERT OR REPLACE INTO guardian_checks VALUES(?,?,?,?)",(member["team_id"],key,now,encode(payload)))
