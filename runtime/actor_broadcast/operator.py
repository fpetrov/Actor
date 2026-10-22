"""Owned loopback operator HTTP/UI. No token URLs, CORS or shell actions.

Initial direct navigation creates an HttpOnly/SameSite session. State changes
require exact Origin plus per-session CSRF, or a separate Keychain bearer for
the owner's CLI. Renderer capabilities never authenticate this service.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
import html
import json
import math
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit

from .contracts import bounded_text

READY=frozenset({"observer","model","tts","obs","audio","chat"})
USAGE=frozenset({"calls_reserved","calls","input_tokens","output_tokens","unknown_usage","calls_per_match",
    "input_tokens_charged","output_tokens_charged","unknown_usage_calls","idle_calls","hard_calls_per_hour",
    "hard_calls_per_match","calls_remaining","input_tokens_remaining","output_tokens_remaining"})
STATES=frozenset({"Idle","Preparing","PreviewReady","Live","Ending","Stopping","Stopped","Failed"})


class OperatorError(RuntimeError):
    def __init__(self,code,status=400):self.code,self.status=code,status;super().__init__(code)


def scalar(value,maximum=512):
    if value is None:return None
    if type(value)is bool:return value
    if type(value)in(int,float) and math.isfinite(value) and abs(value)<=10**12:return value
    if isinstance(value,str) and len(value.encode())<=maximum and "\0" not in value:return value
    return None


def public_status(value):
    if not isinstance(value,dict):raise OperatorError("operator_status_invalid",503)
    state=value.get("state")
    if state not in STATES:raise OperatorError("operator_status_invalid",503)
    result={"state":state,"live":value.get("live") is True,"chat_enabled":value.get("chat_enabled") is not False}
    for key,maximum in (("reason",64),("channel",64),("room_code",6),("broadcast_session_id",128)):
        item=scalar(value.get(key),maximum)
        if isinstance(item,str):result[key]=item
    result["ready"]={name:value.get("ready",{}).get(name) is True for name in READY}
    spoken=value.get("last_spoken",{})
    if isinstance(spoken,dict):
        result["last_spoken"]={name:item for name,maximum in (("text",1024),("completed_at",64))
            if (item:=scalar(spoken.get(name),maximum)) is not None}
    commentary=value.get("commentary",{})
    if isinstance(commentary,dict):
        result['commentary']={name:commentary[name] for name in ('idle_enabled','allow_memory_callbacks')
            if type(commentary.get(name))is bool}
        for name,minimum,maximum in (('idle_after_seconds',5,120),('idle_deadline_seconds',0,86400)):
            item=commentary.get(name)
            if type(item)in(int,float) and math.isfinite(item) and minimum<=item<=maximum:
                result['commentary'][name]=item
        reason=commentary.get('idle_reason')
        if isinstance(reason,str) and re.fullmatch(r'[a-z0-9_]{1,64}',reason):result['commentary']['idle_reason']=reason
        pending=commentary.get('pending_speech_segments')
        if type(pending)is int and 0<=pending<=2:result['commentary']['pending_speech_segments']=pending
        calls=commentary.get('calls_by_mode')
        if isinstance(calls,dict):
            result['commentary']['calls_by_mode']={name:calls[name] for name in ('reactive','idle_recap','memory_callback')
                if type(calls.get(name))is int and 0<=calls[name]<=1000000}
    usage=value.get("usage",{})
    if isinstance(usage,dict):result["usage"]={key:scalar(usage[key],0) for key in USAGE if key in usage}
    metrics=value.get("metrics",{})
    if isinstance(metrics,dict):
        result["metrics"]={key:scalar(item,0) for key,item in list(metrics.items())[:24]
            if isinstance(key,str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}",key) and type(item)in(int,float)}
    rooms=value.get("rooms",[])
    if not isinstance(rooms,list) or len(rooms)>32:raise OperatorError("operator_rooms_invalid",503)
    result["rooms"]=[]
    for row in rooms:
        if not isinstance(row,dict):continue
        code=row.get("room_code",row.get("code"))
        if not isinstance(code,str) or not re.fullmatch(r"[A-Z0-9]{6}",code):continue
        result["rooms"].append({"room_code":code,**{key:item for key,limit in (("state",64),("mode",64),("players",0),("capacity",0))
            if (item:=scalar(row.get(key),limit)) is not None}})
    encoded=json.dumps(result,ensure_ascii=False,allow_nan=False)
    if len(encoded.encode())>16384:raise OperatorError("operator_status_limit",503)
    return result


def action_payload(value):
    if not isinstance(value,dict) or set(value)!={"action","payload"} or not isinstance(value["payload"],dict):
        raise OperatorError("operator_action_invalid")
    action,payload=value["action"],value["payload"]
    fields={"preview":{"room_code"},"public_start":{"channel","broadcast_session_id","confirmation"},
        "stop":set(),"emergency_stop":set(),"configure":{"idle_enabled","idle_after_seconds","allow_memory_callbacks"}}
    if not isinstance(action,str) or action not in fields:raise OperatorError('operator_action_invalid')
    if action=='configure':
        if not payload or not set(payload)<=fields[action]:raise OperatorError('operator_action_invalid')
    elif set(payload)!=fields[action]:raise OperatorError('operator_action_invalid')
    if action=="preview" and (not isinstance(payload["room_code"],str) or not re.fullmatch(r"[A-Z0-9]{6}",payload["room_code"])):
        raise OperatorError("operator_room_invalid")
    if action=="public_start":
        if payload["confirmation"]!="START PUBLIC STREAM":raise OperatorError("operator_public_confirmation_required")
        for key in ("channel","broadcast_session_id"):
            try:bounded_text(payload[key],64 if key=="channel" else 128)
            except (ValueError,TypeError):raise OperatorError("operator_action_invalid") from None
    if action=="configure":
        if any(name in payload and type(payload[name])is not bool for name in ('idle_enabled','allow_memory_callbacks')):
            raise OperatorError("operator_idle_config_invalid")
        if 'idle_after_seconds' in payload:
            value=payload['idle_after_seconds']
            if type(value)not in(int,float) or not math.isfinite(value) or not 5<=value<=120:
                raise OperatorError('operator_idle_config_invalid')
    return action,payload


class OperatorServer:
    def __init__(self,controller,*,secret_store,port=0,clock=time.monotonic):
        if type(port)is not int or not 0<=port<=65535:raise ValueError("Invalid loopback port")
        self.controller,self.store,self.port,self.clock=controller,secret_store,port,clock
        self._server=None;self._origin=None;self._api_secret=None
        self._sessions=OrderedDict();self._connections=set();self._closed=False
        self._action_lock=asyncio.Lock()

    @property
    def url(self):
        if self._origin is None:raise OperatorError("operator_not_started",503)
        return self._origin+"/"

    async def start(self):
        if self._closed:raise OperatorError("operator_closed",503)
        if self._server:return
        self._api_secret=await asyncio.to_thread(self.store.get_or_create,"operator-http")
        self._server=await asyncio.start_server(self._handle,"127.0.0.1",self.port,limit=16384)
        self.port=self._server.sockets[0].getsockname()[1]
        self._origin="http://127.0.0.1:"+str(self.port)

    def _origin_check(self,headers):
        if headers.get("host")!=urlsplit(self._origin).netloc:raise OperatorError("operator_host_rejected",403)
        origin=headers.get("origin")
        if origin is not None and origin!=self._origin:raise OperatorError("operator_origin_rejected",403)
        referer=headers.get("referer")
        if referer is not None:
            try:parsed=urlsplit(referer)
            except ValueError:raise OperatorError("operator_origin_rejected",403) from None
            if parsed.scheme+"://"+parsed.netloc!=self._origin:raise OperatorError("operator_origin_rejected",403)
        if headers.get("sec-fetch-site") not in (None,"none","same-origin"):
            raise OperatorError("operator_origin_rejected",403)

    def _session(self,headers):
        cookie=headers.get("cookie","")
        values={}
        for pair in cookie.split(";"):
            if "=" in pair:
                key,value=pair.strip().split("=",1)
                if key in values:raise OperatorError("operator_auth_required",401)
                values[key]=value
        token=values.get("actor_operator")
        session=self._sessions.get(token)
        if session and self.clock()-session[1]<=28800:return session
        return None

    def _authenticate(self,headers,*,mutation):
        authorization=headers.get("authorization")
        if authorization is not None:
            if not isinstance(authorization,str) or not secrets.compare_digest(authorization,"Bearer "+self._api_secret):
                raise OperatorError("operator_auth_required",401)
            return
        session=self._session(headers)
        if session is None:raise OperatorError("operator_auth_required",401)
        if mutation:
            token=headers.get("x-csrf-token","")
            if headers.get("origin")!=self._origin or not secrets.compare_digest(token,session[0]):
                raise OperatorError("operator_csrf_rejected",403)

    async def _dispatch(self,method,path,headers,body):
        self._origin_check(headers)
        resources=Path(__file__).with_name("resources")
        if method=="GET" and path=="/":
            token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
            self._sessions[token]=(csrf,self.clock())
            while len(self._sessions)>8:self._sessions.popitem(last=False)
            page=(resources/"operator.html").read_text().replace("{{CSRF}}",html.escape(csrf,quote=True))
            return 200,"text/html; charset=utf-8",page.encode(),{"Set-Cookie":"actor_operator="+token+"; HttpOnly; SameSite=Strict; Path=/"}
        files={"/operator.js":("operator.js","application/javascript; charset=utf-8"),
            "/operator.css":("operator.css","text/css; charset=utf-8")}
        if method=="GET" and path in files:
            name,mime=files[path]
            return 200,mime,(resources/name).read_bytes(),{}
        if method=="GET" and path=="/api/status":
            self._authenticate(headers,mutation=False)
            async with asyncio.timeout(3):value=await self.controller.status()
            return 200,"application/json",json.dumps(public_status(value),ensure_ascii=False).encode(),{}
        if method=="POST" and path=="/api/action":
            self._authenticate(headers,mutation=True)
            if headers.get("content-type")!="application/json":raise OperatorError("operator_json_required",415)
            try:value=json.loads(body,object_pairs_hook=_unique)
            except (ValueError,UnicodeError):raise OperatorError("operator_json_invalid") from None
            action,payload=action_payload(value)
            if action=="emergency_stop":
                async with asyncio.timeout(2):
                    await self.controller.execute(action,payload)
                    value=await self.controller.status()
                return 200,"application/json",json.dumps(public_status(value),ensure_ascii=False).encode(),{}
            async with self._action_lock:
                async with asyncio.timeout(10):
                    # Publication binds to the currently displayed session and
                    # configured channel; caller-supplied metadata is not truth.
                    if action=="public_start":
                        status=public_status(await self.controller.status())
                        if (status["state"] not in ("PreviewReady","Live") or status.get("channel")!=payload["channel"] or
                            status.get("broadcast_session_id")!=payload["broadcast_session_id"]):
                            raise OperatorError("operator_preview_changed",409)
                    await self.controller.execute(action,payload)
                    value=await self.controller.status()
            return 200,"application/json",json.dumps(public_status(value),ensure_ascii=False).encode(),{}
        raise OperatorError("operator_route_not_found",404)

    async def _handle(self,reader,writer):
        task=asyncio.current_task();self._connections.add(task)
        try:
            if len(self._connections)>16:raise OperatorError("operator_busy",503)
            async with asyncio.timeout(3):header=await reader.readuntil(b"\r\n\r\n")
            if len(header)>16384:raise OperatorError("operator_headers_limit",431)
            lines=header[:-4].decode("ascii",errors="strict").split("\r\n")
            first=lines.pop(0).split(" ")
            if len(first)!=3 or first[2]!="HTTP/1.1" or len(first[1])>256:raise OperatorError("operator_request_invalid")
            method,path,_=first
            if len(lines)>32:raise OperatorError("operator_headers_limit",431)
            headers={}
            for line in lines:
                if ":" not in line or line[0].isspace():raise OperatorError("operator_header_invalid")
                name,value=line.split(":",1);name=name.lower()
                if name in headers or not re.fullmatch(r"[a-z0-9-]+",name):raise OperatorError("operator_header_invalid")
                headers[name]=value.strip()
            if "transfer-encoding" in headers:raise OperatorError("operator_chunked_rejected")
            length=headers.get("content-length","0")
            if not re.fullmatch(r"[0-9]{1,5}",length):raise OperatorError("operator_body_invalid")
            length=int(length)
            if length>8192 or method=="GET" and length:raise OperatorError("operator_body_limit",413)
            async with asyncio.timeout(3):body=await reader.readexactly(length)
            status,mime,response,extra=await self._dispatch(method,path,headers,body)
        except OperatorError as error:
            status,mime,response,extra=error.status,"application/json",json.dumps({"error":error.code}).encode(),{}
        except (TimeoutError,ValueError,UnicodeError,asyncio.IncompleteReadError,asyncio.LimitOverrunError):
            status,mime,response,extra=400,"application/json",b'{"error":"operator_request_invalid"}',{}
        except asyncio.CancelledError:
            writer.close();self._connections.discard(task);raise
        except Exception:
            status,mime,response,extra=503,"application/json",b'{"error":"operator_action_unavailable"}',{}
        try:
            reason={200:"OK",400:"Bad Request",401:"Unauthorized",403:"Forbidden",404:"Not Found",409:"Conflict",
                413:"Payload Too Large",415:"Unsupported Media Type",431:"Request Header Fields Too Large",503:"Unavailable"}.get(status,"Error")
            headers={"Content-Type":mime,"Content-Length":str(len(response)),"Connection":"close","Cache-Control":"no-store",
                "X-Content-Type-Options":"nosniff","Referrer-Policy":"no-referrer",
                "Permissions-Policy":"camera=(), microphone=(), display-capture=()",
                "Content-Security-Policy":"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",**extra}
            writer.write(("HTTP/1.1 "+str(status)+" "+reason+"\r\n"+"".join(key+": "+value+"\r\n" for key,value in headers.items())+"\r\n").encode()+response)
            async with asyncio.timeout(2):await writer.drain()
        except (OSError,TimeoutError):pass
        finally:
            writer.close()
            self._connections.discard(task)
            try:await writer.wait_closed()
            except OSError:pass

    async def aclose(self):
        self._closed=True
        if self._server:self._server.close();await self._server.wait_closed()
        pending=[task for task in self._connections if task is not asyncio.current_task()]
        for task in pending:task.cancel()
        if pending:
            done,_=await asyncio.wait(pending,timeout=2)
            for task in pending:
                if task not in done:task.add_done_callback(lambda value:None if value.cancelled() else value.exception())
        self._sessions.clear();self._api_secret=None


def _unique(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError("Duplicate JSON key")
        result[key]=value
    return result
