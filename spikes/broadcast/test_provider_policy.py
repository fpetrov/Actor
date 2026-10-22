"""Pinned official app-server, local fake Responses API, no account/model calls.

Run only with the explicit [codex] test extra. Base wheel tests deliberately
have no SDK. We inspect real emitted tool specifications, not observed calls.
No request body, prompt, environment, credential, or app-server stderr is logged.
"""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from importlib.resources import files
import json
from pathlib import Path
import threading

import pytest

from actor_codex.launch import codex_launch_args, PINNED_SDK_VERSION


pytestmark = pytest.mark.asyncio
MODEL = "gpt-6-luna"
SCHEMA = {"type":"object", "properties":{"ok":{"type":"boolean"}}, "required":["ok"], "additionalProperties":False}
ANSWER = json.dumps({"ok":True}, separators=(",", ":"))
MCP_SERVER = """import json, sys
for line in sys.stdin:
    request=json.loads(line)
    if 'id' not in request: continue
    method=request['method']
    if method=='initialize':
        result={'protocolVersion':request['params']['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'actor_fixture_echo','version':'1'}}
    elif method=='tools/list':
        result={'tools':[{'name':'fixture_echo','description':'Harmless acceptance fixture; never invoked.','inputSchema':{'type':'object','properties':{},'additionalProperties':False}}]}
    elif method=='tools/call':
        # Any execution invalidates this text-only fixture; never perform work.
        raise SystemExit(73)
    elif method=='ping': result={}
    else: raise SystemExit(74)
    print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}),flush=True)
"""


def request_tools(request):
    """Responses + ResponsesLite: additional_tools is a real callable surface."""
    specs = request.get("tools", [])
    if not isinstance(specs,list): raise AssertionError("Unexpected top-level tools representation")
    result = list(specs)
    for item in request.get("input",[]):
        if isinstance(item,dict) and item.get("type") == "additional_tools":
            tools = item.get("tools")
            if not isinstance(tools,list): raise AssertionError("Unexpected ResponsesLite tools representation")
            result.extend(tools)
    return result


def callable_names(specs):
    names = []
    for item in specs:
        if not isinstance(item,dict): raise AssertionError("Unexpected callable specification")
        if item.get("type") == "namespace": names.extend(callable_names(item["tools"]))
        elif item.get("type") == "tool_search": names.append("tool_search")
        else:
            name = item.get("name")
            if name is None and isinstance(item.get("function"),dict): name=item["function"].get("name")
            if not isinstance(name,str): raise AssertionError("Unexpected callable name representation")
            names.append(name)
    return names


def events(answer=ANSWER, phase=None):
    item = {"id":"msg_fixture", "type":"message", "role":"assistant", "status":"completed",
        "content":[{"type":"output_text", "text":answer, "annotations":[]}]}
    if phase is not None: item["phase"] = phase
    values = [
        {"type":"response.created", "response":{"id":"resp_fixture", "model":MODEL, "status":"in_progress", "output":[]}},
        {"type":"response.output_item.added", "output_index":0, "item":{**item,"status":"in_progress","content":[]}},
        {"type":"response.content_part.added", "item_id":item["id"], "output_index":0, "content_index":0,
            "part":{"type":"output_text","text":"","annotations":[]}},
        {"type":"response.output_text.delta", "item_id":item["id"], "output_index":0, "content_index":0,"delta":answer},
        {"type":"response.output_text.done", "item_id":item["id"], "output_index":0, "content_index":0,"text":answer},
        {"type":"response.output_item.done", "output_index":0,"item":item},
        {"type":"response.completed", "response":{"id":"resp_fixture","model":MODEL,"status":"completed","output":[item],
            "usage":{"input_tokens":10,"output_tokens":3,"total_tokens":13}}},
    ]
    return b"".join(("event: " + value["type"] + "\ndata: " + json.dumps(value) + "\n\n").encode() for value in values)


