"""Optional GitHub device authorization; secrets stay in current-user DPAPI storage."""
import ctypes
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError


@dataclass(frozen=True, slots=True)
class DeviceAuthorization:
    user_code: str
    verification_uri: str
    expires_at: str
    interval: int


@dataclass(frozen=True, slots=True)
class AccountStatus:
    state: str
    login: str | None
    reason: str | None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise ValueError("GitHub 授权端点不允许跳转到其他地址")


def _dpapi(data, decrypt=False):
    if os.name != 'nt':
        raise OSError("GitHub 凭证保护需要 Windows")
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size",wintypes.DWORD),("data",ctypes.POINTER(ctypes.c_ubyte))]
    buffer=ctypes.create_string_buffer(data)
    incoming=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    outgoing=Blob()
    dll=ctypes.WinDLL("crypt32",use_last_error=True)
    func=dll.CryptUnprotectData if decrypt else dll.CryptProtectData
    func.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,
                   ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    func.restype=wintypes.BOOL
    if not func(ctypes.byref(incoming),None,None,None,None,1,ctypes.byref(outgoing)):
        raise OSError("无法保护或读取当前 Windows 用户的凭证")
    try:
        return ctypes.string_at(outgoing.data,outgoing.size)
    finally:
        free=ctypes.WinDLL("kernel32",use_last_error=True).LocalFree
        free.argtypes=[ctypes.c_void_p];free.restype=ctypes.c_void_p
        free(outgoing.data)


