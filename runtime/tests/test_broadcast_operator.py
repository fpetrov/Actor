"""Real loopback HTTP and injected exact-owned Keychain backend; no UI/auth."""
import asyncio
import json
import re

import pytest

from actor_broadcast.operator import OperatorServer,public_status
from actor_broadcast.secrets import MacOSKeychainSecretStore,NativeKeychainBackend,SecretStoreError,SERVICE


class Backend:
    def __init__(self):self.items={};self.operations=[]
    def get(self,name):self.operations.append(("get",name));return self.items.get(name)
    def add(self,name,value):
        self.operations.append(("add",name))
        if name in self.items:return False
        self.items[name]=value;return True
    def set(self,name,value):self.operations.append(("set",name));self.items[name]=value
    def delete(self,name):self.operations.append(("delete",name));return self.items.pop(name,None)is not None


class Controller:
    def __init__(self):self.state="Idle";self.commands=[];self.starts=0;self.hold=asyncio.Event()
    async def status(self):
        return {"state":self.state,"channel":"fedorr_codex","room_code":"ABC123","broadcast_session_id":"session",
            "rooms":[{"room_code":"ABC123","state":"Ready","mode":"SocialDeduction","players":4,"capacity":6,
                "private_token":"secret","player_private_memory":"not-allowed"}],
            "ready":{"observer":True,"model":True,"tts":True,"obs":True,"audio":True,"chat":False},
            "live":self.state=="Live","usage":{"calls":1,"input_tokens":None,"password":"secret"},
            "last_spoken":{"text":"Разрешённая законченная реплика.","completed_at":1},
            "private_context":"not-allowed","access_token":"secret"}
    async def execute(self,action,payload):
        self.commands.append((action,payload))
        if action=="preview":self.state="PreviewReady"
        if action=="public_start":
            if self.state!="Live":self.starts+=1
            self.state="Live"
        if action in ("stop","emergency_stop"):self.state="Stopped"
        return await self.status()


def test_explicit_chat_optout_is_preserved_without_fabricating_chat_readiness():
    result=public_status({"state":"PreviewReady","chat_enabled":False,"ready":{"chat":False}})
    assert result["chat_enabled"] is False and result["ready"]["chat"] is False
    assert public_status({"state":"Idle"})["chat_enabled"] is True


@pytest.fixture
async def operator():
    backend=Backend();store=MacOSKeychainSecretStore(backend=backend);controller=Controller()
    server=OperatorServer(controller,secret_store=store)
    await server.start()
    yield server,controller,store
    await server.aclose()


async def http(server,path="/",*,method="GET",headers=None,body=b"",raw=None):
    reader,writer=await asyncio.open_connection("127.0.0.1",server.port)
    if raw is None:
        values={"Host":"127.0.0.1:"+str(server.port),"Connection":"close",**(headers or {})}
        if method=="POST":values.setdefault("Content-Length",str(len(body)))
        raw=(method+" "+path+" HTTP/1.1\r\n"+"".join(key+": "+str(value)+"\r\n" for key,value in values.items())+"\r\n").encode()+body
    writer.write(raw);await writer.drain()
    data=await asyncio.wait_for(reader.read(),5)
    writer.close();await writer.wait_closed()
    head,content=data.split(b"\r\n\r\n",1)
    lines=head.decode().split("\r\n")
    return int(lines[0].split()[1]),dict(line.split(": ",1) for line in lines[1:]),content


async def session(server):
    status,headers,page=await http(server)
    assert status==200
    cookie=headers["Set-Cookie"].split(";",1)[0]
    csrf=re.search(r'name="csrf-token" content="([^"]+)"',page.decode())[1]
    return {"Cookie":cookie,"Origin":server.url[:-1],"X-CSRF-Token":csrf,"Content-Type":"application/json"}


async def action(server,name,payload,headers):
    return await http(server,"/api/action",method="POST",headers=headers,body=json.dumps({"action":name,"payload":payload}).encode())


async def test_nonsecret_navigation_cookie_csrf_and_public_start_separate_preview(operator):
    server,controller,store=operator
    status,headers,page=await http(server)
    assert status==200 and "HttpOnly" in headers["Set-Cookie"] and "SameSite=Strict" in headers["Set-Cookie"]
    secret=store.get("operator-http")
    assert secret not in page.decode() and secret not in server.url and "Access-Control-Allow-Origin" not in headers
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    auth=await session(server)
    status,_,_=await action(server,"preview",{"room_code":"ABC123"},auth)
    assert status==200 and controller.state=="PreviewReady" and controller.starts==0
    payload={"channel":"fedorr_codex","broadcast_session_id":"session","confirmation":"START PUBLIC STREAM"}
    status,_,_=await action(server,"public_start",payload,auth)
    assert status==200 and controller.starts==1
    status,_,_=await action(server,"public_start",payload,auth)
    assert status==200 and controller.starts==1


@pytest.mark.parametrize("headers",[
    {"Host":"attacker.example"},{"Origin":"https://attacker.example"},{"Origin":"null"},
    {"Referer":"https://attacker.example/path"},{"Sec-Fetch-Site":"cross-site"},
])
async def test_dns_rebinding_cross_origin_and_cross_site_navigation_are_denied(operator,headers):
    server,controller,_=operator
    status,_,_=await http(server,headers=headers)
    assert status==403 and not controller.commands