class ResponsesFixture:
    def __init__(self, *, answer=ANSWER, phase=None, external_marker=None):
        self.requests = []
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self): self.send_error(404)
            def do_POST(self):
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 2*1024**2 or self.path != "/v1/responses":
                    self.send_error(400); return
                value = json.loads(self.rfile.read(size))
                # Keep only the fields this acceptance actually checks.
                fixture.requests.append({"model":value.get("model"),"tools":request_tools(value),
                    "format":value.get("text",{}).get("format"),"authorization_present":"Authorization" in self.headers,
                    "shape":{key:type(item).__name__ for key,item in value.items()}, "tool_choice":value.get("tool_choice")})
                if external_marker is not None:
                    fixture.requests[-1].update({
                        "external_in_function_output":any(isinstance(item,dict) and item.get("type")=="function_call_output"
                            and external_marker in json.dumps(item) for item in value.get("input",[])),
                        "external_in_trusted_instruction":external_marker in str(value.get("instructions","")) or any(
                            isinstance(item,dict) and item.get("role") in ("developer","system")
                            and external_marker in json.dumps(item) for item in value.get("input",[])),
                    })
                body = events(answer, phase)
                self.send_response(200); self.send_header("Content-Type","text/event-stream")
                self.send_header("Content-Length",str(len(body))); self.end_headers()
                try: self.wfile.write(body); self.wfile.flush()
                except (BrokenPipeError,ConnectionResetError): pass
        self.server = ThreadingHTTPServer(("127.0.0.1",0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self): self.thread.start(); return self
    def __exit__(self,*args):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=1)
        assert not self.thread.is_alive()


async def run_fixture(tmp_path, catalog, overrides, *, output_schema=SCHEMA):
    # Import fails clearly if someone runs the explicit test without its extra.
    import openai_codex as sdk
    assert version("openai-codex") == version("openai-codex-cli-bin") == PINNED_SDK_VERSION
    home = tmp_path / "isolated-home"; home.mkdir(mode=0o700)
    cwd = tmp_path / "empty-workspace"; cwd.mkdir(mode=0o700)
    catalog_path = tmp_path / "catalog.json"; catalog_path.write_text(json.dumps(catalog))
    environment = {key:str(home / key.lower()) for key in ("HOME","CODEX_HOME","XDG_CONFIG_HOME","XDG_DATA_HOME","XDG_STATE_HOME")}
    for value in environment.values(): Path(value).mkdir(mode=0o700)
    import sys
    # Harmless inherited server in the isolated auth home. The separate MCP
    # positive control omits the old empty overlay; no merge semantics assumed.
    (Path(environment["CODEX_HOME"]) / "config.toml").write_text(
        '[mcp_servers.fixture_echo]\ncommand=' + json.dumps(sys.executable) + '\nargs=' +
        json.dumps(["-I","-S","-c",MCP_SERVER]) + '\nstartup_timeout_sec=3\ntool_timeout_sec=2\n')
    with ResponsesFixture() as fixture:
        config = sdk.CodexConfig(cwd=str(cwd), env=environment, client_name="actor_authfree_policy_fixture",
            config_overrides=tuple(overrides) + (
                'model_provider="actor_fixture"', 'model_providers.actor_fixture.name="Local auth-free fixture"',
                f'model_providers.actor_fixture.base_url="http://127.0.0.1:{fixture.server.server_port}/v1"',
                'model_providers.actor_fixture.wire_api="responses"', 'model_providers.actor_fixture.requires_openai_auth=false',
                'model_providers.actor_fixture.supports_websockets=false',
                'model_providers.actor_fixture.request_max_retries=0', 'model_providers.actor_fixture.stream_max_retries=0',
                'model_providers.actor_fixture.stream_idle_timeout_ms=5000',
                'cli_auth_credentials_store="file"', 'mcp_oauth_credentials_store="file"',
                'check_for_update_on_startup=false', 'analytics.enabled=false', 'feedback.enabled=false',
                'model_catalog_json=' + json.dumps(str(catalog_path)),))
        config.launch_args_override = codex_launch_args(config)
        client = sdk.AsyncCodex(config)
        try:
            await asyncio.wait_for(client.__aenter__(), 10)
            thread = await asyncio.wait_for(client.thread_start(model=MODEL, model_provider="actor_fixture",
                approval_mode=sdk.ApprovalMode.deny_all, sandbox=sdk.Sandbox.read_only, ephemeral=True,
                base_instructions="This is a local protocol fixture. Return the fixed structured answer."), 10)
            result = await asyncio.wait_for(thread.run("Return the fixture answer.", output_schema=output_schema,
                approval_mode=sdk.ApprovalMode.deny_all), 12)
            assert result.status.value == "completed" and json.loads(result.final_response) == {"ok":True}
        finally: await asyncio.wait_for(client.close(), 5)
    assert len(fixture.requests) == 1
    request = fixture.requests[0]
    assert request["model"] == MODEL and request["authorization_present"] is False
    if output_schema is not None:
        assert request["format"]["type"] == "json_schema" and request["format"]["schema"] == output_schema
    return request


