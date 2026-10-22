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
from types import SimpleNamespace
from uuid import uuid4

from actor_broadcast.budget import BudgetLedger
from actor_broadcast.config import CommentaryConfig
from actor_broadcast.context import reservation_input_tokens, speech_presentation
from actor_broadcast.contracts import CommentaryCandidate, CommentaryRequest, EvidenceRef, ProviderSegment, ProviderTerminal, Scope
from actor_broadcast.editorial import editorial_frame
from actor_broadcast.native import installation_identity, private_directory
from actor_broadcast.ownership import ChannelLease
from actor_codex.commentary import CodexCommentaryProvider


# Authored examples only: no player observations, roles, memory, chat or credentials.
CASES = (
    ("intro", "Exploration", "В холле началось исследование. Участники ещё не выбрали маршрут.", "phase_started"),
    ("reactive", "Exploration", "Наблюдателю видны Лена и Олег: оба вошли в инженерную из холла.", "visible_transition"),
    ("reactive", "Exploration", "Публичный счётчик выполненных задач вырос с двух до трёх из десяти.", "task_progress"),
    ("reactive", "Exploration", "Включилась публичная тревога. Причина и виновник наблюдателю неизвестны.", "alarm_started"),
    ("reactive", "Exploration", "Публичная тревога снята. На видимом участке Мира и Ян стоят у консоли.", "alarm_ended"),
    ("reactive", "Discussion", "Нина сообщила об обнаруженном теле в холле. Наблюдатель не видел нападения.", "body_report"),
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

CRITICAL_PREDICATES = frozenset({"alarm_started", "body_report", "vote_started", "vote_tied",
    "witnessed_attack", "emergency_meeting", "terminal_result"})

EDITORIAL_CASES = ("focused_movement_history", "stopped_near_console", "memory_link",
    "attributed_meeting_arguments", "voting_unknown_ballots", "confirmed_public_tally",
    "visibility_loss_uncertainty", "terminal_result")


def editorial_request(index, case, *, now, config, session, match):
    """Richer authored input through real framing helpers, never a fake ACK."""
    if type(index) is not int or not 1 <= index <= len(EDITORIAL_CASES) or case != EDITORIAL_CASES[index-1]:
        raise ValueError("Unknown authored editorial case")
    names = {"lena": "Лена", "oleg": "Олег", "nina": "Нина"}
    current = {"camera": {"anchor_id": "", "focus_actor_id": "lena"},
        "actors": [{"actor_id": "lena", "display_name": "Лена"}, {"actor_id": "oleg", "display_name": "Олег"}],
        "visible_actor_ids": ["lena", "oleg"], "crew_tasks_completed": 3, "task_threshold": 8,
        "meeting": {"meeting_id": "", "participants": [], "result": [], "resolution": "", "ejected_actor_id": ""}}
    mode, phase, priority = "reactive", "Exploration", 2
    rows = []
    def add(predicate, text, *, subjects=(), age=0, kind="historical_observation", room="", value=0):
        ref = EvidenceRef(f"benchmark-editorial-{index}-{len(rows)+1}", "1")
        rows.append({"ref": asdict(ref), "kind": kind, "origin": "game", "source_event_id": ref.id,
            "observed_at_unix_ms": now-age*1000, "expires_at_unix_ms": now+300000,
            "age_at_presented_view_seconds": age, "subjects": list(subjects), "predicate": predicate,
            "value": {"text": text, "actor_id": subjects[0] if subjects else "", "room_id": room,
                "label": names.get(subjects[0], "") if subjects else "", "value": value}})
    if case == "focused_movement_history":
        add("actor-seen", "Две минуты назад Лена была видна в инженерной; это не её текущее место.", subjects=("lena",), age=120, room="engineering")
        add("movement-seen", "На свежем кадре Лена движется на восток по холлу; Олег виден у входа.", subjects=("lena",), room="main-hall", value=3)
        add("actor-seen", "Сейчас Олег виден у входа в холл; намерение Лены неизвестно.", subjects=("oleg",), room="main-hall")
    elif case == "stopped_near_console":
        add("movement-seen", "Восемь секунд назад Лена двигалась к видимой консоли в инженерной.", subjects=("lena",), age=8, room="engineering", value=5)
        add("movement-seen", "Теперь Лена остановилась рядом с консолью; содержимое её личной задачи не видно.", subjects=("lena",), room="engineering", value=9)
        add("public-state", "Публичный прогресс остаётся три из восьми; остановка сама по себе не подтверждает выполнение задачи.", value=3)
    elif case == "memory_link":
        mode, priority = "memory_callback", 3
        add("actor-seen", "Полторы минуты назад Лена и Олег были одновременно видны в инженерной.", subjects=("lena", "oleg"), age=90, room="engineering")
        add("movement-seen", "На текущем кадре Лена снова идёт к видимому входу инженерной, а Олег остаётся в холле.", subjects=("lena",), room="main-hall", value=5)
        add("actor-seen", "Олег сейчас виден в холле; прошлое совместное появление не доказывает общий план.", subjects=("oleg",), room="main-hall")
    elif case in ("attributed_meeting_arguments", "voting_unknown_ballots", "confirmed_public_tally"):
        phase = {"attributed_meeting_arguments": "Discussion", "voting_unknown_ballots": "Voting", "confirmed_public_tally": "VoteResolution"}[case]
        if phase == "Discussion": mode = "analysis"
        current["camera"] = {"anchor_id": "main-hall", "focus_actor_id": ""}
        current["meeting"].update(meeting_id="authored-meeting-one", participants=[
            {"actor_id": identity, "display_name": name, "life": "Alive"} for identity, name in names.items()])
        add("discussion-claim", "Лена: «Я пришла из инженерной, Олег был у входа в холл».", subjects=("lena",), age=12, kind="player_claim")
        add("discussion-claim", "Олег отвечает Лене: «У входа я был раньше, потом ушёл к складу».", subjects=("oleg",), age=8, kind="player_claim")
        add("discussion-claim", "Нина: «Я видела Олега у входа, но не видела, куда он ушёл».", subjects=("nina",), age=5, kind="player_claim")
        if phase == "Discussion":
            add("discussion-claim", "Лена уточняет Нине: «Тогда мы могли видеть его в разное время; нужен порядок событий».", subjects=("lena",), kind="player_claim")
        elif phase == "Voting":
            add("public-state", "Голосование открыто; персональные бюллетени и итог ещё неизвестны.")
        else:
            current["meeting"].update(result=[{"candidate_actor_id": "oleg", "votes": 2},
                {"candidate_actor_id": "", "votes": 1}], resolution="ejected", ejected_actor_id="oleg", participants=[])
            current["actors"] = [{"actor_id": "lena", "display_name": "Лена"}]
            current["visible_actor_ids"] = ["lena"]
            add("vote-result", "Опубликован итог: два голоса за Олега, один пропуск; Олег исключён. Кто как голосовал, не раскрыто.", subjects=("oleg",), value=2)
    elif case == "visibility_loss_uncertainty":
        mode, priority = "idle_recap", 3
        current["camera"] = {"anchor_id": "security", "focus_actor_id": ""}
        current["actors"] = [{"actor_id": "oleg", "display_name": "Олег"}]
        current["visible_actor_ids"] = ["oleg"]
        add("actor-seen", "Сорок секунд назад Лена была видна у входа в охрану; теперь её в кадре нет.", subjects=("lena",), age=40, room="security")
        add("actor-seen", "Сейчас Олег виден в охране. Текущее место Лены и её дальнейший маршрут неизвестны.", subjects=("oleg",), room="security")
        add("public-state", "Публичный прогресс задач не изменился: три из восьми.", value=3)
    else:
        mode, phase, priority = "outro", "GameOver", 0
        current["crew_tasks_completed"] = 8
        current["terminal"] = {"winning_side": "Crew", "win_reason": "tasks_completed"}
        add("public-state", "Сервер завершил матч: экипаж победил выполнением восьми задач из восьми. Это подтверждённый итог.", value=8)
    refs = tuple(EvidenceRef(**row["ref"]) for row in rows)
    candidate = CommentaryCandidate(Scope(session, match, 1, 1), index, uuid4().hex, mode,
        priority, case, refs, now+300000)
    framing_view = SimpleNamespace(current=current, phase=phase)
    external = {"mode": mode, "phase": phase, "current_view": current, "actually_spoken": [],
        "presented_view_observed_at_unix_ms": now, "evidence": rows, "public_notes": [],
        "historical_callback": mode == "memory_callback", "topic_key": case,
        "speech_presentation": speech_presentation(candidate, config),
        "editorial_frame": editorial_frame(framing_view, rows)}
    if phase in ("Discussion", "Voting", "VoteResolution") and priority > 1:
        external["speech_presentation"].update(purpose="meeting_arguments", words_target=[40,65], clauses_preferred=[3,4])
    return CommentaryRequest(candidate, external, 768 if mode == "analysis" else
        config.idle.max_output_tokens if mode in ("idle_recap", "memory_callback") else 512,
        uuid4().hex, 1, "medium" if mode == "analysis" else "default")


def authored_request(index, case, *, now, config, session, match):
    """Synthetic public input with real editorial sizing, never a native ACK."""
    mode, phase, text, predicate = case
    ref = EvidenceRef(f"benchmark-public-{index}", "1")
    rows = [(ref, text, "player_claim" if predicate in {"public_claim", "earlier_dispute"} else "historical_observation")]
    if predicate == "conflicting_claims":
        rows = [(ref, "Олег публично заявил: «Я был рядом с Леной». Это неподтверждённое утверждение Олега.", "player_claim"),
            (EvidenceRef(ref.id + "-response", "1"), "Лена публично ответила Олегу: «Я тебя там не видела». Это неподтверждённое утверждение Лены.", "player_claim")]
    priority = 0 if predicate in CRITICAL_PREDICATES else 2 if mode == "reactive" else 3
    candidate = CommentaryCandidate(Scope(session, match, 1, 1), index, uuid4().hex, mode,
        priority, predicate, tuple(row[0] for row in rows), now + 3600000)
    age_ms = 90000 if predicate == "earlier_dispute" else 120000 if mode == "memory_callback" else 0
    external = {"mode": mode, "phase": phase, "current_view": {}, "actually_spoken": [],
        "presented_view_observed_at_unix_ms": now, "speech_presentation": speech_presentation(candidate, config),
        "historical_callback": mode == "memory_callback", "public_notes": [], "topic_key": predicate,
        "evidence": [{"ref": asdict(identity), "kind": "viewer_claim" if mode == "chat_response" else kind,
            "origin": "viewer_chat" if mode == "chat_response" else "game", "source_event_id": identity.id,
            "observed_at_unix_ms": now - age_ms, "age_at_presented_view_seconds": age_ms / 1000,
            "expires_at_unix_ms": now + 3600000, "value": {"text": statement}, "subjects": [], "predicate": predicate}
            for identity, statement, kind in rows]}
    return CommentaryRequest(candidate, external,
        config.idle.max_output_tokens if mode in ("idle_recap", "memory_callback") else 512, uuid4().hex, 1)


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
    suite = getattr(args, "suite", "standard")
    if suite not in ("standard", "editorial"): raise ValueError("Unknown authored suite")
    cases = EDITORIAL_CASES if suite == "editorial" else CASES
    start_case=getattr(args,"start_case",1)
    if type(start_case)is not int or not 1<=start_case<=len(cases):raise ValueError("Invalid authored start case")
    limit = args.limit if args.limit is not None else len(cases)-start_case+1
    if type(limit) is not int or not 1 <= limit <= len(cases)-start_case+1: raise ValueError("Invalid authored case limit")
    make_request = editorial_request if suite == "editorial" else authored_request
    config = CommentaryConfig()
    root = private_directory(Path.home() / ".actor" / "broadcast")
    lease = ChannelLease(args.channel).acquire()
    budget = BudgetLedger(root / "quota.sqlite", quota_scope_id="installation:" + installation_identity(root), config=config)
    provider = CodexCommentaryProvider(codex_home=args.codex_home.expanduser().absolute())
    report = {"schema": "actor.broadcast-provider-benchmark.v1", "scenarios": "authored_public_synthetic",
        "authored_fixture_version": 3 if suite == "editorial" else 2, "suite": suite,
        "controller_courtesy_audio": False,
        "real_provider": True, "native_presentation": False, "tts": False, "playback": False,
        "obs_recording": False, "twitch_ingest": False, "model": "gpt-6-luna", "cases_requested": limit,
        "max_provider_turns": limit,
        "start_case":start_case,
        "shared_production_quota": True, "attempts": [], "completed": False, "cleanup_completed": False}
    started = time.monotonic(); session, match = uuid4().hex, uuid4().hex
    deadline = started + args.total_timeout
    try:
        if type(provider.max_provider_calls) is not int or provider.max_provider_calls != 1:
            raise RuntimeError("benchmark_provider_call_cap")
        report["startup"] = await asyncio.wait_for(provider.prepare(), 30)
        floor=getattr(provider,"minimum_input_reservation",None)
        if floor is not None: await budget.raise_unknown_input_floor(floor)
        for index, case in enumerate(cases[start_case-1:start_case-1+limit], start_case):
            request = make_request(index, case, now=int(time.time() * 1000), config=config, session=session, match=match)
            candidate = request.candidate; mode = candidate.mode
            analysis_key = request.external_state["current_view"]["meeting"]["meeting_id"] if mode == "analysis" else None
            while True:
                if time.monotonic() >= deadline: raise TimeoutError("benchmark_total_timeout")
                decision = await budget.reserve(match, mode, candidate.priority, reservation_input_tokens(provider,request),
                    request.max_output_tokens, calls=provider.max_provider_calls, analysis_key=analysis_key)
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
            "budget_match_calls", "budget_input_tokens", "budget_output_tokens", "budget_soft_pressure", "budget_idle_hour", "budget_idle_match",
            "budget_analysis_disabled", "budget_analysis_match", "budget_analysis_meeting", "benchmark_provider_call_cap"}
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
    parser.add_argument("--suite", choices=("standard", "editorial"), default="standard",
        help="Existing 20 authored scenarios or 8 richer focused-player/meeting cases (no native/audio proof)")
    parser.add_argument("--limit", type=int, choices=range(1, 21), default=None)
    parser.add_argument("--start-case",type=int,default=1,help="First authored case for a bounded targeted regression")
    parser.add_argument("--codex-home", type=Path, default=Path.home()/".actor/broadcast/codex-home",
        help="Dedicated officially authenticated commentator profile; never resets the shared budget")
    parser.add_argument("--total-timeout", type=float, default=1800)
    args = parser.parse_args()
    if not args.execute_real: parser.error("--execute-real is required; no provider call was started")
    maximum = len(EDITORIAL_CASES) if args.suite == "editorial" else len(CASES)
    if not 1<=args.start_case<=maximum:parser.error("start case exceeds the selected authored suite")
    if args.limit is None: args.limit = maximum-args.start_case+1
    if args.limit > maximum-args.start_case+1: parser.error("limit exceeds the selected authored suite")
    if not 60 <= args.total_timeout <= 1800: parser.error("total timeout must be 60–1800 seconds")
    args.output = args.output.expanduser().absolute()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