async def test_cookie_without_csrf_or_origin_does_not_mutate(operator):
    server,controller,_=operator;auth=await session(server)
    for key in ("Origin","X-CSRF-Token","Cookie"):
        bad=dict(auth);bad.pop(key)
        status,_,_=await action(server,"preview",{"room_code":"ABC123"},bad)
        assert status in (401,403)
    assert not controller.commands


async def test_cli_keychain_bearer_not_renderer_token_and_no_secret_in_status(operator):
    server,controller,store=operator
    auth={"Authorization":"Bearer "+store.get("operator-http"),"Content-Type":"application/json"}
    status,_,body=await http(server,"/api/status",headers=auth)
    assert status==200 and b"access_token" not in body and b"private_context" not in body and b"private_token" not in body
    assert json.loads(body)["usage"]["input_tokens"] is None
    status,_,_=await action(server,"preview",{"room_code":"ABC123"},auth)
    assert status==200
    status,_,_=await action(server,"stop",{},{**auth,"Authorization":"Bearer renderer-capability"})
    assert status==401 and controller.state=="PreviewReady"


@pytest.mark.parametrize("payload",[
    {"channel":"other","broadcast_session_id":"session","confirmation":"START PUBLIC STREAM"},
    {"channel":"fedorr_codex","broadcast_session_id":"old","confirmation":"START PUBLIC STREAM"},
    {"channel":"fedorr_codex","broadcast_session_id":"session","confirmation":"preview"},
])
async def test_publication_requires_current_channel_scope_and_explicit_confirmation(operator,payload):
    server,controller,_=operator;controller.state="PreviewReady";auth=await session(server)
    status,_,_=await action(server,"public_start",payload,auth)
    assert status in (400,409) and controller.starts==0


async def test_emergency_stop_is_not_queued_behind_slow_normal_action(operator):
    server,controller,_=operator;auth=await session(server)
    await server._action_lock.acquire()
    try:
        status,_,_=await asyncio.wait_for(action(server,"emergency_stop",{},auth),1)
        assert status==200 and controller.state=="Stopped"
    finally:server._action_lock.release()


async def test_header_smuggling_body_limit_unknown_action_and_arbitrary_shell_are_rejected(operator):
    server,controller,_=operator;host="127.0.0.1:"+str(server.port)
    requests=[f"GET / HTTP/1.1\r\nHost: {host}\r\nHost: {host}\r\n\r\n",
        f"POST /api/action HTTP/1.1\r\nHost: {host}\r\nTransfer-Encoding: chunked\r\n\r\n",
        f"POST /api/action HTTP/1.1\r\nHost: {host}\r\nContent-Length: 8193\r\n\r\n"]
    for raw in requests:
        status,_,_=await http(server,raw=raw.encode())
        assert status in (400,413)
    auth=await session(server)
    status,_,_=await action(server,"shell",{"command":"rm -rf /"},auth)
    assert status==400 and not controller.commands


def test_exact_owned_keychain_items_idempotent_secret_not_logged(capsys):
    backend=Backend();store=MacOSKeychainSecretStore(backend=backend)
    first=store.get_or_create("operator-http")
    assert first==store.get_or_create("operator-http") and len(first)>=32
    store.set("broadcast-server-operator-key","private-token")
    assert store.get("broadcast-server-operator-key")=="private-token" and store.delete("broadcast-server-operator-key")
    for item in ("OBS native credentials","com.apple.token","../other",""):
        with pytest.raises(SecretStoreError,match="secret_item_not_owned"):store.get(item)
    assert not capsys.readouterr().out and SERVICE=="org.actor.broadcast.v1"


def test_keychain_denial_never_falls_back_to_a_plaintext_file(tmp_path):
    class Denied(Backend):
        def get(self,name):raise SecretStoreError("keychain_denied")
    store=MacOSKeychainSecretStore(backend=Denied())
    with pytest.raises(SecretStoreError,match="keychain_denied"):store.get_or_create("operator-http")
    assert list(tmp_path.iterdir())==[]


def test_native_query_is_fixed_generic_password_service_and_exact_name_only():
    backend=object.__new__(NativeKeychainBackend)
    backend._constant=lambda name:name
    query=backend._query("operator-http")
    assert query=={"kSecClass":"kSecClassGenericPassword","kSecAttrService":"org.actor.broadcast.v1",
        "kSecAttrAccount":"operator-http","kSecUseAuthenticationUI":"kSecUseAuthenticationUIFail"}


def test_rotated_twitch_pair_is_one_atomic_item_with_unix_expiry_and_validated_restart():
    backend=Backend();store=MacOSKeychainSecretStore(backend=backend)
    store.save_twitch_token_pair(access_token="access",refresh_token="refresh",expires_at_unix=1200)
    assert backend.operations==[("set","twitch-token-pair")]
    loaded=store.load_twitch_token_pair(wall_clock=lambda:1000,monotonic_clock=lambda:10)
    assert loaded["expires_at_monotonic"]==210 and loaded["expires_at_unix"]==1200
    expired=store.load_twitch_token_pair(wall_clock=lambda:1300,monotonic_clock=lambda:20)
    assert expired["expires_at_monotonic"]==20  # Never revive access on restart.
    backend.items["twitch-token-pair"]='{"access_token":"secret","expires_at_unix":NaN}'
    with pytest.raises(SecretStoreError,match="twitch_token_pair_invalid"):store.load_twitch_token_pair()
    for old in ("twitch-access-token","twitch-refresh-token","twitch-client-secret"):
        with pytest.raises(SecretStoreError,match="not_owned"):store.get(old)