class GitHubAccount:
    def __init__(self,data_dir,client_id=None,opener=None,clock=time.time,
                 protect=None,unprotect=None):
        self.path=Path(data_dir)/"github-credential.bin"
        self.client_id=client_id
        self.opener=opener or build_opener(NoRedirect()).open
        self.clock=clock
        self.protect=protect or _dpapi
        self.unprotect=unprotect or (lambda data:_dpapi(data,True))
        self._token=None;self._login=None;self._device=None;self._public=None
        self._token_expires=0;self._refresh_token=None;self._refresh_expires=0;self._refresh_retry_at=0
        self._expires=0;self._next_poll=0;self._interval=5
        self._lock=threading.RLock()
        self._status=AccountStatus("disconnected",None,None) if client_id else AccountStatus(
            "unavailable",None,"软件尚未配置 GitHub 授权应用；未连接也能使用")
        if self.path.exists():
            try:
                payload=json.loads(self.unprotect(self.path.read_bytes()))
                if payload.get('version') not in (1,2) or not isinstance(payload.get('token'),str) or not payload['token']:
                    raise ValueError("credential")
                self._token,self._login=payload['token'],payload.get('login')
                for name in ('expires_at','refresh_expires_at'):
                    value=payload.get(name)
                    if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not 0<value<1e12):
                        raise ValueError('expiry')
                refresh=payload.get('refresh_token')
                if refresh is not None and (not isinstance(refresh,str) or not refresh):raise ValueError('refresh')
                self._token_expires=payload.get('expires_at') or 0
                self._refresh_token=refresh;self._refresh_expires=payload.get('refresh_expires_at') or 0
                self._status=AccountStatus("connected",self._login,None)
            except (OSError,ValueError,TypeError):
                self._token=None;self._refresh_token=None
                self._status=AccountStatus("reconnect",None,"保存的 GitHub 凭证无法读取，请重新连接")

    def status(self):
        with self._lock:return self._status

    def access_token(self):
        with self._lock:
            now=self.clock()
            if self._refresh_token and (not self._token or (self._token_expires and now>=self._token_expires-60)):
                self._renew()
            elif self._token and self._token_expires and now>=self._token_expires:
                self._reconnect()
            return self._token if not self._token_expires or now<self._token_expires else None

    def _reconnect(self):
        # Keep the encrypted file intact; only explicit disconnect removes it.
        self._token=None;self._refresh_token=None
        self._status=AccountStatus('reconnect',self._login,'GitHub 授权已失效，请重新登录；暂用公开请求更新。')

    def reject_token(self, token):
        with self._lock:
            if token!=self._token:return
            self._token=None
            if self._refresh_token:self._renew(force=True)
            else:self._reconnect()

    def _record(self,result,login):
        token,scope=result.get('access_token'),result.get('scope','')
        if not isinstance(token,str) or not token or not isinstance(scope,str) or scope.strip():
            raise ValueError('授权包含不必要权限或凭证无效')
        refresh=result.get('refresh_token')
        if refresh is not None and (not isinstance(refresh,str) or not refresh):raise ValueError('refresh')
        record={'version':2,'token':token,'login':login,'refresh_token':refresh}
        for key,field in (('expires_in','expires_at'),('refresh_token_expires_in','refresh_expires_at')):
            value=result.get(key)
            if value is not None and (isinstance(value,bool) or not isinstance(value,int) or not 0<value<=366*86400):raise ValueError('expiry')
            record[field]=self.clock()+value if value is not None else None
        return record

    def _save(self,record):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix('.tmp')
        temporary.write_bytes(self.protect(json.dumps(record).encode()))
        os.replace(temporary,self.path)
        self._token,self._login=record['token'],record['login']
        self._token_expires=record.get('expires_at') or 0
        self._refresh_token=record.get('refresh_token');self._refresh_expires=record.get('refresh_expires_at') or 0
        self._refresh_retry_at=0;self._status=AccountStatus('connected',self._login,None)

    def _renew(self,force=False):
        now=self.clock()
        if not self.client_id or not self._refresh_token or (self._refresh_expires and now>=self._refresh_expires):
            self._reconnect();return
        if not force and now<self._refresh_retry_at:return
        self._refresh_retry_at=now+60
        try:
            result=self._request('https://github.com/login/oauth/access_token',{
                'client_id':self.client_id,'grant_type':'refresh_token','refresh_token':self._refresh_token})
            if result.get('error'):
                self._reconnect();return
            record=self._record(result,self._login)
            # A refresh continues the already verified user's grant and rotates
            # both tokens. Persist it before any further network dependency.
            self._save(record)
        except (ValueError,OSError):
            self._status=AccountStatus('error',self._login,'GitHub 授权续期暂未完成，将稍后重试；暂用公开请求更新。')

    def _request(self,url,payload=None,token=None):
        headers={"Accept":"application/json","User-Agent":"GitHubRadar/0.2"}
        if token:headers['Authorization']='Bearer '+token
        data=urlencode(payload).encode() if payload is not None else None
        request=Request(url,data=data,headers=headers)
        try:
            with self.opener(request,timeout=20) as response:
                raw=response.read(65537)
            if len(raw)>65536:raise ValueError("response size")
            value=json.loads(raw)
            if not isinstance(value,dict):raise ValueError("response")
            return value
        except (HTTPError,URLError,OSError,ValueError) as exc:
            raise ValueError("GitHub 账号连接失败，请稍后重试") from exc

    def begin(self):
        with self._lock:
            if not self.client_id:
                raise ValueError("软件尚未配置 GitHub 授权应用；未连接也能使用")
            result=self._request("https://github.com/login/device/code",{"client_id":self.client_id,"scope":""})
            if (result.get('verification_uri')!='https://github.com/login/device'
                    or not re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}",result.get('user_code',''))
                    or not isinstance(result.get('device_code'),str)
                    or not isinstance(result.get('expires_in'),int) or isinstance(result['expires_in'],bool)
                    or not 1<=result['expires_in']<=3600):
                raise ValueError("GitHub 授权响应无效")
            interval=result.get('interval',5)
            if isinstance(interval,bool) or not isinstance(interval,int) or not 1<=interval<=60:
                raise ValueError("GitHub 授权间隔无效")
            self._interval=interval;self._expires=self.clock()+result['expires_in']
            self._device=result['device_code'];self._next_poll=self.clock()+interval
            self._public=DeviceAuthorization(result['user_code'],result['verification_uri'],
                datetime.fromtimestamp(self._expires,timezone.utc).isoformat(),interval)
            self._status=AccountStatus("pending",None,"请在 GitHub 官方页面登录并授权")
            return self._public

    def poll(self):
        with self._lock:
            if not self._device:return self._status
            now=self.clock()
            if now>=self._expires:
                self._device=None;self._status=AccountStatus("expired",None,"GitHub 授权已过期，请重新连接")
                return self._status
            if now<self._next_poll:return self._status
            self._next_poll=now+self._interval
            try:
                result=self._request("https://github.com/login/oauth/access_token",{
                    'client_id':self.client_id,'device_code':self._device,
                    'grant_type':'urn:ietf:params:oauth:grant-type:device_code'})
                error=result.get('error')
                if error=='authorization_pending':return self._status
                if error=='slow_down':
                    self._interval+=5;self._next_poll=now+self._interval;return self._status
                if error:
                    self._device=None
                    self._status=AccountStatus("expired" if error=='expired_token' else "error",None,
                                              "GitHub 授权未完成，请重新连接")
                    return self._status
                token=result.get('access_token')
                if not isinstance(token,str) or not token or result.get('scope','').strip():
                    raise ValueError("授权包含不必要权限或凭证无效")
                user=self._request('https://api.github.com/user',token=token)
                login=user.get('login')
                if not isinstance(login,str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*",login):
                    raise ValueError("GitHub 账号身份无效")
                self._save(self._record(result,login));self._device=None
            except (ValueError,OSError):
                self._status=AccountStatus('error',None,'GitHub 账号连接未完成，请稍后重试')
            return self._status

    def cancel(self):
        with self._lock:
            self._device=None;self._public=None
            self._status=AccountStatus('connected',self._login,None) if self._token else AccountStatus('disconnected',None,None)

    def disconnect(self):
        with self._lock:
            # Only this software's credential is removed; GitHub website grants are unchanged.
            self.path.unlink(missing_ok=True)
            self._token,self._login=None,None
            self._refresh_token=None;self._token_expires=0;self._refresh_expires=0
            self.cancel()
