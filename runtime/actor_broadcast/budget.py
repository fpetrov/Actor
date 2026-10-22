"""One durable local ledger for every commentator provider call.

Reservations count before execution. Unknown usage retains its reservation;
known overshoot creates debt and blocks further admissions. This is a local
quota, not a representation of the account's global subscription allowance.
"""
import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from .config import CommentaryConfig
from .contracts import ProviderTerminal, integer, bounded_text

IDLE_MODES = frozenset({"idle_recap", "memory_callback"})


@dataclass(frozen=True)
class Reservation:
    id: str
    match_id: str
    mode: str
    input_tokens: int
    output_tokens: int
    calls: int
    analysis_key: str | None = None


@dataclass(frozen=True)
class BudgetDecision:
    reservation: Reservation | None
    reason: str


class BudgetLedger:
    def __init__(self, path=None, *, quota_scope_id="local-commentary", config=None, clock=time.time):
        if not isinstance(quota_scope_id, str) or not 1 <= len(quota_scope_id) <= 128:
            raise ValueError("A bounded opaque local quota scope is required")
        self.path = Path(path) if path is not None else None
        self.scope, self.config, self.clock = quota_scope_id, config or CommentaryConfig(), clock
        self._connection = None
        self._lock = asyncio.Lock()
        self.closed = False

    def _open(self):
        if self._connection is not None: return self._connection
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.path.is_symlink(): raise ValueError("Budget ledger cannot be a symlink")
        connection = sqlite3.connect(str(self.path) if self.path else ":memory:", timeout=2, check_same_thread=False)
        application = connection.execute("PRAGMA application_id").fetchone()[0]
        tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if application not in (0, 0x41434231) or application == 0 and tables:
            connection.close(); raise ValueError("Not a commentary budget ledger")
        connection.execute("PRAGMA application_id=1094926897")  # ACB1
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS charges(
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, match TEXT NOT NULL, mode TEXT NOT NULL,
                priority INTEGER NOT NULL, at REAL NOT NULL, calls INTEGER NOT NULL,
                input INTEGER NOT NULL, output INTEGER NOT NULL, known INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS by_scope_time ON charges(scope,at);
            CREATE TABLE IF NOT EXISTS matches(scope TEXT, match TEXT, calls INTEGER, idle INTEGER,
                PRIMARY KEY(scope,match));
            CREATE TABLE IF NOT EXISTS clocks(scope TEXT PRIMARY KEY, latest REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS analyses(scope TEXT, match TEXT, meeting TEXT, calls INTEGER,
                PRIMARY KEY(scope,match,meeting));
        """)
        connection.commit()
        if self.path: os.chmod(self.path, 0o600)
        self._connection = connection
        return connection

    async def _call(self, function, *arguments):
        async with self._lock:
            if self.closed: raise RuntimeError("Budget ledger is closed")
            # Await the actual worker even if the caller is cancelled: a
            # reservation must never be abandoned while still committing.
            task = asyncio.create_task(asyncio.to_thread(function, *arguments))
            cancelled=False
            while not task.done():
                try:await asyncio.shield(task)
                except asyncio.CancelledError:cancelled=True
            result=task.result()
            if cancelled:raise asyncio.CancelledError
            return result

    async def reserve(self, match_id, mode, priority, input_tokens, output_tokens, *, calls=1, analysis_key=None):
        bounded_text(match_id,128)
        if mode not in ("reactive","idle_recap","memory_callback","chat_response","intro","outro","analysis","repair"):
            raise ValueError("Unknown commentary mode")
        if analysis_key is not None:bounded_text(analysis_key,128)
        integer(input_tokens, 1, 1000000); integer(output_tokens, 1, 100000)
        integer(calls, 1, 2); integer(priority, 0, 3)
        return await self._call(self._reserve, match_id, mode, priority, input_tokens, output_tokens, calls,analysis_key)

    def _reserve(self, match_id, mode, priority, input_tokens, output_tokens, calls,analysis_key):
        connection = self._open(); now = self.clock(); config = self.config
        connection.execute("BEGIN IMMEDIATE")
        try:
            previous = connection.execute("SELECT latest FROM clocks WHERE scope=?", (self.scope,)).fetchone()
            if previous and now < previous[0]:
                connection.rollback(); return BudgetDecision(None, "clock_rollback")
            connection.execute("INSERT INTO clocks VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET latest=excluded.latest", (self.scope, now))
            connection.execute("DELETE FROM charges WHERE at<=?", (now - 3600,))
            rows = connection.execute("SELECT mode,priority,at,calls,input,output FROM charges WHERE scope=?", (self.scope,)).fetchall()
            match = connection.execute("SELECT calls,idle FROM matches WHERE scope=? AND match=?", (self.scope, match_id)).fetchone() or (0,0)
            minute = [row for row in rows if row[2] > now - 60]
            recent = [row for row in rows if row[2] > now - 60 * config.normal_burst_capacity / config.normal_calls_per_minute]
            idle = mode in IDLE_MODES
            reasons = []
            if mode=="analysis":
                if not config.analysis_enabled or analysis_key is None:reasons.append("analysis_disabled")
                total=connection.execute("SELECT COALESCE(SUM(calls),0) FROM analyses WHERE scope=? AND match=?",(self.scope,match_id)).fetchone()[0]
                meeting=connection.execute("SELECT calls FROM analyses WHERE scope=? AND match=? AND meeting=?",(self.scope,match_id,analysis_key)).fetchone()
                if total+calls>config.analysis_max_calls_per_match:reasons.append("analysis_match")
                if (meeting[0] if meeting else 0)+calls>config.analysis_max_calls_per_meeting:reasons.append("analysis_meeting")
            if sum(row[3] for row in rows) + calls > config.hard_calls_per_hour: reasons.append("hourly_calls")
            if match[0] + calls > config.hard_calls_per_match: reasons.append("match_calls")
            if sum(row[3] for row in minute) + calls > config.absolute_calls_per_rolling_minute: reasons.append("minute_calls")
            if sum(row[4] for row in rows) + input_tokens > config.input_tokens_per_hour: reasons.append("input_tokens")
            if sum(row[5] for row in rows) + output_tokens > config.output_tokens_per_hour: reasons.append("output_tokens")
            # A sliding burst window enforces the finite normal bucket without
            # mutable restart-sensitive token-bucket state.
            normal = [row for row in minute if row[1] != 0]
            if priority != 0:
                if sum(row[3] for row in normal) + calls > config.normal_calls_per_minute: reasons.append("normal_rate")
                if sum(row[3] for row in recent if row[1] != 0) + calls > config.normal_burst_capacity: reasons.append("normal_burst")
            if sum(row[3] for row in minute) + calls > config.normal_calls_per_minute + config.critical_extra_calls_per_minute:
                reasons.append("critical_reserve")
            if (idle or mode=="chat_response") and sum(row[3] for row in rows)>=config.soft_calls_per_hour:
                reasons.append("soft_pressure")
            if idle:
                if sum(row[3] for row in rows) >= config.soft_calls_per_hour: reasons.append("soft_pressure")
                if sum(row[3] for row in minute if row[0] in IDLE_MODES) + calls > config.idle.max_calls_per_rolling_minute: reasons.append("idle_minute")
                if sum(row[3] for row in rows if row[0] in IDLE_MODES) + calls > config.idle.max_calls_per_hour: reasons.append("idle_hour")
                if match[1] + calls > config.idle.max_calls_per_match: reasons.append("idle_match")
            if reasons:
                connection.commit(); return BudgetDecision(None, reasons[0])
            reservation = Reservation(uuid4().hex, match_id, mode, input_tokens, output_tokens, calls,analysis_key)
            connection.execute("INSERT INTO charges VALUES(?,?,?,?,?,?,?,?,?,0)",
                (reservation.id,self.scope,match_id,mode,priority,now,calls,input_tokens,output_tokens))
            connection.execute("INSERT INTO matches VALUES(?,?,?,?) ON CONFLICT(scope,match) DO UPDATE SET calls=calls+excluded.calls,idle=idle+excluded.idle",
                (self.scope,match_id,calls,calls if idle else 0))
            if mode=="analysis":
                connection.execute("INSERT INTO analyses VALUES(?,?,?,?) ON CONFLICT(scope,match,meeting) DO UPDATE SET calls=calls+excluded.calls",
                    (self.scope,match_id,analysis_key,calls))
            connection.commit(); return BudgetDecision(reservation, "admitted")
        except BaseException:
            connection.rollback(); raise

    async def reconcile(self, reservation, terminal: ProviderTerminal | None):
        await self._call(self._reconcile, reservation, terminal)

    async def raise_unknown_input_floor(self, minimum_per_call):
        """Increase unresolved reservations when provider overhead is learned."""
        integer(minimum_per_call,1,1000000)
        def raise_floor():
            connection=self._open();now=self.clock()
            connection.execute("BEGIN IMMEDIATE")
            try:
                previous=connection.execute("SELECT latest FROM clocks WHERE scope=?",(self.scope,)).fetchone()
                if previous and now<previous[0]:raise ValueError("Budget clock moved backwards")
                connection.execute("INSERT INTO clocks VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET latest=excluded.latest",(self.scope,now))
                connection.execute("UPDATE charges SET input=MAX(input,calls*?) WHERE scope=? AND known=0 AND at>?",
                    (minimum_per_call,self.scope,now-3600))
                connection.commit()
            except BaseException:
                connection.rollback();raise
        await self._call(raise_floor)

    def _reconcile(self, reservation, terminal):
        if terminal is None: return  # Unknown is the original charged reservation.
        connection = self._open()
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute("SELECT calls,known,input,output FROM charges WHERE id=? AND scope=?", (reservation.id,self.scope)).fetchone()
            if row is None or row[1]:
                connection.commit();return
            known = terminal.input_tokens is not None and terminal.output_tokens is not None
            # Unknown settlement cannot erase a newer concurrent reservation.
            charged_input = terminal.input_tokens if known else max(row[2],reservation.input_tokens, terminal.input_tokens or 0)
            charged_output = terminal.output_tokens if known else max(row[3],reservation.output_tokens, terminal.output_tokens or 0)
            count = terminal.provider_calls if known else max(row[0],reservation.calls,terminal.provider_calls)
            connection.execute("UPDATE charges SET calls=?,input=?,output=?,known=? WHERE id=? AND scope=?",
                (count,charged_input,charged_output,int(known),reservation.id,self.scope))
            delta = count-row[0]
            connection.execute("UPDATE matches SET calls=calls+?,idle=idle+? WHERE scope=? AND match=?",
                (delta,delta if reservation.mode in IDLE_MODES else 0,self.scope,reservation.match_id))
            if reservation.mode=="analysis":
                connection.execute("UPDATE analyses SET calls=calls+? WHERE scope=? AND match=? AND meeting=?",
                    (delta,self.scope,reservation.match_id,reservation.analysis_key))
            connection.commit()
        except BaseException:
            connection.rollback();raise

    async def report(self):
        def read():
            rows=self._open().execute("SELECT mode,calls,input,output,known FROM charges WHERE scope=? AND at>?",(self.scope,self.clock()-3600)).fetchall()
            return {"calls":sum(row[1] for row in rows),"input_tokens_charged":sum(row[2] for row in rows),
                "output_tokens_charged":sum(row[3] for row in rows),"unknown_usage_calls":sum(row[1] for row in rows if not row[4]),
                "idle_calls":sum(row[1] for row in rows if row[0] in IDLE_MODES),"persistent":self.path is not None,
                "scope":"local_commentary_only"}
        return await self._call(read)

    async def close(self):
        async with self._lock:
            if self._connection is not None:
                await asyncio.to_thread(self._connection.close)
                self._connection=None
            self.closed=True
