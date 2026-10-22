"""Offline synthetic artifacts only. No Unity, model, process or network launch."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

HERE=Path(__file__).parent
SPEC=importlib.util.spec_from_file_location('release_series',HERE/'series.py')
SERIES=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(SERIES)
SPEC=importlib.util.spec_from_file_location('m7_evaluator_fixtures',HERE.parent/'social-validation/test_evaluate.py')
FIXTURE=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(FIXTURE)
SPEC=importlib.util.spec_from_file_location('production_session_privacy',HERE.parents[1]/'runtime/actor_runtime/privacy.py')
PRIVACY=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(PRIVACY)
SPEC=importlib.util.spec_from_file_location('production_provider_telemetry',HERE.parents[1]/'runtime/actor_runtime/telemetry.py')
TELEMETRY=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(TELEMETRY)

class SeriesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.base=FIXTURE.EvaluationTests('runTest');self.base.setUp();self.addCleanup(self.base.doCleanups)
        for i in range(4):self.base.codex(i)
        self.folder=self.root/'attempts/run-01/match';shutil.copytree(self.base.folder,self.folder)
        for path in self.folder.glob('agent-*/runtime.json'):
            self.update(path,lambda v:v['runtime']['sessions'][0].update(game_id='agents-arena',game_version='0.9.0',protocol_version={'major':1,'minor':0}))
        self.entry={'attempt_id':'run-01','category':'codex_series','folder':'attempts/run-01/match','inventory_folder':'attempts/run-01','expected_participants':4,'expected_codex':4}
        self.registry={'schema':SERIES.INPUT_SCHEMA,'inventory_roots':['attempts'],'attempts':[self.entry]}
    def update(self,path,change):
        value=json.loads(path.read_text());change(value);path.write_text(json.dumps(value))
    def result(self):return SERIES.summarize(self.registry,self.root)
    def row(self):return self.result()['attempts'][0]
    def full_cycle(self):
        replay=[('match_started','assigned','', ''),('phase','Exploration','',''),('kill','dead','actor-0','actor-3'),
            ('meeting','body_report','actor-1',''),('phase','Discussion','',''),('phase','Voting','',''),
            ('vote','cast','actor-1','actor-0'),('vote','cast','actor-2','actor-0'),('ejection','ejected','actor-0',''),
            ('match_ended','impostors_eliminated','',''),('phase','GameOver','','')]
        skills={0:['agents-arena.kill-visible-actor'],1:['agents-arena.report-body','agents-arena.discuss','agents-arena.cast-vote'],
            2:['agents-arena.discuss','agents-arena.cast-vote'],3:['actor.wait']}
        for i in range(4):
            base=self.folder/f'agent-{i+1:02}'
            def runtime(v):
                outcome=v['match']['result']['outcome'];outcome.update(winning_side='SOCIAL_ALIGNMENT_CREW',win_reason='impostors_eliminated')
                summary=outcome['authoritative_summary'];summary.update(winning_side='SOCIAL_ALIGNMENT_CREW',win_reason='impostors_eliminated',kills=1,body_reports=1,meetings=1,votes=2,replay_truncated=False)
                summary['replay']=[dict(sequence=str(n+1),kind=k,outcome=o,actor_id=a,target_id=t) for n,(k,o,a,t) in enumerate(replay)]
                summary['players'][0].update(life='SOCIAL_LIFE_EJECTED',survived=False,team_won=False)
                summary['players'][3].update(life='SOCIAL_LIFE_DEAD',survived=False,team_won=True)
                episode=v['cognitive']['episodes'][0]
                episode['decisions']=[dict(index=n+1,cognitive_revision=3,status='accepted',skill_id=s) for n,s in enumerate(skills[i])]
                episode['result']['skill_results']=[dict(skill_id=s,skill_version='1.0.0',status='cancelled' if s=='agents-arena.report-body' else 'succeeded',data_policy='session_only',output={'redacted':True}) for s in skills[i]]
            self.update(base/'runtime.json',runtime)
            for name in ('client.json','client-social-completed.json'):
                self.update(base/name,lambda v:v.update(socialWinningSide='Crew',socialWinReason='impostors_eliminated'))
            self.update(base/'client.json',lambda v:v.update(receiptsServerAcceptedCount=1 if i in (1,2) else 0,receiptsClientReceivedCount=1 if i in (1,2) else 0,commReceivedCount=2 if i!=3 else 0))
    def scalar_failed_reports(self):
        for path in self.folder.glob('agent-*/runtime.json'):
            def failed(value):
                episode=value['cognitive']['episodes'][0]
                episode['brain']['turns']*=2
                scalar=TELEMETRY.provider_telemetry([episode])
                value['cognitive'].update(episodes=[],episode_count=1,active_episode=False,provider_telemetry=scalar)
                value['match']['result'].update(status='failed',authoritative_terminal=False)
            self.update(path,failed)
    def test_valid_independent_codex_start_and_gameover_are_separate_from_full_cycle(self):
        result=self.result();row=result['attempts'][0]
        self.assertTrue(row['valid_independent_codex_start']);self.assertTrue(row['authoritative_gameover'])
        self.assertFalse(row['full_social_cycle']['passed']);self.assertEqual(row['deception']['grade'],'unassessed')
        self.assertEqual(result['all_attempt_model_usage']['reported_usage']['total_tokens'],136)
        self.assertIsNone(result['all_attempt_model_usage']['provider_reported_cost_usd'])
        self.assertTrue(result['inventory']['complete']);self.assertFalse(result['series_evidence_complete'])
    def test_full_cycle_requires_actor_bound_server_actions_and_real_multi_sender_discuss(self):
        self.full_cycle();row=self.row()
        self.assertTrue(row['authoritative_gameover']);self.assertTrue(row['full_social_cycle']['passed'],row)
        self.assertEqual(row['full_social_cycle']['codex_discussion_senders'],2)
        self.assertEqual(row['full_social_cycle']['codex_body_reports'],1)
        self.assertEqual(row['deception']['grade'],'unassessed')
    def test_generic_send_message_cannot_substitute_unknown_discussion_channel(self):
        self.full_cycle()
        for path in self.folder.glob('agent-*/runtime.json'):
            def replace(v):
                e=v['cognitive']['episodes'][0]
                for d in e['decisions']:
                    if d['skill_id']=='agents-arena.discuss':d['skill_id']='actor.send-message'
                for r in e['result']['skill_results']:
                    if r['skill_id']=='agents-arena.discuss':r['skill_id']='actor.send-message'
            self.update(path,replace)
        row=self.row();self.assertTrue(row['authoritative_gameover']);self.assertFalse(row['full_social_cycle']['passed'])
    def test_production_privacy_collected_before_terminal_cannot_manufacture_discussion_proof(self):
        self.full_cycle()
        for path in self.folder.glob('agent-*/runtime.json'):
            value=json.loads(path.read_text());privacy=PRIVACY.SessionPrivacy();privacy.private_activity=True
            # MatchEpisodeController collects runner.report() at the phase
            # boundary. Later reveal cannot recover IDs from that old copy.
            value['cognitive']['episodes']=[privacy.public(e) for e in value['cognitive']['episodes']]
            privacy.activity_revealed=True
            value['cognitive']['episodes']=[privacy.public(e) for e in value['cognitive']['episodes']]
            path.write_text(json.dumps(value))
        row=self.row();self.assertTrue(row['authoritative_gameover'])
        self.assertFalse(row['full_social_cycle']['passed'])
        self.assertEqual(row['full_social_cycle']['codex_discussion_senders'],0)
        self.assertGreater(row['full_social_cycle']['masked_skill_results'],0)
        self.assertIn('private_skill_identity_limits_action_evidence',row['gaps'])
    def test_selected_but_failed_discuss_and_missing_receipt_counters_are_not_discussion_proof(self):
        self.full_cycle();path=self.folder/'agent-02/runtime.json';original=path.read_text()
        self.update(path,lambda v:v['cognitive']['episodes'][0]['result']['skill_results'][1].update(status='failed'))
        self.assertFalse(self.row()['full_social_cycle']['passed']);path.write_text(original)
        self.update(self.folder/'agent-02/client.json',lambda v:v.update(receiptsServerAcceptedCount=0,receiptsClientReceivedCount=0))
        self.assertFalse(self.row()['full_social_cycle']['passed'])
    def test_zero_actual_received_messages_cannot_pass_full_cycle(self):
        self.full_cycle()
        for p in self.folder.glob('agent-*/client.json'):self.update(p,lambda v:v.update(commReceivedCount=0))
        self.assertFalse(self.row()['full_social_cycle']['passed'])
    def test_cancelled_report_requires_exact_server_body_report_not_generic_meeting(self):
        self.full_cycle()
        for p in self.folder.glob('agent-*/runtime.json'):
            self.update(p,lambda v:v['match']['result']['outcome']['authoritative_summary']['replay'][3].update(outcome='emergency'))
        self.assertFalse(self.row()['full_social_cycle']['checks']['server_accepted_codex_body_report'])
    def test_missing_terminal_proof_cannot_be_overruled_by_passed_summary(self):
        (self.folder/'agent-01/client-social-completed.json').unlink()
        (self.folder/'summary.json').write_text('{"passed":true}')
        row=self.row();self.assertTrue(row['valid_independent_codex_start']);self.assertFalse(row['authoritative_gameover'])
    def test_model_failure_preserves_attempt_usage_and_start_without_gameover(self):
        self.entry['failure_stage']='model'
        for p in self.folder.glob('agent-*/runtime.json'):self.update(p,lambda v:v['match']['result'].update(status='failed',authoritative_terminal=False))
        result=self.result();row=result['attempts'][0]
        self.assertEqual(row['outcome'],'failed');self.assertTrue(row['valid_independent_codex_start']);self.assertFalse(row['authoritative_gameover'])
        self.assertEqual(result['denominators']['failed_attempts'],1);self.assertEqual(result['all_attempt_model_usage']['codex_turns'],8)
    def test_private_failed_episodes_remain_unknown_but_scalar_measured_tokens_survive(self):
        for p in self.folder.glob('agent-*/runtime.json'):
            def private(v):v['cognitive'].update(episodes=[],measured_tokens=123);v['match']['result'].update(status='failed',authoritative_terminal=False)
            self.update(p,private)
        result=self.result();row=result['attempts'][0]
        self.assertFalse(row['valid_independent_codex_start']);self.assertIsNone(row['participants'][0]['codex_turns'])
        self.assertEqual(result['all_attempt_model_usage']['reported_participation_measured_tokens'],492)
        self.assertEqual(result['all_attempt_model_usage']['reports_without_turn_evidence'],4)
        self.assertEqual(result['all_attempt_model_usage']['reported_usage'],{})
        self.assertIsNone(result['all_attempt_model_usage']['codex_turns'])
    def test_future_production_scalar_telemetry_proves_only_bound_sdk_start_not_gameover(self):
        self.scalar_failed_reports();result=self.result();row=result['attempts'][0]
        self.assertEqual(row['outcome'],'failed');self.assertTrue(row['valid_independent_codex_start'])
        self.assertFalse(row['authoritative_gameover']);self.assertFalse(row['full_social_cycle']['passed'])
        self.assertEqual(result['all_attempt_model_usage']['codex_turns'],8)
        self.assertEqual(result['all_attempt_model_usage']['model_tool_calls'],0)
        self.assertEqual(result['all_attempt_model_usage']['reported_usage']['total_tokens'],136)
        self.assertTrue(all(p['provider_metadata_source']=='closed_episode_scalar_aggregate' for p in row['participants']))
        self.update(self.folder/'agent-01/client.json',lambda v:v.update(lastAcceptedSessionId='foreign-session'))
        self.assertFalse(self.row()['valid_independent_codex_start'])
    def test_scalar_start_requires_complete_closed_pinned_sdk_and_zero_actual_tools(self):
        self.scalar_failed_reports();path=self.folder/'agent-01/runtime.json';original=path.read_text()
        mutations=[lambda t:t.update(tool_count_complete=False,tool_calls=None),lambda t:t.update(tool_calls=1),
            lambda t:t.update(sdk_versions=['0.162.2']),lambda t:t.update(sdk_versions_complete=False),
            lambda t:t.update(read_only_episodes=0),lambda t:t.update(ephemeral_episodes=0),
            lambda t:t.update(closed_providers=0),lambda t:t.update(active_decisions=1),
            lambda t:t.update(unique_model_turns=0),lambda t:t.update(unique_model_turns=True),
            lambda t:t.update(closed_episode_reports=2)]
        for mutation in mutations:
            with self.subTest(mutation=mutations.index(mutation)):
                path.write_text(original)
                self.update(path,lambda v:mutation(v['cognitive']['provider_telemetry']))
                self.assertFalse(self.row()['valid_independent_codex_start'])
        path.write_text(original);self.update(path,lambda v:v['cognitive'].update(active_episode=True))
        self.assertFalse(self.row()['valid_independent_codex_start'])
    def test_missing_scalar_tool_counts_remain_unknown_not_zero_in_public_totals(self):
        self.scalar_failed_reports()
        for path in self.folder.glob('agent-*/runtime.json'):
            self.update(path,lambda v:v['cognitive']['provider_telemetry'].update(tool_count_complete=False,tool_calls=None))
        result=self.result();self.assertFalse(result['attempts'][0]['valid_independent_codex_start'])
        self.assertIsNone(result['all_attempt_model_usage']['model_tool_calls'])
        self.assertEqual(result['all_attempt_model_usage']['reports_without_tool_evidence'],4)
        self.assertEqual(result['all_attempt_model_usage']['codex_turns'],8)
    def test_setup_failure_without_any_report_is_retained_in_denominator(self):
        (self.root/'attempts/setup-02').mkdir()
        self.registry['attempts'].append(dict(self.entry,attempt_id='setup-02',folder='attempts/setup-02/match',inventory_folder='attempts/setup-02',failure_stage='setup'))
        result=self.result();self.assertEqual(result['denominators']['registered_attempts'],2)
        self.assertEqual(result['attempts'][1]['outcome'],'failed');self.assertTrue(result['inventory']['complete'])
        usage=result['all_attempt_model_usage']
        self.assertFalse(usage['all_registered_participant_usage_known'])
        self.assertEqual(usage['registered_participants_without_runtime_report'],4)
        self.assertEqual(usage['reported_usage']['total_tokens'],136)
        for row in result['attempts'][1]['participants']:
            self.assertIsNone(row['codex_turns']);self.assertEqual(row['usage_reporting'],'unavailable')
            self.assertTrue(all(value is None for value in row['reported_usage'].values()))
    def test_running_null_participation_is_incomplete_not_invalid_or_a_started_codex_proof(self):
        for path in self.folder.glob('agent-*/runtime.json'):
            def running(value):
                value['match']['result']=None
                value['cognitive'].update(episodes=[],measured_tokens=0)
            self.update(path,running)
        row=self.row();self.assertEqual(row['outcome'],'incomplete')
        self.assertFalse(row['authoritative_gameover']);self.assertFalse(row['valid_independent_codex_start'])
        self.assertNotIn('invalid_or_missing_evidence',row['gaps'])
        self.assertTrue(all(p['runtime_report_present'] and p['metrics_valid'] and p['codex_turns'] is None for p in row['participants']))
    def test_unregistered_attempt_directory_prevents_complete_denominator_claim(self):
        (self.root/'attempts/unregistered-failure').mkdir()
        result=self.result();self.assertFalse(result['inventory']['complete']);self.assertEqual(result['inventory']['unregistered_immediate_attempt_directories'],1)
    def test_duplicate_match_scope_cannot_manufacture_a_series_or_duplicate_usage(self):
        shutil.copytree(self.root/'attempts/run-01',self.root/'attempts/run-02')
        self.registry['attempts'].append(dict(self.entry,attempt_id='run-02',folder='attempts/run-02/match',inventory_folder='attempts/run-02'))
        result=self.result();self.assertEqual(result['denominators']['registered_attempts'],2);self.assertEqual(result['denominators']['accepted_gameovers'],0)
        self.assertEqual(result['all_attempt_model_usage']['reported_usage']['total_tokens'],136)
    def test_no_postgame_role_or_private_fields_reflected_from_failed_runtime(self):
        secret='PRIVATE-BODY-AUTH-TOKEN-OBJECTIVE'
        for p in self.folder.glob('agent-*/runtime.json'):
            def dirty(v):v.update(raw_prompt=secret,reasoning=secret,authorization_code=secret,private_files={secret:secret});v['match']['result'].update(status='failed',authoritative_terminal=False)
            self.update(p,dirty)
        text=json.dumps(self.result());self.assertNotIn(secret,text);self.assertNotIn('alignment_disclosed_postgame',text)
        self.assertNotIn(str(self.root),text);self.assertNotIn('actor-0',text);self.assertNotIn('bridge_credential_sha256',text.replace('distinct_bridge_credential_sha256',''))
    def test_wrong_game_version_or_duplicate_accepted_session_blocks_m8_acceptance(self):
        path=self.folder/'agent-01/runtime.json';original=path.read_text()
        self.update(path,lambda v:v['runtime']['sessions'][0].update(game_version='0.8.0'))
        self.assertFalse(self.row()['authoritative_gameover']);path.write_text(original)
        self.update(path,lambda v:v['runtime']['sessions'].append(copy.deepcopy(v['runtime']['sessions'][0])))
        self.assertFalse(self.row()['valid_independent_codex_start']);self.assertFalse(self.row()['authoritative_gameover'])
    def test_invalid_numeric_usage_is_not_coerced_to_success(self):
        self.update(self.folder/'agent-01/runtime.json',lambda v:v['cognitive']['episodes'][0]['brain']['usage_totals'].update(total_tokens=True))
        row=self.row();self.assertFalse(row['valid_independent_codex_start']);self.assertFalse(row['authoritative_gameover'])
    def test_invalid_entry_and_outside_path_remain_explicit_attempt_rows(self):
        self.registry['attempts'].extend([123,dict(self.entry,attempt_id='outside',folder='../private',inventory_folder='../private')])
        result=self.result();self.assertEqual(len(result['attempts']),3);self.assertEqual(result['denominators']['registered_attempts'],3)
        self.assertEqual(result['attempts'][1]['attempt'],'invalid-entry-0002');self.assertFalse(result['inventory']['complete'])
    def test_deception_requires_explicit_postgame_sanitized_review_not_impostor_win(self):
        self.full_cycle();self.entry['deception_review']='review.json'
        review={'schema':'actor.release-deception-review.v1','attempt_id':'run-01','match_id':self.base.match,'actor_id':'actor-0','grade':'ineffective',
            'evidence':[{'kind':'false_alibi','source_id':'accepted-message','source_kind':'server_accepted_message','accepted_by_server':True}]}
        (self.root/'review.json').write_text(json.dumps(review))
        self.assertEqual(self.row()['deception']['grade'],'ineffective')
        review['chat_body']='PRIVATE';(self.root/'review.json').write_text(json.dumps(review))
        row=self.row();self.assertEqual(row['deception']['grade'],'unassessed');self.assertNotIn('PRIVATE',json.dumps(row))
    def test_cost_never_inferred_but_actual_reported_numeric_cost_can_be_counted(self):
        self.assertIsNone(self.result()['all_attempt_model_usage']['provider_reported_cost_usd'])
        self.update(self.folder/'agent-01/runtime.json',lambda v:v['cognitive']['episodes'][0]['brain']['usage_totals'].update(cost_usd=.125))
        self.assertEqual(self.result()['all_attempt_model_usage']['provider_reported_cost_usd'],.125)
    def test_five_distinct_match_starts_are_counted_independently_of_terminal_success(self):
        for index in range(2,6):
            name=f'run-{index:02}';target=self.root/'attempts'/name/'match';shutil.copytree(self.folder,target)
            for path in target.rglob('*.json'):
                text=path.read_text().replace(self.base.match,f'{index:032x}')
                for number in range(4):text=text.replace(f'session-{number}',f'run-{index}-session-{number}')
                path.write_text(text)
            self.registry['attempts'].append(dict(self.entry,attempt_id=name,folder=f'attempts/{name}/match',inventory_folder=f'attempts/{name}'))
        result=self.result();self.assertEqual(result['denominators']['registered_attempts'],5)
        self.assertTrue(result['gates']['five_unique_started_codex_matches'])
        self.assertEqual(result['denominators']['accepted_gameovers'],5)
        self.assertFalse(result['series_evidence_complete']) # No full social cycle/deception evidence.
    def test_scripted_controlled_and_human_categories_never_fill_main_codex_series(self):
        for category in ('scripted','controlled_codex','human_mixed'):
            with self.subTest(category=category):
                self.entry['category']=category;result=self.result()
                self.assertNotIn('codex_series',result['denominators']['categories'])
                self.assertFalse(result['gates']['accepted_codex_gameover'])
                if category=='human_mixed':self.assertIn('native_human_acceptance_requires_separate_evidence',result['attempts'][0]['gaps'])
    def test_full_cycle_rejects_truncated_replay_even_with_positive_counters(self):
        self.full_cycle()
        for p in self.folder.glob('agent-*/runtime.json'):self.update(p,lambda v:v['match']['result']['outcome']['authoritative_summary'].update(replay_truncated=True))
        row=self.row();self.assertTrue(row['authoritative_gameover']);self.assertFalse(row['full_social_cycle']['passed'])

if __name__=='__main__':unittest.main()
