import io,json,unittest
from urllib.request import Request
from urllib.error import HTTPError
from unittest.mock import patch
from github_radar.github_client import GitHubClient,GitHubRequestError
from tests.test_github_client import FakeResponse,REPO_PAYLOAD

class BatchMetadataTests(unittest.TestCase):
 def test_missing_alias_preserves_valid_records_for_stable_id_recovery(self):
  row={'databaseId':7,'nameWithOwner':'org/valid','url':'https://github.com/org/valid','description':'skills','stargazerCount':1000,'isArchived':False,'primaryLanguage':None,'repositoryTopics':{'nodes':[]}}
  body={'data':{'r0':row,'r1':None},'errors':[{'type':'NOT_FOUND','path':['r1'],'message':'Could not resolve repository'}]}
  client=GitHubClient(opener=lambda *a,**kw:FakeResponse(body),token_provider=lambda:'test-only')
  result=client.get_repositories_batch(('org/valid','org/missing'))
  self.assertEqual(list(result),['org/valid']);self.assertEqual(result['org/valid'].id,7)

 def test_unreturned_alias_without_explicit_null_is_not_complete(self):
  client=GitHubClient(opener=lambda *a,**kw:FakeResponse({'data':{}}),token_provider=lambda:'test-only')
  with self.assertRaises(GitHubRequestError):client.get_repositories_batch(('org/missing',))

 def test_twenty_official_repositories_use_one_query_and_cache_each_total(self):
  calls=[]
  def opener(request,timeout):
   calls.append(request);data={}
   for i in range(20):data['r'+str(i)]={'databaseId':i+1,'nameWithOwner':'org/repo'+str(i),'url':'https://github.com/org/repo'+str(i),'description':'skills','stargazerCount':1000+i,'isArchived':False,'primaryLanguage':{'name':'Python'},'repositoryTopics':{'nodes':[{'topic':{'name':'skills'}}]}}
   return FakeResponse({'data':data,'extensions':{}},{'x-ratelimit-resource':'graphql','x-ratelimit-remaining':'4999'})
  client=GitHubClient(opener=opener,token_provider=lambda:'test-placeholder')
  self.assertTrue(hasattr(client,'get_repositories_batch'),'batch metadata interface missing')
  result=client.get_repositories_batch(tuple('org/repo'+str(i) for i in range(20)))
  self.assertEqual(len(calls),1);self.assertEqual(len(result),20);self.assertEqual(result['org/repo19'].stars,1019)
  self.assertEqual(calls[0].get_method(),'POST');self.assertEqual(client.graphql_remaining,4999)
  self.assertIsNone(client.core_remaining)
 def test_graphql_partial_errors_are_not_silently_complete(self):
  client=GitHubClient(opener=lambda *a,**kw:FakeResponse({'data':{},'errors':[{'message':'limited'}]}),token_provider=lambda:'test-placeholder')
  self.assertTrue(hasattr(client,'get_repositories_batch'),'batch metadata interface missing')
  with self.assertRaises(GitHubRequestError):client.get_repositories_batch(('org/repo',))

