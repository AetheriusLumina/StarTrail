"""Anonymous, fixed-repository release checks and verified full-package updates."""
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass,asdict
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.parse import urlsplit

REPOSITORY='AetheriusLumina/StarTrail'
RELEASE_TAG='v0.5.0-preview.2'
RELEASE_CHANNEL='preview'
CHECK_INTERVAL=6*60*60
API='https://api.github.com/repos/'+REPOSITORY+'/releases?per_page=100'
DOWNLOAD='https://github.com/'+REPOSITORY+'/releases/download/'

class SoftwareUpdateError(ValueError):pass

def version(value):
    match=re.fullmatch(r'v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-(alpha|beta|preview|rc)\.(0|[1-9][0-9]*))?',value or '')
    if not match or len(value)>80:raise SoftwareUpdateError('软件版本格式无效')
    major,minor,patch,kind,number=match.groups()
    return (int(major),int(minor),int(patch),{'alpha':0,'beta':1,'preview':2,'rc':3,None:4}[kind],int(number or 0))

def newer_version(candidate,current):return version(candidate)>version(current)

@dataclass(frozen=True,slots=True)
class SoftwareRelease:
    tag:str
    url:str
    notes:str
    installer_url:str
    sha256:str
    size:int
    prerelease:bool

def _url(value,expected=None,*,download=False):
    if not isinstance(value,str):raise SoftwareUpdateError('下载地址无效')
    parsed=urlsplit(value)
    hosts=('github.com','release-assets.githubusercontent.com','objects.githubusercontent.com') if download else ('api.github.com','github.com')
    if parsed.scheme!='https' or parsed.hostname not in hosts or parsed.port not in (None,443) or parsed.username or parsed.password:
        raise SoftwareUpdateError('更新来源不是受支持的官方下载站点')
    if expected is not None and value!=expected:raise SoftwareUpdateError('安装包不是指定仓库的发布附件')
    return value

def _request(url):return Request(url,headers={'User-Agent':'StarTrail Software Update','Accept':'application/vnd.github+json' if url.startswith('https://api.github.com/') else 'application/octet-stream'})

def _read(opener,url,limit):
    _url(url)
    try:
        with opener(_request(url),timeout=15) as response:
            _url(response.geturl(),download=not url.startswith('https://api.github.com/'))
            content=response.read(limit+1)
            if len(content)>limit:raise SoftwareUpdateError('更新信息超过安全读取限制')
            return content
    except SoftwareUpdateError:raise
    except Exception as exc:raise SoftwareUpdateError('无法检查软件更新，请检查网络后重试') from exc

class ReleaseClient:
    def __init__(self,opener=urlopen):self.opener=opener
    def check(self,current=RELEASE_TAG,channel=RELEASE_CHANNEL):
        version(current)
        if channel not in ('stable','preview'):raise SoftwareUpdateError('更新渠道无效')
        try:releases=json.loads(_read(self.opener,API,512*1024))
        except (UnicodeError,ValueError) as exc:raise SoftwareUpdateError('软件发布信息格式有误') from exc
        if not isinstance(releases,list) or len(releases)>100:raise SoftwareUpdateError('软件发布列表格式有误')
        candidates=[]
        for release in releases:
            if not isinstance(release,dict) or release.get('draft') is not False:continue
            if channel=='stable' and release.get('prerelease') is not False:continue
            tag=release.get('tag_name')
            try:
                if not newer_version(tag,current):continue
            except SoftwareUpdateError:continue
            assets=release.get('assets',[])
            if not isinstance(assets,list):continue
            installers=[a for a in assets if isinstance(a,dict) and a.get('name')=='StarTrail_Setup.exe']
            hashes=[a for a in assets if isinstance(a,dict) and a.get('name')=='SHA256SUMS.txt']
            if len(installers)!=1 or len(hashes)!=1:continue
            candidates.append((version(tag),release,installers[0],hashes[0]))
        if not candidates:return None
        _,release,installer,hash_asset=max(candidates,key=lambda item:item[0]);tag=release['tag_name']
        url=_url(installer.get('browser_download_url'),DOWNLOAD+tag+'/StarTrail_Setup.exe')
        hash_url=_url(hash_asset.get('browser_download_url'),DOWNLOAD+tag+'/SHA256SUMS.txt')
        size=installer.get('size')
        if type(size) is not int or not 1<=size<=1024*1024*1024:raise SoftwareUpdateError('安装包大小无效')
        try:sums=_read(self.opener,hash_url,65536).decode('utf-8')
        except UnicodeError as exc:raise SoftwareUpdateError('安装包校验值格式有误') from exc
        matches=re.findall(r'^([0-9a-fA-F]{64})[ \t]+\*?StarTrail_Setup\.exe[ \t]*$',sums.replace('\r\n','\n'),re.M)
        if len(matches)!=1:raise SoftwareUpdateError('发布附件缺少唯一有效的安装包SHA256')
        digest=matches[0].lower();official=installer.get('digest')
        if official and official!='sha256:'+digest:raise SoftwareUpdateError('发布附件的两份校验值不一致')
        page=_url(release.get('html_url'),'https://github.com/'+REPOSITORY+'/releases/tag/'+tag)
        notes=release.get('body') or ''
        if not isinstance(notes,str):raise SoftwareUpdateError('更新说明格式有误')
        return SoftwareRelease(tag,page,notes[:8000],url,digest,size,bool(release.get('prerelease')))

