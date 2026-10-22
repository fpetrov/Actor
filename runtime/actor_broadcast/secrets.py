"""Exact owned generic-password items in native macOS Keychain.

No `security` subprocess, argv secret, native OBS auth-file read, enumeration,
or plaintext fallback. An injected backend is solely a test/application seam.
"""
from __future__ import annotations

import ctypes as C
import json
import math
import re
import secrets
import sys
import threading
import time

SERVICE="org.actor.broadcast.v1"
ITEMS=frozenset({"operator-http","obs-websocket-password","broadcast-server-operator-key",
    "obs-service-binding","twitch-token-pair"})


class SecretStoreError(RuntimeError):
    def __init__(self,code):self.code=code;super().__init__(code)


def _name(name):
    if name not in ITEMS:raise SecretStoreError("secret_item_not_owned")


def _value(value):
    if not isinstance(value,str) or not 1<=len(value.encode())<=8192 or "\0" in value:
        raise SecretStoreError("secret_value_invalid")


class NativeKeychainBackend:
    def __init__(self):
        if sys.platform!="darwin":raise SecretStoreError("keychain_platform_unsupported")
        self.security=C.CDLL("/System/Library/Frameworks/Security.framework/Security")
        self.cf=C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        ptr=C.c_void_p;index=C.c_long
        self.cf.CFStringCreateWithCString.argtypes=[ptr,C.c_char_p,C.c_uint32];self.cf.CFStringCreateWithCString.restype=ptr
        self.cf.CFDataCreate.argtypes=[ptr,ptr,index];self.cf.CFDataCreate.restype=ptr
        self.cf.CFDictionaryCreateMutable.argtypes=[ptr,index,ptr,ptr];self.cf.CFDictionaryCreateMutable.restype=ptr
        self.cf.CFDictionarySetValue.argtypes=[ptr,ptr,ptr]
        self.cf.CFRelease.argtypes=[ptr]
        self.cf.CFDataGetLength.argtypes=[ptr];self.cf.CFDataGetLength.restype=index
        self.cf.CFDataGetBytePtr.argtypes=[ptr];self.cf.CFDataGetBytePtr.restype=ptr
        for name,args in {"SecItemAdd":[ptr,ptr],"SecItemCopyMatching":[ptr,C.POINTER(ptr)],
            "SecItemUpdate":[ptr,ptr],"SecItemDelete":[ptr]}.items():
            function=getattr(self.security,name);function.argtypes=args;function.restype=C.c_int32
        class Callbacks(C.Structure):
            _fields_=[("version",index),("retain",ptr),("release",ptr),("description",ptr),("equal",ptr),("hash",ptr)]
        class ValueCallbacks(C.Structure):
            _fields_=[("version",index),("retain",ptr),("release",ptr),("description",ptr),("equal",ptr)]
        self.keys=Callbacks.in_dll(self.cf,"kCFTypeDictionaryKeyCallBacks")
        # Value callbacks have no hash field. Only their address is passed.
        self.values=ValueCallbacks.in_dll(self.cf,"kCFTypeDictionaryValueCallBacks")

    def _constant(self,name):
        return C.c_void_p.in_dll(self.security,name).value

    def _dictionary(self,attributes):
        result=self.cf.CFDictionaryCreateMutable(None,0,C.byref(self.keys),C.byref(self.values))
        if not result:raise SecretStoreError("keychain_unavailable")
        try:
            for name,value in attributes.items():
                owned=False
                if isinstance(value,str):
                    reference=self.cf.CFStringCreateWithCString(None,value.encode(),0x08000100);owned=True
                elif isinstance(value,bytes):
                    buffer=C.create_string_buffer(value)
                    reference=self.cf.CFDataCreate(None,C.cast(buffer,C.c_void_p),len(value));owned=True
                else:reference=value
                if not reference:raise SecretStoreError("keychain_unavailable")
                self.cf.CFDictionarySetValue(result,self._constant(name),reference)
                if owned:self.cf.CFRelease(reference)
            return result
        except BaseException:
            self.cf.CFRelease(result);raise

    def _query(self,name):
        _name(name)
        return {"kSecClass":self._constant("kSecClassGenericPassword"),"kSecAttrService":SERVICE,"kSecAttrAccount":name,
            "kSecUseAuthenticationUI":self._constant("kSecUseAuthenticationUIFail")}

    @staticmethod
    def _check(status):
        if status:raise SecretStoreError("keychain_denied" if status in (-25293,-25308,-128) else "keychain_operation_failed")

    def get(self,name):
        attributes=self._query(name)
        attributes.update(kSecReturnData=C.c_void_p.in_dll(self.cf,"kCFBooleanTrue").value,
            kSecMatchLimit=self._constant("kSecMatchLimitOne"))
        query=self._dictionary(attributes);result=C.c_void_p()
        try:
            status=self.security.SecItemCopyMatching(query,C.byref(result))
            if status==-25300:return None
            self._check(status)
            length=self.cf.CFDataGetLength(result)
            if not 1<=length<=8192:raise SecretStoreError("secret_value_invalid")
            try:return C.string_at(self.cf.CFDataGetBytePtr(result),length).decode("utf-8",errors="strict")
            except UnicodeError:raise SecretStoreError("secret_value_invalid") from None
        finally:
            if result:self.cf.CFRelease(result)
            self.cf.CFRelease(query)

    def add(self,name,value):
        attributes=self._query(name);attributes["kSecValueData"]=value.encode()
        query=self._dictionary(attributes)
        try:
            status=self.security.SecItemAdd(query,None)
            if status==-25299:return False
            self._check(status);return True
        finally:self.cf.CFRelease(query)

    def set(self,name,value):
        if self.add(name,value):return
        query=self._dictionary(self._query(name));attributes=self._dictionary({"kSecValueData":value.encode()})
        try:self._check(self.security.SecItemUpdate(query,attributes))
        finally:self.cf.CFRelease(query);self.cf.CFRelease(attributes)

    def delete(self,name):
        query=self._dictionary(self._query(name))
        try:
            status=self.security.SecItemDelete(query)
            if status==-25300:return False
            self._check(status);return True
        finally:self.cf.CFRelease(query)