class ConnectionReuseTests(unittest.TestCase):
 def pool(self,factory):
  from github_radar.github_transport import PooledHTTPSOpener
  return PooledHTTPSOpener(factory=factory)
 def test_connection_is_reused_when_response_is_fully_consumed(self):
  made=[]
  class Response(io.BytesIO):
   status=200;reason='OK';headers={};will_close=False
   def isclosed(self):return self.tell()==len(self.getvalue())
  class Connection:
   sock=None
   def __init__(self,*a,**kw):made.append(self)
   def request(self,*a,**kw):pass
   def getresponse(self):return Response(b'{}')
   def close(self):pass
  pool=self.pool(Connection)
  for _ in range(3):
   with pool(Request('https://api.github.com/repos/a/b'),timeout=2) as response:self.assertEqual(response.read(),b'{}')
  self.assertEqual(len(made),1);pool.close()
 def test_unconsumed_response_closes_connection_before_next_request(self):
  made=[]
  class Response(io.BytesIO):
   status=200;reason='OK';headers={};will_close=False
   def isclosed(self):return self.tell()==len(self.getvalue())
  class Connection:
   sock=None
   def __init__(self,*a,**kw):made.append(self);self.closed=False
   def request(self,*a,**kw):pass
   def getresponse(self):return Response(b'{}')
   def close(self):self.closed=True
  pool=self.pool(Connection)
  with pool(Request('https://api.github.com/repos/a/b'),timeout=2):pass
  self.assertTrue(made[0].closed)
  with pool(Request('https://api.github.com/repos/a/b'),timeout=2) as response:response.read()
  self.assertEqual(len(made),2);pool.close()
 def test_cross_origin_redirect_never_sends_credentials(self):
  class Response(io.BytesIO):
   status=302;reason='Redirect';headers={'Location':'https://example.com/steal'};will_close=False
   def isclosed(self):return True
  class Connection:
   sock=None
   def __init__(self,*a,**kw):pass
   def request(self,*a,**kw):pass
   def getresponse(self):return Response()
   def close(self):pass
  pool=self.pool(Connection)
  from github_radar.public_http import SourceRequestError
  with self.assertRaises(SourceRequestError):pool(Request('https://api.github.com/repos/a/b',headers={'Authorization':'Bearer test-placeholder'}),timeout=2)
  pool.close()


class PoolBoundTests(unittest.TestCase):
 def test_four_network_requests_overlap_but_fifth_waits_for_release(self):
  import threading
  from github_radar.github_transport import PooledHTTPSOpener
  from concurrent.futures import ThreadPoolExecutor
  entered=threading.Event();release=threading.Event();lock=threading.Lock();state={'count':0,'peak':0}
  class Response(io.BytesIO):
   status=200;reason='OK';headers={};will_close=False
   def isclosed(self):return self.tell()==len(self.getvalue())
  class Connection:
   sock=None
   def __init__(self,*a,**k):pass
   def request(self,*a,**k):
    with lock:
     state['count']+=1;state['peak']=max(state['peak'],state['count'])
     if state['count']==4:entered.set()
    release.wait(3)
   def getresponse(self):
    with lock:state['count']-=1
    return Response(b'{}')
   def close(self):pass
  pool=PooledHTTPSOpener(factory=Connection)
  def fetch():
   with pool(Request('https://api.github.com/repos/a/b'),timeout=4) as r:return r.read()
  try:
   with ThreadPoolExecutor(max_workers=5) as executor:
    tasks=[executor.submit(fetch) for _ in range(5)]
    try:self.assertTrue(entered.wait(1),'four official connections must overlap');self.assertEqual(state['peak'],4)
    finally:release.set()
    self.assertEqual([t.result() for t in tasks],[b'{}']*5)
   self.assertEqual(state['peak'],4)
  finally:release.set();pool.close()

class ConfigurableConcurrencyTests(unittest.TestCase):
 def test_client_passes_requested_connection_limit_to_official_pool(self):
  with patch('github_radar.github_client.getproxies',return_value={}), patch('github_radar.github_transport.PooledHTTPSOpener') as pool:
   client=GitHubClient(request_concurrency=100)
   self.assertEqual(client.request_concurrency,100)
   pool.assert_called_once_with(max_connections=100)
   client.close()
 def test_reject_invalid_or_over_limit_concurrency(self):
  from github_radar.github_transport import PooledHTTPSOpener
  for value in (0,101,True,1.5):
   with self.subTest(value=value), self.assertRaises(ValueError):PooledHTTPSOpener(max_connections=value)
   with self.subTest(client=value), self.assertRaises(ValueError):GitHubClient(request_concurrency=value)
 def test_pool_has_one_shared_hundred_request_bound(self):
  from github_radar.github_transport import PooledHTTPSOpener
  pool=PooledHTTPSOpener(max_connections=100)
  try:
   for _ in range(100):self.assertTrue(pool.slots.acquire(blocking=False))
   self.assertFalse(pool.slots.acquire(blocking=False))
   for _ in range(100):pool.slots.release()
  finally:pool.close()

