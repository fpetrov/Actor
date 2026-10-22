import asyncio
from dataclasses import replace

import pytest

from actor_broadcast.budget import BudgetLedger
from actor_broadcast.config import CommentaryConfig, IdleConfig
from actor_broadcast.contracts import ProviderTerminal
from test_broadcast_commentary import Clock


async def test_one_ledger_idle_is_subset_and_critical_reserve_is_finite():
    clock=Clock();ledger=BudgetLedger(clock=clock.wall)
    for _ in range(2):assert (await ledger.reserve("match","idle_recap",3,100,100)).reservation
    assert (await ledger.reserve("match","memory_callback",3,100,100)).reservation is None
    for _ in range(3):assert (await ledger.reserve("match","reactive",0,100,100)).reservation
    assert (await ledger.reserve("match","reactive",0,100,100)).reservation is None
    report=await ledger.report()
    assert report["calls"]==5 and report["idle_calls"]==2
    await ledger.close()


async def test_concurrent_reservation_cannot_race_past_hour_or_match_limits(tmp_path):
    config=CommentaryConfig(soft_calls_per_hour=2,hard_calls_per_hour=2,hard_calls_per_match=2)
    ledger=BudgetLedger(tmp_path/"quota.sqlite",config=config)
    decisions=await asyncio.gather(*(ledger.reserve("match","reactive",0,100,100) for _ in range(20)))
    assert sum(decision.reservation is not None for decision in decisions)==2
    assert (await ledger.report())["calls"]==2
    await ledger.close()


async def test_restart_new_match_and_new_provider_do_not_reset_hourly_budget(tmp_path):
    config=CommentaryConfig(soft_calls_per_hour=1,hard_calls_per_hour=1)
    path=tmp_path/"quota.sqlite"
    ledger=BudgetLedger(path,config=config)
    assert (await ledger.reserve("one","outro",0,100,100)).reservation
    await ledger.close()
    reopened=BudgetLedger(path,config=config)
    assert (await reopened.reserve("two","intro",0,100,100)).reason=="hourly_calls"
    assert path.stat().st_mode&0o777==0o600
    await reopened.close()


async def test_unknown_cancelled_retry_and_repair_remain_charged_and_known_usage_reconciles_once():
    ledger=BudgetLedger()
    reservation=(await ledger.reserve("one","repair",0,1500,512,calls=2)).reservation
    await ledger.reconcile(reservation,None)
    assert (await ledger.report())["unknown_usage_calls"]==2
    actual=ProviderTerminal("interrupted",100,20,50,0,provider_calls=1)
    await ledger.reconcile(reservation,actual)
    await ledger.reconcile(reservation,ProviderTerminal("completed",999,999,provider_calls=2))
    report=await ledger.report()
    assert report["calls"]==1 and report["input_tokens_charged"]==100 and report["unknown_usage_calls"]==0
    await ledger.close()


async def test_measured_overshoot_creates_debt_not_unaccounted_free_tokens():
    ledger=BudgetLedger(config=CommentaryConfig(input_tokens_per_hour=1000))
    reservation=(await ledger.reserve("one","reactive",0,100,100)).reservation
    await ledger.reconcile(reservation,ProviderTerminal("completed",1200,20))
    assert (await ledger.reserve("one","reactive",0,1,1)).reason=="input_tokens"
    assert (await ledger.report())["input_tokens_charged"]==1200
    await ledger.close()


async def test_unknown_sdk_overhead_floor_survives_restart_without_repricing_known_usage(tmp_path):
    path=tmp_path/"quota.sqlite"
    config=CommentaryConfig(input_tokens_per_hour=25000)
    ledger=BudgetLedger(path,config=config)
    unknown=(await ledger.reserve("one","repair",0,660,512,calls=2)).reservation
    known=(await ledger.reserve("one","reactive",0,8000,512)).reservation
    await ledger.reconcile(known,ProviderTerminal("completed",7182,53))
    await ledger.raise_unknown_input_floor(12000)
    await ledger.reconcile(unknown,ProviderTerminal("failed",0,None))
    report=await ledger.report()
    assert report["input_tokens_charged"]==24000+7182 and report["unknown_usage_calls"]==2
    await ledger.close()
    ledger=BudgetLedger(path,config=config)
    await ledger.raise_unknown_input_floor(12000)
    assert (await ledger.report())["input_tokens_charged"]==31182
    await ledger.reconcile(unknown,ProviderTerminal("interrupted",1,None,provider_calls=1))
    assert (await ledger.report())["input_tokens_charged"]==31182
    assert (await ledger.reserve("two","intro",0,1,1)).reason=="input_tokens"
    # Later real accounting can reconcile this same original reservation once.
    await ledger.reconcile(unknown,ProviderTerminal("interrupted",9000,20,provider_calls=1))
    assert (await ledger.report())["input_tokens_charged"]==16182
    await ledger.close()


