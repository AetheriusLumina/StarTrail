import io,json,tempfile,unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError,URLError
from unittest.mock import patch
from github_radar.github_account import GitHubAccount
from github_radar.github_client import GitHubClient,GitHubRequestError,GitHubRateLimitError
from github_radar.discovery_types import RequestBudget

def response(value,headers=None):
    r=io.BytesIO(json.dumps(value).encode());r.headers=headers or {};return r

class AuthRecoveryTests(unittest.TestCase):
    def account(self,path,payload,clock,opener):
        (path/'github-credential.bin').write_bytes(json.dumps(payload).encode()[::-1])
        return GitHubAccount(path,client_id='public-client',clock=clock,opener=opener,
                             protect=lambda b:b[::-1],unprotect=lambda b:b[::-1])

    def test_expiring_login_keeps_refresh_metadata_and_renews_after_restart(self):
        ticks=[100];requests=[]
        replies=[{'device_code':'device','user_code':'ABCD-EFGH','verification_uri':'https://github.com/login/device','expires_in':300,'interval':5},
                 {'access_token':'old','scope':'','expires_in':120,'refresh_token':'refresh-old','refresh_token_expires_in':10000},
                 {'login':'testuser'},
                 {'access_token':'new','scope':'','expires_in':28800,'refresh_token':'refresh-new','refresh_token_expires_in':10000}]
        def opener(req,timeout):requests.append(req);return response(replies.pop(0))
        with tempfile.TemporaryDirectory() as d:
            a=GitHubAccount(Path(d),client_id='public-client',clock=lambda:ticks[0],opener=opener,
                            protect=lambda b:b[::-1],unprotect=lambda b:b[::-1])
            a.begin();ticks[0]=105;self.assertEqual(a.poll().state,'connected')
            packed=json.loads(a.path.read_bytes()[::-1]);self.assertEqual(packed['refresh_token'],'refresh-old')
            restored=GitHubAccount(Path(d),client_id='public-client',clock=lambda:ticks[0],opener=opener,
                                   protect=lambda b:b[::-1],unprotect=lambda b:b[::-1])
            ticks[0]=230;self.assertEqual(restored.access_token(),'new')
            self.assertIn(b'grant_type=refresh_token',requests[-1].data)
            self.assertNotIn(b'client_secret',requests[-1].data)
            self.assertEqual(json.loads(a.path.read_bytes()[::-1])['refresh_token'],'refresh-new')
            self.assertNotIn('refresh-new',str(restored.status()))

    def test_rejected_old_credential_marks_reconnect_and_preserves_file(self):
        with tempfile.TemporaryDirectory() as d:
            a=self.account(Path(d),{'version':1,'token':'old','login':'testuser'},lambda:100,None)
            before=a.path.read_bytes();a.reject_token('old')
            self.assertIsNone(a.access_token());self.assertEqual(a.status().state,'reconnect')
            self.assertEqual(a.path.read_bytes(),before)

    def test_late_rejection_does_not_invalidate_new_login(self):
        with tempfile.TemporaryDirectory() as d:
            a=self.account(Path(d),{'version':1,'token':'new','login':'testuser'},lambda:100,None)
            a.reject_token('old');self.assertEqual(a.access_token(),'new')

    def test_transient_renewal_failure_keeps_encrypted_credentials_and_throttles_retry(self):
        ticks=[230];calls=[]
        def fail(req,timeout):calls.append(req);raise URLError('offline')
        with tempfile.TemporaryDirectory() as d:
            a=self.account(Path(d),{'version':2,'token':'old','login':'testuser','expires_at':200,'refresh_token':'refresh','refresh_expires_at':10000},lambda:ticks[0],fail)
            before=a.path.read_bytes();self.assertIsNone(a.access_token());self.assertIsNone(a.access_token())
            self.assertEqual(len(calls),1);self.assertEqual(a.path.read_bytes(),before)
            ticks[0]=300;self.assertIsNone(a.access_token());self.assertEqual(len(calls),2)

    def test_invalid_refresh_does_not_repeat_or_expand_permissions(self):
        calls=[]
        def fail(req,timeout):calls.append(req);return response({'error':'bad_refresh_token'})
        with tempfile.TemporaryDirectory() as d:
            a=self.account(Path(d),{'version':2,'token':'old','login':'testuser','expires_at':200,'refresh_token':'refresh','refresh_expires_at':10000},lambda:230,fail)
            self.assertIsNone(a.access_token());self.assertIsNone(a.access_token());self.assertEqual(len(calls),1)
            self.assertEqual(a.status().state,'reconnect')

    def test_401_recovers_public_json_with_new_identity_budget(self):
        tokens=['old'];requests=[];rejected=[]
        def opener(req,timeout):
            requests.append(req)
            if req.get_header('Authorization'):raise HTTPError(req.full_url,401,'unauthorized',{},None)
            return response({'items':[]},{'x-ratelimit-resource':'search','x-ratelimit-remaining':'9'})
        def rejected_token(token):rejected.append(token);tokens[0]=None
        budget=RequestBudget(5000,30,1000)
        c=GitHubClient(opener=opener,token_provider=lambda:tokens[0],on_auth_failure=rejected_token,budget=budget,clock=lambda:0)
        self.assertEqual(c.search('ai'),[]);self.assertEqual(rejected,['old'])
        self.assertEqual(len(requests),2);self.assertIsNone(requests[-1].get_header('Authorization'))
        self.assertIsNone(budget.core_remaining);self.assertEqual(budget.search_remaining,9)
        c.search('ai');self.assertEqual(len(rejected),1)

    def test_401_recovery_also_applies_to_readme(self):
        requests=[]
        def opener(req,timeout):
            requests.append(req)
            if req.get_header('Authorization'):raise HTTPError(req.full_url,401,'unauthorized',{},None)
            r=io.BytesIO(b'readme');r.headers={};return r
        c=GitHubClient(opener=opener,token_provider=lambda:'old',on_auth_failure=lambda token:None)
        self.assertEqual(c.fetch_readme('a/repo').text,'readme')
        self.assertEqual(len(requests),2)

    def test_403_quota_is_not_treated_as_expired_auth(self):
        rejected=[]
        def opener(req,timeout):raise HTTPError(req.full_url,403,'quota',{'x-ratelimit-remaining':'0'},None)
        c=GitHubClient(opener=opener,token_provider=lambda:'valid',on_auth_failure=rejected.append)
        with self.assertRaises(GitHubRateLimitError):c.search('ai')
        self.assertFalse(rejected)

    def test_parallel_requests_share_one_rotating_refresh(self):
        calls=[]
        def opener(req,timeout):
            calls.append(req)
            return response({'access_token':'new','scope':'','expires_in':28800,'refresh_token':'next','refresh_token_expires_in':10000} if req.data else {'login':'testuser'})
        with tempfile.TemporaryDirectory() as d:
            a=self.account(Path(d),{'version':2,'token':'old','login':'testuser','expires_at':200,'refresh_token':'refresh','refresh_expires_at':10000},lambda:230,opener)
            with ThreadPoolExecutor(max_workers=8) as workers:
                self.assertEqual(list(workers.map(lambda _:a.access_token(),range(16))),['new']*16)
            self.assertEqual(len(calls),1)

    def test_expired_refresh_requires_login_without_network_loop(self):
        calls=[]
        with tempfile.TemporaryDirectory() as d:
            a=self.account(Path(d),{'version':2,'token':'old','login':'testuser','expires_at':200,'refresh_token':'refresh','refresh_expires_at':220},lambda:230,lambda *args,**kwargs:calls.append(args))
            self.assertIsNone(a.access_token());self.assertIsNone(a.access_token())
            self.assertEqual(a.status().state,'reconnect');self.assertFalse(calls)

    def test_rotated_pair_is_saved_without_extra_network_dependency(self):
        calls=[]
        def opener(req,timeout):
            calls.append(req)
            if not req.data:raise URLError('identity endpoint offline after rotation')
            return response({'access_token':'new','scope':'','expires_in':28800,'refresh_token':'next','refresh_token_expires_in':10000})
        with tempfile.TemporaryDirectory() as d:
            a=self.account(Path(d),{'version':2,'token':'old','login':'testuser','expires_at':200,'refresh_token':'refresh','refresh_expires_at':10000},lambda:230,opener)
            self.assertEqual(a.access_token(),'new')
            restored=GitHubAccount(Path(d),client_id='public-client',clock=lambda:230,opener=opener,protect=lambda b:b[::-1],unprotect=lambda b:b[::-1])
            self.assertEqual(restored.access_token(),'new');self.assertEqual(restored.status().login,'testuser')
            self.assertEqual(len(calls),1)

    def test_401_and_transient_refresh_failure_retains_retry(self):
        ticks=[230];calls=[]
        def opener(req,timeout):
            calls.append(req)
            if len(calls)==1:raise URLError('offline')
            return response({'access_token':'new','scope':'','expires_in':28800,'refresh_token':'next','refresh_token_expires_in':10000})
        with tempfile.TemporaryDirectory() as d:
            a=self.account(Path(d),{'version':2,'token':'old','login':'testuser','expires_at':1000,'refresh_token':'refresh','refresh_expires_at':10000},lambda:ticks[0],opener)
            before=a.path.read_bytes();a.reject_token('old')
            self.assertEqual(a.status().state,'error');self.assertIsNone(a.access_token());self.assertEqual(len(calls),1)
            self.assertEqual(a.path.read_bytes(),before)
            ticks[0]=300;self.assertEqual(a.access_token(),'new');self.assertEqual(len(calls),2)

    def test_failed_atomic_write_keeps_original_encrypted_file(self):
        def opener(req,timeout):
            return response({'access_token':'new','scope':'','expires_in':28800,'refresh_token':'next','refresh_token_expires_in':10000} if req.data else {'login':'testuser'})
        with tempfile.TemporaryDirectory() as d:
            a=self.account(Path(d),{'version':2,'token':'old','login':'testuser','expires_at':200,'refresh_token':'refresh','refresh_expires_at':10000},lambda:230,opener)
            before=a.path.read_bytes()
            with patch('github_radar.github_account.os.replace',side_effect=OSError('disk full')):
                self.assertIsNone(a.access_token())
            self.assertEqual(a.path.read_bytes(),before);self.assertEqual(a.status().state,'error')

    def test_renewal_rejects_extra_permissions(self):
        for extra_scope in ('repo','read:user'):
            with self.subTest(scope=extra_scope),tempfile.TemporaryDirectory() as d:
                def opener(req,timeout):
                    return response({'access_token':'new','scope':extra_scope,'expires_in':28800,'refresh_token':'next','refresh_token_expires_in':10000})
                a=self.account(Path(d),{'version':2,'token':'old','login':'testuser','expires_at':200,'refresh_token':'refresh','refresh_expires_at':10000},lambda:230,opener)
                before=a.path.read_bytes();self.assertIsNone(a.access_token())
                self.assertEqual(a.path.read_bytes(),before);self.assertNotEqual(a.status().state,'connected')