async def test_original_feature_flags_do_not_physically_remove_all_model_tools(tmp_path):
    row = json.loads(files("actor_codex").joinpath("gpt6_luna_policy_model.json").read_text())
    request = await run_fixture(tmp_path, {"models":[row]}, (
        "features.shell_tool=false","features.apps=false","features.plugins=false",
        "features.multi_agent=false",'web_search="disabled"',"mcp_servers={}"))
    names = callable_names(request["tools"])
    assert names


async def test_inherited_harmless_mcp_exposes_discovery_without_explicit_disable(tmp_path):
    from actor_codex.policy import restricted_catalog, RESTRICTIVE_OVERRIDES
    request = await run_fixture(tmp_path, restricted_catalog(), RESTRICTIVE_OVERRIDES)
    # This pinned runtime defers individual MCP schemas behind a real
    # tool_search callable. Do not claim fixture_echo itself was exposed.
    assert any(item.get("type") == "tool_search" for item in request["tools"])
    assert any(item.get("type") == "namespace" for item in request["tools"])


async def test_restricted_policy_physically_emits_no_tool_specs(tmp_path):
    from actor_codex.policy import restricted_catalog, RESTRICTIVE_OVERRIDES, mcp_disable_override
    request = await run_fixture(tmp_path, restricted_catalog(), RESTRICTIVE_OVERRIDES +
        (mcp_disable_override(("fixture_echo",)),))
    if request["tools"] != []:
        pytest.fail("Tool denial failed: nonempty actual request callable specifications")