class RateDowngradeTests(unittest.TestCase):
 def test_secondary_limit_preserves_status_retry_time_and_halves_concurrency(self):
  from github_radar.github_client import GitHubRateLimitError
  def opener(request,timeout):raise HTTPError(request.full_url,403,'limited',{'Retry-After':'60','x-ratelimit-remaining':'4999'},io.BytesIO(b'{"message":"secondary rate limit"}'))
  client=GitHubClient(opener=opener,request_concurrency=100)
  with patch('github_radar.github_client.time.time',return_value=1000), self.assertRaises(GitHubRateLimitError) as caught:client.get_repository('a/b')
  self.assertEqual(caught.exception.status,403);self.assertEqual(caught.exception.reset_at,1060)
  self.assertEqual(client.request_concurrency,50)
 def test_permission_403_does_not_reduce_concurrency(self):
  def opener(request,timeout):raise HTTPError(request.full_url,403,'forbidden',{'x-ratelimit-remaining':'4999'},io.BytesIO(b'{"message":"Resource not accessible by integration"}'))
  client=GitHubClient(opener=opener,request_concurrency=100)
  with self.assertRaises(GitHubRequestError) as caught:client.get_repository('a/b')
  self.assertEqual(caught.exception.status,403);self.assertEqual(client.request_concurrency,100)

class RateBurstTests(unittest.TestCase):
 def test_concurrent_secondary_burst_updates_cooldown_atomically(self):
  from threading import Barrier,RLock
  from concurrent.futures import ThreadPoolExecutor
  gate=Barrier(20)
  class CountingLock:
   def __init__(self):self.lock=RLock();self.entries=0
   def __enter__(self):self.lock.acquire();self.entries+=1
   def __exit__(self,*args):self.lock.release()
  client=GitHubClient(opener=lambda *a,**kw:None,request_concurrency=100)
  lock=CountingLock();client._quota_lock=lock
  def fail(_):
   gate.wait()
   return client._rate_error(HTTPError('https://api.github.com',429,'limited',{'Retry-After':'60','x-ratelimit-remaining':'4999'},io.BytesIO()),4999,2000,'/repos/a/b')
  with patch('github_radar.github_client.time.time',return_value=1000):
   with ThreadPoolExecutor(max_workers=20) as executor:list(executor.map(fail,range(20)))
  self.assertEqual(client.request_concurrency,50)
  self.assertEqual(client.retry_not_before,1060)
  self.assertEqual(lock.entries,20)
 def test_one_secondary_burst_reduces_concurrency_once(self):
  client=GitHubClient(opener=lambda *a,**kw:None,request_concurrency=100)
  with patch('github_radar.github_client.time.time',return_value=1000):
   for _ in range(20):
    client._rate_error(HTTPError('https://api.github.com',429,'limited',{'Retry-After':'60','x-ratelimit-remaining':'4999'},io.BytesIO()),4999,2000,'/repos/a/b')
  self.assertEqual(client.request_concurrency,50)
 def test_hourly_quota_exhaustion_does_not_lower_connection_concurrency(self):
  client=GitHubClient(opener=lambda *a,**kw:None,request_concurrency=100)
  with patch('github_radar.github_client.time.time',return_value=1000):
   client._rate_error(HTTPError('https://api.github.com',403,'limited',{'x-ratelimit-remaining':'0'},io.BytesIO()),0,2000,'/repos/a/b')
  self.assertEqual(client.request_concurrency,100);self.assertEqual(client.retry_not_before,2000)

class SharedRateCooldownTests(unittest.TestCase):
 def test_other_official_requests_wait_for_shared_retry_after(self):
  from github_radar.github_client import GitHubRateLimitError
  calls=[]
  def opener(request,timeout):
   calls.append(request.full_url)
   if len(calls)==1:raise HTTPError(request.full_url,429,'limited',{'Retry-After':'60','x-ratelimit-remaining':'4999'},io.BytesIO())
   return FakeResponse(REPO_PAYLOAD)
  client=GitHubClient(opener=opener,request_concurrency=100)
  with patch('github_radar.github_client.time.time',return_value=1000):
   with self.assertRaises(GitHubRateLimitError):client.get_repository('a/b')
   with self.assertRaises(GitHubRateLimitError):client.get_repository('c/d')
   self.assertEqual(len(calls),1)
  with patch('github_radar.github_client.time.time',return_value=1061):self.assertEqual(client.get_repository('c/d').id,7)
  self.assertEqual(len(calls),2)
