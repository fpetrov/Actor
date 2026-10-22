#!/usr/bin/env python3
"""Bounded real-provider benchmark on authored public scenarios, without audio.

This deliberately does not manufacture native presentation or playback ACKs.
It uses the production installation budget and channel lease, not a test quota.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import time
from uuid import uuid4

from actor_broadcast.budget import BudgetLedger
from actor_broadcast.config import CommentaryConfig
from actor_broadcast.context import reservation_input_tokens
from actor_broadcast.contracts import CommentaryCandidate, CommentaryRequest, EvidenceRef, ProviderSegment, ProviderTerminal, Scope
from actor_broadcast.native import installation_identity, private_directory
from actor_broadcast.ownership import ChannelLease
from actor_codex.commentary import CodexCommentaryProvider


# Authored examples only: no player observations, roles, memory, chat or credentials.
CASES = (
    ("reactive", "Exploration", "В холле началось исследование. Участники ещё не выбрали маршрут.", "phase_started"),
    ("reactive", "Exploration", "Наблюдателю видны Лена и Олег: оба вошли в инженерную из холла.", "visible_transition"),
    ("reactive", "Exploration", "Публичный счётчик выполненных задач вырос с двух до трёх из десяти.", "task_progress"),
    ("reactive", "Exploration", "Включилась публичная тревога. Причина и виновник наблюдателю неизвестны.", "alarm_started"),
    ("reactive", "Exploration", "Публичная тревога снята. На видимом участке Мира и Ян стоят у консоли.", "alarm_ended"),
    ("reactive", "Discussion", "Нина сообщила об обнаруженном теле в лаборатории. Наблюдатель не видел нападения.", "body_report"),
    ("reactive", "Discussion", "Олег публично заявил: «Я был рядом с Леной». Это утверждение игрока, не проверенный факт.", "public_claim"),
    ("reactive", "Discussion", "Лена публично возразила Олегу: «Я тебя там не видела». Обе версии остаются неподтверждёнными.", "conflicting_claims"),
    ("reactive", "Voting", "Обсуждение завершилось и началось публичное голосование. Результата ещё нет.", "vote_started"),
    ("reactive", "Exploration", "Опубликован результат: голоса разделились поровну, никто не исключён.", "vote_tied"),
    ("idle_recap", "Exploration", "За последние сорок секунд выполнена одна публичная задача и снята тревога. Нападений наблюдатель не видел.", "recent_recap"),
    ("idle_recap", "Exploration", "На текущем видимом участке Ян остался у консоли; Мира ушла из поля зрения. Её нынешнее положение неизвестно.", "visibility_lost"),
    ("memory_callback", "Exploration", "Полторы минуты назад Олег заявил, что был рядом с Леной. Тогда Лена это оспорила; новых подтверждений нет.", "earlier_dispute"),
    ("reactive", "Exploration", "Наблюдатель непосредственно видел нападение: Ян атаковал Нину на освещённом участке. Это свидетельство данного эпизода.", "witnessed_attack"),
    ("reactive", "Discussion", "Объявлен экстренный сбор. В публичной повестке пока нет обвинений.", "emergency_meeting"),
    ("idle_recap", "Exploration", "Публичный прогресс задач составляет восемь из десяти. На видимом участке сейчас нет участников.", "progress_recap"),
    ("memory_callback", "Exploration", "Две минуты назад Лена и Олег вместе вошли в инженерную. Это старое наблюдение, оно не определяет их нынешнее место.", "earlier_transition"),
    ("chat_response", "Exploration", "Зритель написал: «Мне кажется, виноват Олег». Это предположение зрителя, а не игровое доказательство.", "viewer_claim"),
    ("reactive", "Exploration", "Видимая совместная работа у консоли завершилась. Публичный счётчик вырос до девяти из десяти.", "cooperative_progress"),
    ("outro", "GameOver", "Сервер публично завершил матч: победила команда экипажа после выполнения всех задач. Это авторитетный итог.", "terminal_result"),
)


def save(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


def percentile(values, q):
    if not values: return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lo, hi = math.floor(position), math.ceil(position)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


async def run(args):
    config = CommentaryConfig()
    root = private_directory(Path.home() / ".actor" / "broadcast")
    lease = ChannelLease(args.channel).acquire()
    budget = BudgetLedger(root / "quota.sqlite", quota_scope_id="installation:" + installation_identity(root), config=config)
    provider = CodexCommentaryProvider(codex_home=args.codex_home.expanduser().absolute())
    report = {"schema": "actor.broadcast-provider-benchmark.v1", "scenarios": "authored_public_synthetic",
        "real_provider": True, "native_presentation": False, "tts": False, "playback": False,
        "obs_recording": False, "twitch_ingest": False, "model": "gpt-6-luna", "cases_requested": args.limit,
        "shared_production_quota": True, "attempts": [], "completed": False, "cleanup_completed": False}
    started = time.monotonic(); session, match = uuid4().hex, uuid4().hex
    deadline = started + args.total_timeout
    try:
        report["startup"] = await asyncio.wait_for(provider.prepare(), 30)
        floor=getattr(provider,"minimum_input_reservation",None)
        if floor is not None: await budget.raise_unknown_input_floor(floor)
        for index, (mode, phase, text, predicate) in enumerate(CASES[:args.limit], 1):
            now = int(time.time() * 1000); ref = EvidenceRef(f"benchmark-public-{index}", "1")
            rows = [(ref, text, "player_claim" if predicate in {"public_claim", "earlier_dispute"} else "historical_observation")]
            if predicate == "conflicting_claims":
                rows = [(ref, "Олег публично заявил: «Я был рядом с Леной». Это неподтверждённое утверждение Олега.", "player_claim"),
                    (EvidenceRef(ref.id + "-response", "1"), "Лена публично ответила Олегу: «Я тебя там не видела». Это неподтверждённое утверждение Лены.", "player_claim")]
            candidate = CommentaryCandidate(Scope(session, match, 1, 1), index, uuid4().hex, mode,
                2 if mode == "reactive" else 3, predicate, tuple(row[0] for row in rows), now + 3600000)
            external = {"mode": mode, "phase": phase, "current_view": {}, "actually_spoken": [],
                "historical_callback": mode == "memory_callback", "public_notes": [], "topic_key": predicate,
                "evidence": [{"ref": asdict(identity), "kind": "viewer_claim" if mode == "chat_response" else kind,
                    "origin": "viewer_chat" if mode == "chat_response" else "game", "source_event_id": identity.id,
                    "observed_at_unix_ms": now - (120000 if mode == "memory_callback" else 0),
                    "expires_at_unix_ms": now + 3600000, "value": {"text": statement}, "subjects": [], "predicate": predicate}
                    for identity, statement, kind in rows]}
            request = CommentaryRequest(candidate, external, 256 if mode in ("idle_recap", "memory_callback") else 512, uuid4().hex, 1)
            while True:
                if time.monotonic() >= deadline: raise TimeoutError("benchmark_total_timeout")
                decision = await budget.reserve(match, mode, candidate.priority, reservation_input_tokens(provider,request),
                    request.max_output_tokens, calls=provider.max_provider_calls)
                if decision.reservation is not None: break
                if decision.reason not in {"minute_calls", "normal_rate", "normal_burst", "critical_reserve", "idle_minute"}:
                    raise RuntimeError("budget_" + decision.reason)
                if time.monotonic() + 5 > deadline: raise TimeoutError("benchmark_total_timeout")
                await asyncio.sleep(5)
            attempt = {"case": index, "mode": mode, "first_segment_seconds": None, "segments": [], "terminal": None}
            report["attempts"].append(attempt)
            begin = time.monotonic(); terminal = None
            try:
                if begin >= deadline: raise TimeoutError("benchmark_total_timeout")
                async with asyncio.timeout(min(config.provider_timeout_seconds, deadline - begin)):
                    async for event in provider.generate(request):
                        if isinstance(event, ProviderSegment):
                            if attempt["first_segment_seconds"] is None: attempt["first_segment_seconds"] = time.monotonic() - begin
                            attempt["segments"].append(asdict(event))
                        elif isinstance(event, ProviderTerminal): terminal = event
            finally:
                terminal = terminal or provider.last_terminal
                await budget.reconcile(decision.reservation, terminal)
                attempt["duration_seconds"] = time.monotonic() - begin
                attempt["terminal"] = asdict(terminal) if terminal else None
                report["provider"] = provider.report(); report["budget"] = await budget.report()
                save(args.output, report)
            print(json.dumps({"case": index, "status": terminal.status if terminal else "unknown",
                "first_segment_seconds": attempt["first_segment_seconds"], "segments": len(attempt["segments"])}), flush=True)
            if terminal is None or terminal.status != "completed" or not attempt["segments"]:
                raise RuntimeError("provider_case_incomplete")
        report["completed"] = True
    except Exception as error:
        # Diagnostic payloads may contain SDK internals. Keep only bounded local codes.
        known = str(error)
        allowed = {"provider_case_incomplete", "benchmark_total_timeout", "budget_clock_rollback", "budget_hourly_calls",
            "budget_match_calls", "budget_input_tokens", "budget_output_tokens", "budget_soft_pressure", "budget_idle_hour", "budget_idle_match"}
        report["error_code"] = known if known in allowed else "benchmark_failed"
    finally:
        cleanup_errors = []
        for _ in range(2):
            try: await asyncio.wait_for(provider.close(), 8)
            except Exception: cleanup_errors.append("provider_cleanup_failed")
            if provider.report().get("closed") is True: break
        report["provider"] = provider.report()
        try: report["budget"] = await budget.report()
        except Exception: cleanup_errors.append("budget_report_failed")
        budget_closed = False
        try:
            await budget.close(); budget_closed = True
        except Exception: cleanup_errors.append("budget_cleanup_failed")
        lease_released = False
        try:
            lease.release(); lease_released = True
        except Exception: cleanup_errors.append("lease_cleanup_failed")
        report["cleanup_completed"] = provider.report().get("closed") is True and budget_closed and lease_released
        report["cleanup_errors"] = cleanup_errors
        report["elapsed_seconds"] = time.monotonic() - started
        values = [item["first_segment_seconds"] for item in report["attempts"] if item["first_segment_seconds"] is not None]
        report["first_segment_p50_seconds"] = percentile(values, .5)
        report["first_segment_p95_seconds"] = percentile(values, .95)
        save(args.output, report)
    return 0 if report["completed"] and report["cleanup_completed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-real", action="store_true", help="Spend shared commentary quota using existing official ChatGPT sign-in")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--channel", choices=("fedorr_codex",), default="fedorr_codex")
    parser.add_argument("--limit", type=int, choices=range(1, 21), default=20)
    parser.add_argument("--codex-home", type=Path, default=Path.home()/".actor/broadcast/codex-home",
        help="Dedicated officially authenticated commentator profile; never resets the shared budget")
    parser.add_argument("--total-timeout", type=float, default=1800)
    args = parser.parse_args()
    if not args.execute_real: parser.error("--execute-real is required; no provider call was started")
    if not 60 <= args.total_timeout <= 1800: parser.error("total timeout must be 60–1800 seconds")
    args.output = args.output.expanduser().absolute()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