async def test_concurrent_known_settlement_changes_persistent_match_count_once(tmp_path):
    path=tmp_path/"quota.sqlite"
    config=CommentaryConfig(hard_calls_per_match=2)
    first=BudgetLedger(path,config=config)
    reservation=(await first.reserve("match","repair",0,660,512,calls=2)).reservation
    connections=[first,*[BudgetLedger(path,config=config) for _ in range(15)]]
    await asyncio.gather(*(item.reconcile(reservation,ProviderTerminal("completed",8000,50,provider_calls=1)) for item in connections))
    report=await first.report()
    assert report["calls"]==1 and report["input_tokens_charged"]==8000
    assert (await first.reserve("match","reactive",0,100,100)).reservation is not None
    assert (await first.reserve("match","reactive",0,100,100)).reason=="match_calls"
    await asyncio.gather(*(item.close() for item in connections))


async def test_unknown_floor_observes_clock_high_water(tmp_path):
    clock=Clock();ledger=BudgetLedger(tmp_path/"quota.sqlite",clock=clock.wall)
    assert (await ledger.reserve("match","intro",0,100,100)).reservation
    clock.advance(4000);await ledger.raise_unknown_input_floor(12000)
    clock.advance(-3999)
    assert (await ledger.reserve("other","intro",0,100,100)).reason=="clock_rollback"
    with pytest.raises(ValueError,match="clock"):
        await ledger.raise_unknown_input_floor(12000)
    await ledger.close()


async def test_partial_usage_is_never_a_zero_or_refunded_unknown_charge():
    ledger=BudgetLedger()
    reservation=(await ledger.reserve("one","reactive",0,100,100)).reservation
    await ledger.reconcile(reservation,ProviderTerminal("failed",0,None))
    report=await ledger.report()
    assert report["input_tokens_charged"]==100 and report["output_tokens_charged"]==100 and report["unknown_usage_calls"]==1
    await ledger.close()


async def test_clock_rollback_does_not_expire_charges_and_forward_hour_does():
    clock=Clock();ledger=BudgetLedger(clock=clock.wall)
    assert (await ledger.reserve("one","reactive",0,100,100)).reservation
    clock.advance(-1)
    assert (await ledger.reserve("one","reactive",0,100,100)).reason=="clock_rollback"
    clock.advance(3602)
    assert (await ledger.report())["calls"]==0
    assert (await ledger.reserve("two","reactive",0,100,100)).reservation
    await ledger.close()


async def test_match_call_limit_survives_hour_window_and_disabled_optional_modes():
    clock=Clock();config=CommentaryConfig(hard_calls_per_match=1)
    ledger=BudgetLedger(clock=clock.wall,config=config)
    assert (await ledger.reserve("one","intro",0,100,100)).reservation
    clock.advance(3601)
    assert (await ledger.reserve("one","outro",0,100,100)).reason=="match_calls"
    assert (await ledger.reserve("two","outro",0,100,100)).reservation
    await ledger.close()


async def test_optional_analysis_explicit_opt_in_one_per_meeting_three_per_match():
    clock=Clock();ledger=BudgetLedger(clock=clock.wall)
    assert (await ledger.reserve("one","analysis",0,100,100,analysis_key="meeting1")).reason=="analysis_disabled"
    ledger.config=CommentaryConfig(analysis_enabled=True)
    for index in range(3):
        assert (await ledger.reserve("one","analysis",0,100,100,analysis_key=str(index))).reservation
        assert (await ledger.reserve("one","analysis",0,100,100,analysis_key=str(index))).reservation is None
        clock.advance(61)
    assert (await ledger.reserve("one","analysis",0,100,100,analysis_key="fourth")).reason=="analysis_match"
    await ledger.close()


async def test_actual_analysis_overshoot_reconciles_its_nested_quota_too():
    clock=Clock();ledger=BudgetLedger(clock=clock.wall,config=CommentaryConfig(analysis_enabled=True))
    first=(await ledger.reserve("one","analysis",0,100,100,analysis_key="first")).reservation
    await ledger.reconcile(first,ProviderTerminal("completed",100,100,provider_calls=2))
    clock.advance(61)
    assert (await ledger.reserve("one","analysis",0,100,100,analysis_key="second")).reservation
    clock.advance(61)
    assert (await ledger.reserve("one","analysis",0,100,100,analysis_key="third")).reason=="analysis_match"
    await ledger.close()


async def test_a_foreign_database_is_not_adopted(tmp_path):
    import sqlite3
    path=tmp_path/"foreign.sqlite"
    with sqlite3.connect(path) as connection:connection.execute("CREATE TABLE private(value)")
    ledger=BudgetLedger(path)
    with pytest.raises(ValueError,match="Not a commentary"):
        await ledger.reserve("one","reactive",0,100,100)
    await ledger.close()