class MacOSKeychainSecretStore:
    def __init__(self,*,backend=None):
        self.backend=backend if backend is not None else NativeKeychainBackend()
        self._lock=threading.Lock()

    def get(self,name):
        _name(name)
        with self._lock:
            value=self.backend.get(name)
            if value is not None:_value(value)
            return value

    def set(self,name,value):
        _name(name);_value(value)
        with self._lock:self.backend.set(name,value)

    def delete(self,name):
        _name(name)
        with self._lock:return self.backend.delete(name)

    def get_or_create(self,name):
        _name(name)
        with self._lock:
            value=self.backend.get(name)
            if value is None:
                proposed=secrets.token_urlsafe(32)
                # SecItemAdd's unique service/account key makes concurrent
                # processes converge; never overwrite another startup's token.
                self.backend.add(name,proposed)
                value=self.backend.get(name)
            _value(value)
            return value

    def save_twitch_token_pair(self,*,access_token,refresh_token,expires_at_unix):
        """One native update commits both rotating credentials and expiry."""
        _value(access_token);_value(refresh_token)
        if any(not re.fullmatch(r"[A-Za-z0-9._~+/=-]{1,4096}",value) for value in (access_token,refresh_token)):
            raise SecretStoreError("twitch_token_pair_invalid")
        if type(expires_at_unix)not in(int,float) or not math.isfinite(expires_at_unix) or expires_at_unix<=0:
            raise SecretStoreError("twitch_token_pair_invalid")
        value=json.dumps({"schema":"actor.twitch-token-pair.v1","access_token":access_token,
            "refresh_token":refresh_token,"expires_at_unix":expires_at_unix},separators=(",",":"),allow_nan=False)
        self.set("twitch-token-pair",value)

    def load_twitch_token_pair(self,*,wall_clock=time.time,monotonic_clock=time.monotonic):
        value=self.get("twitch-token-pair")
        if value is None:return None
        try:
            def pairs(items):
                result={}
                for key,item in items:
                    if key in result:raise ValueError("duplicate")
                    result[key]=item
                return result
            data=json.loads(value,object_pairs_hook=pairs)
            if (not isinstance(data,dict) or set(data)!={"schema","access_token","refresh_token","expires_at_unix"}
                or data["schema"]!="actor.twitch-token-pair.v1"):raise ValueError("shape")
            _value(data["access_token"]);_value(data["refresh_token"])
            if any(not re.fullmatch(r"[A-Za-z0-9._~+/=-]{1,4096}",data[key]) for key in ("access_token","refresh_token")):
                raise ValueError("token")
            expiry=data["expires_at_unix"]
            if type(expiry)not in(int,float) or not math.isfinite(expiry) or expiry<=0:raise ValueError("expiry")
            remaining=expiry-wall_clock()
            if remaining>86400:raise ValueError("expiry")
            # Expired access may still have a valid refresh credential. Do not
            # fabricate a fresh lifetime; callers refresh through officialOAuth.
            return {"access_token":data["access_token"],"refresh_token":data["refresh_token"],
                "expires_at_unix":expiry,"expires_at_monotonic":monotonic_clock()+max(0,remaining)}
        except (ValueError,TypeError,SecretStoreError):raise SecretStoreError("twitch_token_pair_invalid") from None
