"""Bounded reusable HTTPS connections, restricted to the official API."""
import io,queue,threading,time
from http.client import HTTPSConnection
from urllib.error import HTTPError
from urllib.parse import urlsplit,urljoin
from .public_http import SourceRequestError

class _Response:
    def __init__(self,pool,connection,response):self.pool=pool;self.connection=connection;self.response=response;self.headers=response.headers;self.closed=False
    def read(self,*a):return self.response.read(*a)
    def __enter__(self):return self
    def __exit__(self,*a):self.close()
    def close(self):
        if self.closed:return
        self.closed=True
        reusable=self.response.isclosed() and not self.response.will_close
        self.response.close();self.pool.release(self.connection,reusable)

class PooledHTTPSOpener:
    def __init__(self,*,factory=HTTPSConnection,max_connections=4):
        if type(max_connections) is not int or not 1<=max_connections<=100:raise ValueError("GitHub concurrency must be between 1 and 100")
        self.factory=factory;self.idle=queue.LifoQueue(max_connections);self.slots=threading.BoundedSemaphore(max_connections);self.closed=False;self.lock=threading.Lock()
    def release(self,connection,reusable):
        with self.lock:
            if reusable and not self.closed:self.idle.put_nowait((connection,time.monotonic()))
            else:connection.close()
        self.slots.release()
    def __call__(self,request,timeout=20):
        at=time.monotonic();url=request.full_url;method=request.get_method()
        if timeout<=0:raise TimeoutError('GitHub request deadline reached')
        target=urlsplit(url)
        if target.scheme!='https' or target.netloc!='api.github.com' or target.username or target.password or target.fragment or method not in ('GET','HEAD','POST') or (method=='POST' and target.path!='/graphql'):
            raise SourceRequestError('GitHub连接超出允许的官方接口')
        if not self.slots.acquire(timeout=timeout):raise TimeoutError('GitHub connection pool timed out')
        connection=None;leased=True
        try:
            with self.lock:
                if self.closed:raise OSError('GitHub client closed')
                while connection is None:
                    try:
                        available,released_at=self.idle.get_nowait()
                        if time.monotonic()-released_at>=30:
                            available.close();continue
                        connection=available
                    except queue.Empty:connection=self.factory('api.github.com',timeout=timeout)
            for redirects in range(4):
                remaining=timeout-(time.monotonic()-at)
                if remaining<=0:raise TimeoutError('GitHub request deadline reached')
                connection.timeout=remaining
                if connection.sock:connection.sock.settimeout(remaining)
                target=urlsplit(url);path=target.path+('?' + target.query if target.query else '')
                connection.request(method,path,body=request.data,headers=dict(request.header_items()))
                response=connection.getresponse()
                if response.status in (301,302,303,307,308):
                    destination=urljoin(url,response.headers.get('Location',''))
                    new=urlsplit(destination)
                    # Never forward the authorization header to another origin.
                    if (new.scheme,new.netloc)!=('https','api.github.com') or new.path.startswith(('/login','/signup','/apikeys')) or method=='POST' or destination==url:
                        response.close();raise SourceRequestError('GitHub重定向离开允许的公开接口')
                    response.read(65537);response.close();connection.close();url=destination
                    continue
                wrapped=_Response(self,connection,response);leased=False
                if response.status>=400 or response.status==304:
                    status,reason,headers=response.status,response.reason,response.headers
                    try:error_body=wrapped.read(65537)
                    finally:wrapped.close()
                    raise HTTPError(url,status,reason,headers,io.BytesIO(error_body))
                return wrapped
            raise SourceRequestError('GitHub重定向次数过多')
        except BaseException:
            if leased:
                if connection:connection.close()
                self.slots.release()
            raise
    def close(self):
        with self.lock:
            self.closed=True
            while not self.idle.empty():self.idle.get_nowait()[0].close()