async def test_production_commentary_hydrates_policy_and_streams_external_speech_with_real_sdk(tmp_path, monkeypatch):
    """Auth/catalog are synthetic; main public SDK/RPC/stream are production.

    Local transport overrides intentionally follow the fake availability seam;
    this does not claim production preflight accepts a custom provider or that
    any real account/model is available. No private SDK/provider state is set.
    """
    import copy
    from dataclasses import asdict, replace
    from types import SimpleNamespace as NS
    import openai_codex as sdk
    import actor_codex.commentary as commentary
    from actor_codex import policy
    from actor_broadcast.contracts import (CommentaryCandidate, CommentaryRequest, EvidenceRef,
        ProviderSegment, ProviderTerminal, Scope)

    assert version("openai-codex") == version("openai-codex-cli-bin") == PINNED_SDK_VERSION
    environment = {}
    for key in ("HOME","CODEX_HOME","XDG_CONFIG_HOME","XDG_DATA_HOME","XDG_STATE_HOME"):
        directory = tmp_path / key.lower(); directory.mkdir(mode=0o700); environment[key] = str(directory)
    import sys
    (Path(environment["CODEX_HOME"]) / "config.toml").write_text(
        '[mcp_servers.fixture_echo]\ncommand=' + json.dumps(sys.executable) + '\nargs=' +
        json.dumps(["-I","-S","-c",MCP_SERVER]) + '\nstartup_timeout_sec=3\ntool_timeout_sec=2\n')
    steps = []; observed_threads = []; produced_policy = []; preflight_closed = []
    ref = EvidenceRef("public-fixture-fact","1")
    marker = "UNTRUSTED_EXTERNAL_FIXTURE_ONLY"
    text = "Мы видели подтверждённое событие."
    speech = {"segments":[{"text":text,"evidence_versions":[asdict(ref)]}],"notes":[]}
    real_prepare_policy = policy.prepare_policy

    class MetadataOnlyPreflight:
        def __init__(self, config): pass
        async def start(self): steps.append("synthetic_preflight_start")
        async def initialize(self): pass
        async def request(self, method, params, *, response_model):
            assert method == "config/read"
            return NS(config=NS(model_provider="openai", model_catalog_json=None,
                model_extra={"mcp_servers":{"fixture_echo":{"enabled":True}}}))
        async def account_read(self, params): return NS(account=NS(root=NS(type="chatgpt")))
        async def model_list(self): return NS(data=[NS(id=MODEL,model=MODEL,
            supported_reasoning_efforts=[NS(reasoning_effort="low")])])
        async def close(self): preflight_closed.append(True); steps.append("synthetic_preflight_closed")

    class MainClient(sdk.AsyncCodex):
        # Only account metadata is substituted; every main SDK start, RPC,
        # ExternalMessage conversion, streaming event and close remains real.
        async def account(self): return NS(account=NS(root=NS(type="chatgpt")), requires_openai_auth=False)
        async def thread_start(self, **options):
            assert preflight_closed
            observed_threads.append(copy.deepcopy(options)); steps.append("real_thread_start")
            return await super().thread_start(**options)

    class PublicSDKFacade:
        AsyncCodex = MainClient
        def __getattr__(self, name): return getattr(sdk, name)

    with ResponsesFixture(answer=json.dumps(speech,ensure_ascii=False),phase="final_answer",external_marker=marker) as fixture:
        async def metadata_preflight(config, model, workspace, *, owner):
            config.env = dict(environment)  # child-only isolation, never os.environ
            prepared = await real_prepare_policy(config,model,workspace,owner=owner,
                client_factory=MetadataOnlyPreflight,response_model=object)
            # Fake Responses transport is the sole post-preflight test override.
            local = ('model_provider="actor_fixture"',
                'model_providers.actor_fixture.name="Local auth-free fixture"',
                f'model_providers.actor_fixture.base_url="http://127.0.0.1:{fixture.server.server_port}/v1"',
                'model_providers.actor_fixture.wire_api="responses"',
                'model_providers.actor_fixture.requires_openai_auth=false',
                'model_providers.actor_fixture.supports_websockets=false',
                'model_providers.actor_fixture.request_max_retries=0',
                'model_providers.actor_fixture.stream_max_retries=0',
                'cli_auth_credentials_store="file"','mcp_oauth_credentials_store="file"',
                'check_for_update_on_startup=false','analytics.enabled=false','feedback.enabled=false')
            prepared = replace(prepared,config_overrides=prepared.config_overrides+local)
            produced_policy.append(prepared)
            return prepared
        monkeypatch.setattr(commentary,"prepare_policy",metadata_preflight)
        monkeypatch.setattr(commentary,"sdk_module",lambda:PublicSDKFacade())
        provider = commentary.CodexCommentaryProvider()
        candidate = CommentaryCandidate(Scope("fixture-broadcast","fixture-match",1,1),1,"candidate",
            "reactive",0,"topic",(ref,),9999999999999)
        request = CommentaryRequest(candidate,{"evidence":[{"ref":asdict(ref),"text":marker}]},256,"utterance",1)
        try:
            result = await asyncio.wait_for(_collect_provider(provider,request),15)
            segments = [value for value in result if isinstance(value,ProviderSegment)]
            assert len(segments)==1, provider.report().get("failure_diagnostic")
            assert segments[0].text==text and segments[0].evidence_versions==(ref,)
            terminal = result[-1]
            assert isinstance(terminal,ProviderTerminal) and terminal.status=="completed"
            assert terminal.provider_calls==1 and terminal.tool_calls==0 and terminal.tool_count_scope=="sdk_item_lifecycle"
            assert observed_threads[0]["config"] == produced_policy[0].thread_config
            assert observed_threads[0]["ephemeral"] is True and observed_threads[0]["sandbox"] == sdk.Sandbox.read_only
            assert observed_threads[0]["approval_mode"] == sdk.ApprovalMode.deny_all
            assert marker not in observed_threads[0]["base_instructions"]
            assert steps==["synthetic_preflight_start","synthetic_preflight_closed","real_thread_start"]
        finally: await asyncio.wait_for(provider.close(),10)
        report = provider.report()
        assert report["closed"] and not report["decision_active"] and report["unique_model_turns"]==1
        assert len(fixture.requests)==1
        captured=fixture.requests[0]
        if captured["tools"] != []: pytest.fail("Production composition emitted callable specifications")
        assert not captured["authorization_present"] and captured["external_in_function_output"]
        assert not captured["external_in_trusted_instruction"]
        assert captured["format"]["schema"] == commentary.speech_schema((ref,))


async def _collect_provider(provider, request):
    return [value async for value in provider.generate(request)]
