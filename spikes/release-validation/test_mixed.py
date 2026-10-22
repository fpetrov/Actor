"""Synthetic frozen artifacts only; no UI, process, network, SDK or model calls."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import unittest
import zlib

HERE=Path(__file__).parent
SPEC=importlib.util.spec_from_file_location("human_mixed_evaluator",HERE/"mixed.py")
MIXED=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(MIXED)
SPEC=importlib.util.spec_from_file_location("human_mixed_fixtures",HERE.parent/"social-validation/test_evaluate.py")
FIXTURE=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(FIXTURE)


def png(color):
    def chunk(kind,payload):
        return struct.pack(">I",len(payload))+kind+payload+struct.pack(">I",zlib.crc32(kind+payload)&0xffffffff)
    data=(b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">IIBBBBB",320,200,8,2,0,0,0))
        +chunk(b"IDAT",zlib.compress((b"\0"+bytes(color)*320)*200))+chunk(b"IEND",b""))
    return data


class MixedTests(unittest.TestCase):
    def setUp(self):
        self.base=FIXTURE.EvaluationTests("runTest");self.base.setUp();self.addCleanup(self.base.doCleanups)
        self.folder=self.base.folder
        for i in range(3):self.base.codex(i)
        shutil.rmtree(self.folder/"agent-04")
        summary=copy.deepcopy(self.base.summary)
        summary["players"][1].update(life="SOCIAL_LIFE_DEAD",survived=False)
        summary["players"][3].update(life="SOCIAL_LIFE_ALIVE",survived=True)
        summary["replay"][1].update(actor_id="actor-0",target_id="actor-1")
        summary["replay"][2].update(actor_id="actor-0",target_id="actor-2")
        for i in range(3):
            def runtime(value):
                value["match"]["result"]["outcome"]["authoritative_summary"]=copy.deepcopy(summary)
                value["runtime"]["sessions"][0].update(game_id="agents-arena",game_version="0.9.0",protocol_version={"major":1})
                value["cognitive"]["episodes"][0]["brain"]["turns"]*=2
            self.change(f"agent-{i+1:02}/runtime.json",runtime)
        for name in ("processes.json","processes-after-cleanup.json"):
            def process(value):
                value["agents"]=value["agents"][:3]
                for row in value["agents"]:row["launcher_exit_code"]=0
                if name=="processes-after-cleanup.json":value["remaining_owned_process_groups"]=0
            self.change(name,process)
        self.write("mixed-supervisor.json",{"schema":MIXED.SUPERVISOR_SCHEMA,"status":"authoritative_match_completed",
            "codex_participants":3,"completed_codex_reports":3,"owned_codex_cleanup":True,
            "process_isolation":{key:True for key in MIXED.M7.ISOLATION}})
        self.write("human-proof.json",{"schema":MIXED.HUMAN_SCHEMA,"actorId":"actor-3","mode":"Human","headless":False,
            "client_pid":9999,"gameMode":"SocialDeduction","socialMatchId":self.base.match,"socialPhase":"GameOver",
            "socialPhaseRevision":8,"socialSummaryPresent":True,"socialWinningSide":"Impostor","socialWinReason":"parity",
            "captured_before_leave":True,"captured_at_unix_ms":2500})
        screenshots=[]
        for name,stage,at,color in (("game.png","native_gameplay",1000,(12,34,56)),("end.png","terminal",2000,(56,34,12))):
            data=png(color);(self.folder/name).write_bytes(data)
            screenshots.append({"file":name,"stage":stage,"captured_at_unix_ms":at,"sha256":hashlib.sha256(data).hexdigest()})
        self.write("native-operator.json",{"schema":MIXED.OPERATOR_SCHEMA,"actor_id":"actor-3","match_id":self.base.match,
            "client_pid":9999,"observed_native_window":True,"human_input_performed":True,"terminal_visible":True,
            "screenshots":screenshots,"actions":[{"action":"move","performed":True}]})

    def read(self,name):return self.base.read(name)
    def write(self,name,value):self.base.write(name,value)
    def change(self,name,callback):self.base.change(name,callback)
    def evaluate(self,**kwargs):return MIXED.evaluate(self.folder,**kwargs)
    def reject(self,**kwargs):self.assertIs(self.evaluate(**kwargs)["passed"],False)

    def test_valid_three_official_models_and_explicit_native_operator_consistency(self):
        result=self.evaluate();self.assertTrue(result["passed"],result["checks"])
        self.assertEqual(result["category"],"human_mixed");self.assertEqual(result["status"],"accepted")
        self.assertEqual([row["codex_turns"] for row in result["model_participants"]],[2,2,2])
        self.assertFalse(result["human_exit_evaluated"])
        text=json.dumps(result)
        for forbidden in ("actor-3","session-0",self.base.match,"9999",str(self.folder),"memory.sqlite3","game.png"):
            self.assertNotIn(forbidden,text)
        self.assertFalse(MIXED.M7.evaluate(self.folder)["passed"])  # Never reinterpret Human as a fourth Runtime.

    def test_frozen_human_must_be_fourth_actor_same_match_phase_and_winner(self):
        original=self.read("human-proof.json")
        for mutation in ({"actorId":"actor-0"},{"actorId":"foreign"},{"socialMatchId":"old-match"},
                {"socialPhase":"Exploration"},{"socialPhaseRevision":7},{"socialWinningSide":"Crew"},
                {"socialSummaryPresent":False},{"mode":"Agent"},{"headless":True},{"captured_before_leave":False},
                {"client_pid":102},{"socialPhaseRevision":True}):
            with self.subTest(mutation=mutation):
                self.write("human-proof.json",dict(original,**mutation));self.reject()

    def test_mode_field_does_not_prove_human_input_or_native_window(self):
        original=self.read("native-operator.json")
        for key in ("human_input_performed","observed_native_window","terminal_visible"):
            with self.subTest(key=key):
                self.write("native-operator.json",dict(original,**{key:False}));self.reject()
        self.write("native-operator.json",dict(original,actions=[]));self.reject()
        self.write("native-operator.json",dict(original,actions=[{"action":"move","performed":False}]));self.reject()
        self.write("native-operator.json",dict(original,actions=[{"action":"invented-native-action","performed":True}]));self.reject()

    def test_native_operator_identity_is_bound_to_frozen_human(self):
        original=self.read("native-operator.json")
        for mutation in ({"actor_id":"actor-0"},{"match_id":"old-match"},{"client_pid":1234}):
            with self.subTest(mutation=mutation):
                self.write("native-operator.json",dict(original,**mutation));self.reject()

    def test_forged_hash_corrupt_png_and_valid_crc_invalid_pixels_rejected(self):
        operator=self.read("native-operator.json")
        value=copy.deepcopy(operator);value["screenshots"][0]["sha256"]="0"*64
        self.write("native-operator.json",value);self.reject()
        self.write("native-operator.json",operator);path=self.folder/"game.png";original=path.read_bytes()
        path.write_bytes(original[:-4]+b"junk");self.reject()
        # Correct CRC and digest do not make non-zlib IDAT a decodable image.
        path.write_bytes(original)
        start=original.index(b"IDAT")-4;size=struct.unpack_from(">I",original,start)[0]
        bad=b"x"*size;replacement=struct.pack(">I",size)+b"IDAT"+bad+struct.pack(">I",zlib.crc32(b"IDAT"+bad)&0xffffffff)
        malformed=original[:start]+replacement+original[start+size+12:];path.write_bytes(malformed)
        operator["screenshots"][0]["sha256"]=hashlib.sha256(malformed).hexdigest();self.write("native-operator.json",operator);self.reject()

    def test_screenshots_must_be_contained_distinct_ordered_and_before_leave_capture(self):
        original=self.read("native-operator.json")
        mutations=[lambda v:v["screenshots"][0].update(file="../foreign.png"),
            lambda v:v["screenshots"][0].update(file=str(self.folder/"game.png")),
            lambda v:v["screenshots"][1].update(file="game.png",sha256=v["screenshots"][0]["sha256"]),
            lambda v:v["screenshots"][0].update(captured_at_unix_ms=2400),
            lambda v:v["screenshots"][1].update(captured_at_unix_ms=3000),
            lambda v:v["screenshots"][1].update(stage="native_gameplay")]
        for i,mutation in enumerate(mutations):
            with self.subTest(mutation=i):
                value=copy.deepcopy(original);mutation(value);self.write("native-operator.json",value);self.reject()

    def test_model_must_be_original_official_codex_and_all_provider_counters_real(self):
        path="agent-01/runtime.json";original=self.read(path)
        mutations=[lambda v:v["cognitive"]["episodes"][0]["brain"].update(provider="social-scripted"),
            lambda v:v["cognitive"]["episodes"][0]["brain"]["adapter"].update(unique_model_turns=0),
            lambda v:v["cognitive"]["episodes"][0]["brain"]["adapter"].update(sdk_version="fake"),
            lambda v:v["cognitive"]["episodes"][0]["brain"]["adapter"].update(closed=False),
            lambda v:v["cognitive"]["episodes"][0]["brain"]["adapter"].update(decision_active=True),
            lambda v:v["cognitive"]["episodes"][0]["brain"]["turns"][0].update(tool_calls=1),
            lambda v:v["cognitive"]["episodes"][0]["brain"]["turns"][0].pop("tool_calls"),
            lambda v:v["cognitive"]["episodes"][0]["brain"]["usage_totals"].update(total_tokens=999),
            lambda v:v["cognitive"]["episodes"][0]["brain"]["usage_totals"].update(cached_input_tokens=31),
            lambda v:v["cognitive"].update(active_episode=True),
            lambda v:v["cognitive"].update(episodes=[])]
        for i,mutation in enumerate(mutations):
            with self.subTest(mutation=i):
                value=copy.deepcopy(original);mutation(value);self.write(path,value);self.reject()

    def test_model_terminal_and_accepted_identity_cannot_be_supervisor_only(self):
        path="agent-01/runtime.json";original=self.read(path)
        mutations=[lambda v:v["match"]["result"].update(authoritative_terminal=False),
            lambda v:v["match"]["result"].update(scope_id="old-match"),
            lambda v:v["runtime"]["sessions"][0].update(actor_id="actor-2"),
            lambda v:v["runtime"]["sessions"][0].update(game_id="other-game")]
        for i,mutation in enumerate(mutations):
            with self.subTest(mutation=i):
                value=copy.deepcopy(original);mutation(value);self.write(path,value);self.reject()
        self.write(path,original);self.change("agent-01/client.json",lambda v:v.update(lastAcceptedSessionId="foreign"));self.reject()

    def test_different_authoritative_summary_or_missing_fourth_player_rejected(self):
        path="agent-02/runtime.json";original=self.read(path)
        self.change(path,lambda v:v["match"]["result"]["outcome"]["authoritative_summary"].update(duration_ms="12346"));self.reject()
        self.write(path,original)
        for i in range(3):self.change(f"agent-{i+1:02}/runtime.json",lambda v:v["match"]["result"]["outcome"]["authoritative_summary"]["players"].pop())
        self.reject()

    def test_cleanup_requires_all_three_groups_and_matching_process_identities(self):
        original=self.read("processes-after-cleanup.json")
        for mutate in (lambda v:v.update(remaining_owned_process_groups=1),
                lambda v:v["agents"][0].update(runtime_alive_after_cleanup=True),
                lambda v:v["agents"][0].update(client_pid=7777),
                lambda v:v["agents"][0].update(launcher_exit_code=None)):
            with self.subTest(mutation=str(mutate)):
                value=copy.deepcopy(original);mutate(value);self.write("processes-after-cleanup.json",value);self.reject()

    def test_explicit_group_total_cannot_be_overridden_by_contradictory_row_booleans(self):
        original=self.read("processes-after-cleanup.json")
        for row in original["agents"]:row["process_group_alive_after_cleanup"]=False
        for total in (1,True,"0",None):
            with self.subTest(total=total):
                self.write("processes-after-cleanup.json",dict(original,remaining_owned_process_groups=total));self.reject()
        value=copy.deepcopy(original);value["agents"][0]["process_group_alive_after_cleanup"]=True
        self.write("processes-after-cleanup.json",value);self.reject()
        original.pop("remaining_owned_process_groups")
        self.write("processes-after-cleanup.json",original)
        self.assertTrue(self.evaluate()["passed"])

    def test_unknown_tool_usage_identity_and_error_prose_never_reach_public_output(self):
        self.change("agent-01/runtime.json",lambda v:v.update(private_context={"code":"secret-value","role":"secret-role"}))
        self.change("agent-01/runtime.json",lambda v:v["cognitive"]["episodes"][0]["brain"].update(raw_error="secret-error"))
        result=self.evaluate();self.assertTrue(result["passed"])
        self.assertFalse(any(value in json.dumps(result) for value in ("secret-value","secret-role","secret-error")))

    def test_claimed_human_report_or_vote_requires_own_server_replay_not_peer_action(self):
        for action in ("report_body","cast_vote"):
            with self.subTest(action=action):
                self.change("native-operator.json",lambda v:v.update(actions=[{"action":action,"performed":True}]))
                self.reject()
        self.change("native-operator.json",lambda v:v.update(actions=[{"action":"move","performed":True}]))
        self.reject(require_human_report=True);self.reject(require_human_vote=True)

    def add_human_report_and_vote(self,actor="actor-3"):
        for i in range(3):
            def update(value):
                summary=value["match"]["result"]["outcome"]["authoritative_summary"]
                replay=summary["replay"]
                replay[2:2]=[{"kind":"meeting","outcome":"body_report","actor_id":actor},
                    {"kind":"phase","outcome":"Discussion"},{"kind":"phase","outcome":"Voting"},
                    {"kind":"vote","outcome":"skip","actor_id":actor}]
                for n,row in enumerate(replay,1):row["sequence"]=str(n)
                summary.update(body_reports=1,meetings=1,votes=1)
            self.change(f"agent-{i+1:02}/runtime.json",update)

    def test_typed_human_report_and_vote_claims_join_exact_fourth_actor_replay(self):
        self.add_human_report_and_vote()
        self.change("native-operator.json",lambda v:v.update(actions=[{"action":"report_body","performed":True},{"action":"cast_vote","performed":True}]))
        result=self.evaluate(require_human_report=True,require_human_vote=True)
        self.assertTrue(result["passed"],result["checks"])
        self.assertEqual(result["human_server_actions"],{"body_reports":1,"votes":1})

    def test_peer_report_and_vote_cannot_substitute_human_actions(self):
        self.add_human_report_and_vote("actor-0")
        self.change("native-operator.json",lambda v:v.update(actions=[{"action":"report_body","performed":True},{"action":"cast_vote","performed":True}]))
        self.reject(require_human_report=True,require_human_vote=True)

    def test_failure_and_missing_native_evidence_preserve_failed_category(self):
        self.change("mixed-supervisor.json",lambda v:v.update(status="interrupted",completed_codex_reports=0))
        (self.folder/"human-proof.json").unlink();(self.folder/"native-operator.json").unlink()
        result=self.evaluate();self.assertFalse(result["passed"]);self.assertEqual(result["category"],"human_mixed")
        self.assertEqual(result["status"],"failed");self.assertEqual(result["recorded_supervisor_status"],"interrupted")
        self.change("mixed-supervisor.json",lambda v:v.update(status="running"))
        self.assertEqual(self.evaluate()["status"],"incomplete")

    def test_invented_terminal_replay_counter_or_truncated_history_rejected(self):
        originals=[self.read(f"agent-{i+1:02}/runtime.json") for i in range(3)]
        mutations=[lambda summary:summary.update(replay_truncated=True),lambda summary:summary.update(kills=3),
            lambda summary:summary["replay"][1].update(sequence="1"),
            lambda summary:summary.update(replay=[row for row in summary["replay"] if row["kind"]!="match_ended"]),
            lambda summary:summary.update(replay=[row for row in summary["replay"] if row["kind"]!="match_started"])]
        for n,mutation in enumerate(mutations):
            with self.subTest(mutation=n):
                for i,original in enumerate(originals):
                    value=copy.deepcopy(original);mutation(value["match"]["result"]["outcome"]["authoritative_summary"])
                    self.write(f"agent-{i+1:02}/runtime.json",value)
                self.reject()

    def test_removing_kills_and_counter_cannot_keep_unsupported_parity_or_dead_players(self):
        for i in range(3):
            def update(value):
                summary=value["match"]["result"]["outcome"]["authoritative_summary"]
                summary.update(kills=0,replay=[row for row in summary["replay"] if row["kind"]!="kill"])
            self.change(f"agent-{i+1:02}/runtime.json",update)
        result=self.evaluate();self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["postgame_lives_supported_by_replay"])
        for i in range(3):
            def alive(value):
                for player in value["match"]["result"]["outcome"]["authoritative_summary"]["players"]:
                    player.update(life="SOCIAL_LIFE_ALIVE",survived=True)
            self.change(f"agent-{i+1:02}/runtime.json",alive)
        result=self.evaluate();self.assertFalse(result["checks"]["terminal_outcome_supported_by_gameplay"])

    def test_parity_can_follow_actual_crew_ejections_without_inventing_two_kills(self):
        for i in range(3):
            def update(value):
                summary=value["match"]["result"]["outcome"]["authoritative_summary"]
                for index in (1,2):summary["players"][index].update(life="SOCIAL_LIFE_EJECTED")
                replay=[{"kind":"match_started","outcome":"assigned"}]
                for target,caller,voters in (("actor-1","actor-3",("actor-0","actor-2","actor-3")),
                        ("actor-2","actor-0",("actor-0","actor-3"))):
                    replay.append({"kind":"meeting","actor_id":caller,"outcome":"emergency_call"})
                    replay.extend({"kind":"vote","actor_id":voter,"target_id":target,"outcome":"cast"} for voter in voters)
                    replay.append({"kind":"ejection","actor_id":target,"outcome":"ejected"})
                replay.extend([{"kind":"match_ended","outcome":"parity"},{"kind":"phase","outcome":"GameOver"}])
                for n,row in enumerate(replay,1):row["sequence"]=str(n)
                summary.update(kills=0,meetings=2,votes=5,replay=replay)
            self.change(f"agent-{i+1:02}/runtime.json",update)
        result=self.evaluate();self.assertTrue(result["passed"],result["checks"])

    def test_ejection_requires_a_real_unique_winning_ballot_not_just_a_named_victim(self):
        for i in range(3):
            def update(value):
                outcome=value["match"]["result"]["outcome"]
                outcome.update(winning_side="SOCIAL_ALIGNMENT_CREW",win_reason="impostors_eliminated")
                summary=outcome["authoritative_summary"]
                for player in summary["players"]:player.update(life="SOCIAL_LIFE_ALIVE")
                summary["players"][0]["life"]="SOCIAL_LIFE_EJECTED"
                replay=[{"kind":"match_started","outcome":"assigned"},
                    {"kind":"ejection","actor_id":"actor-0","outcome":"ejected"},
                    {"kind":"match_ended","outcome":"impostors_eliminated"},{"kind":"phase","outcome":"GameOver"}]
                for n,row in enumerate(replay,1):row["sequence"]=str(n)
                summary.update(winning_side="SOCIAL_ALIGNMENT_CREW",win_reason="impostors_eliminated",kills=0,replay=replay)
            self.change(f"agent-{i+1:02}/runtime.json",update)
            for filename in ("client.json","client-social-completed.json"):
                self.change(f"agent-{i+1:02}/"+filename,lambda value:value.update(socialWinningSide="Crew",socialWinReason="impostors_eliminated"))
        self.change("human-proof.json",lambda value:value.update(socialWinningSide="Crew",socialWinReason="impostors_eliminated"))
        result=self.evaluate();self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["postgame_lives_supported_by_replay"])


if __name__=="__main__":unittest.main()