def _hash(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        while chunk:=stream.read(1024*1024):digest.update(chunk)
    return digest.hexdigest()

def download_installer(release,root,*,opener=urlopen,cancel_event=None,on_progress=None):
    version(release.tag);_url(release.installer_url,DOWNLOAD+release.tag+'/StarTrail_Setup.exe')
    if not re.fullmatch('[0-9a-f]{64}',release.sha256):raise SoftwareUpdateError('校验值无效')
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    target=root/(release.tag+'-StarTrail_Setup.exe');partial=target.with_suffix('.part')
    if target.is_file() and target.stat().st_size==release.size and _hash(target)==release.sha256:return target
    started=time.monotonic();count=0;digest=hashlib.sha256()
    try:
        with opener(_request(release.installer_url),timeout=30) as response,partial.open('wb') as stream:
            _url(response.geturl(),download=True)
            while chunk:=response.read(1024*1024):
                if cancel_event and cancel_event.is_set():raise SoftwareUpdateError('软件下载已取消')
                if time.monotonic()-started>600:raise SoftwareUpdateError('安装包下载超过10分钟，请检查网络后重试')
                count+=len(chunk)
                if count>release.size:raise SoftwareUpdateError('安装包大小与发布信息不一致')
                stream.write(chunk);digest.update(chunk)
                if on_progress:on_progress(count,release.size)
        if count!=release.size or digest.hexdigest()!=release.sha256:raise SoftwareUpdateError('安装包校验失败，当前软件和数据保持完整')
        os.replace(partial,target);return target
    except SoftwareUpdateError:raise
    except Exception as exc:raise SoftwareUpdateError('安装包下载失败，请检查网络和可用磁盘空间') from exc
    finally:partial.unlink(missing_ok=True)

class SoftwareUpdater:
    def __init__(self,data_dir,*,client=None,current=RELEASE_TAG,channel=RELEASE_CHANNEL,clock=time.monotonic):
        self.data_dir=Path(data_dir);self.client=client or ReleaseClient();self.current=current;self.channel=channel
        self.clock=clock;self.last_check=None;self.release=None
        self._lock=threading.Lock();self._cancel=threading.Event();self._worker=None
        self._state={'status':'idle','current':current,'channel':channel,'message':'','downloaded':0,'total':0}

    def state(self):
        with self._lock:
            return {**self._state,'release':asdict(self.release) if self.release else None,'installable':bool(getattr(sys,'frozen',False))}

    def _set(self,**changes):
        with self._lock:self._state.update(changes)

    def check(self,*,force=False):
        with self._lock:
            if self._worker and self._worker.is_alive():return self.state_unlocked()
            if not force and self.last_check is not None and self.clock()-self.last_check<CHECK_INTERVAL:return self.state_unlocked()
            self._state.update(status='checking',message='正在检查软件版本')
            self._worker=threading.Thread(target=self._check,daemon=True);self._worker.start()
            return self.state_unlocked()

    def state_unlocked(self):return {**self._state,'release':asdict(self.release) if self.release else None,'installable':bool(getattr(sys,'frozen',False))}

    def _check(self):
        try:
            release=self.client.check(self.current,self.channel)
            with self._lock:self.release=release;self.last_check=self.clock()
            self._set(status='available' if release else 'up_to_date',message='发现新软件版本' if release else '当前已是最新可用软件版本')
        except Exception as exc:
            self.last_check=self.clock();self._set(status='error',message=str(exc)[:300])

    def start(self,on_ready):
        with self._lock:
            if self._worker and self._worker.is_alive():return self.state_unlocked()
            if not self.release:raise SoftwareUpdateError('尚未检测到新安装包')
            if not getattr(sys,'frozen',False):raise SoftwareUpdateError('源码运行请从发布页下载并安装；不会覆盖源码目录')
            self._cancel=threading.Event();release=self.release
            self._state.update(status='downloading',message='下载并校验安装包，之后打开升级程序')
            def download():
                try:
                    target=download_installer(release,self.data_dir/'.software-updates',cancel_event=self._cancel,
                        on_progress=lambda n,total:self._set(downloaded=n,total=total))
                    if self._cancel.is_set():return
                    self._set(status='ready',message='校验通过，准备打开升级程序')
                    on_ready(target,release)
                except Exception as exc:self._set(status='error',message=str(exc)[:300])
            self._worker=threading.Thread(target=download,daemon=True);self._worker.start()
            return self.state_unlocked()

    def close(self):self._cancel.set()


def launch_installer(target, release, data_dir):
    """Recheck cached bytes immediately before executing this install's updater."""
    if not getattr(sys,'frozen',False):raise SoftwareUpdateError('源码运行请手动下载安装包')
    root=Path(sys.executable).resolve().parent
    data=Path(data_dir).resolve();target=Path(target).resolve()
    if data!=root/'UserData' or not (data/'.github-radar-data').is_file():
        raise SoftwareUpdateError('当前数据目录与安装位置不一致，请手动安装更新')
    version(release.tag)
    expected=data/'.software-updates'/(release.tag+'-StarTrail_Setup.exe')
    if target!=expected or not target.is_file() or target.stat().st_size!=release.size or _hash(target)!=release.sha256:
        raise SoftwareUpdateError('安装包启动前校验失败，软件继续运行')
    _url(release.installer_url,DOWNLOAD+release.tag+'/StarTrail_Setup.exe')
    try:
        # Inno owns the same maintenance lock as the running app. It backs up
        # UserData only after graceful shutdown, before replacing application files.
        return subprocess.Popen([str(target),'/DIR='+str(root)],cwd=str(root),shell=False)
    except OSError as exc:raise SoftwareUpdateError('无法打开升级程序，软件继续运行') from exc
