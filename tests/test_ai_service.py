import tempfile
import threading
import unittest

from github_radar.ai_service import AIService
from github_radar.ai_types import (AIProgress, InsightText, ProjectExplanation,
                                   RelevanceVerdict)
from github_radar.github_client import GitHubRequestError
from github_radar.models import KeywordRule, Recommendation, Repository
from github_radar.storage import RadarStore


DAY = "2026-09-27"


def repo(repo_id, stars=None, archived=False):
    return Repository(repo_id, f"owner/repo-{repo_id}",
                      f"https://github.com/owner/repo-{repo_id}", "AI tools",
                      ("ai",), "Python", stars if stars is not None else 3000 - repo_id,
                      archived)


def pick(repo_id, section, keyword_id=None, day=DAY):
    return Recommendation(repo_id, day, section, keyword_id, None, None,
                          f"{day}T12:00:00Z")


class FakeStore:
    def __init__(self):
        self.keywords = [KeywordRule(10, "local ai", 1000, True),
                         KeywordRule(20, "agents", 1000, True)]
        self.picks = [pick(1, "growth"), pick(2, "keyword", 10),
                      pick(3, "keyword", 20)]
        self.repositories = {number: repo(number) for number in range(1, 40)}
        self.checked = {}
        self.progress = AIProgress(1, 0, 0)

    def list_keywords(self):
        return self.keywords

    def daily_recommendations(self, day):
        return self.picks

    def repositories_for_ids(self, ids):
        return {number: self.repositories[number] for number in ids}

    def seen_repo_ids(self):
        return {1, 2, 3, 4}

    def ai_progress(self, day, keyword_id, model_id):
        return self.progress

    def ai_verdicts(self, day, keyword_id, model_id):
        return self.checked


class FakeClient:
    def __init__(self, hits):
        self.hits = hits
        self.search_calls = []
        self.readme_failure = False

    def search(self, query, page=1, per_page=100, sort="stars"):
        self.search_calls.append((query, page, per_page, sort))
        return self.hits[(page - 1) * per_page:page * per_page]

    def get_repository_by_id(self, repo_id):
        return repo(repo_id)

    def fetch_readme_by_id(self, repo_id, etag=None):
        from github_radar.readme_types import ReadmeFetch
        if self.readme_failure:
            raise GitHubRequestError('README unavailable')
        return ReadmeFetch('README evidence', None, False, False,
                           f'https://github.com/owner/repo-{repo_id}/blob/main/README.md')

    def readme_excerpt(self, full_name, max_chars=2400):
        if self.readme_failure:
            raise GitHubRequestError("README unavailable")
        return "README evidence"


class AIServiceDiscoveryTests(unittest.TestCase):
    def test_batch_excludes_history_and_other_groups(self):
        store = FakeStore()
        hits = [repo(1), repo(3), repo(4), repo(5, 999, True),
                repo(6, 900)] + [repo(number) for number in range(7, 38)]
        client = FakeClient(hits)
        batch = AIService(client, store, object()).candidate_batch(DAY, 10, None)
        ids = [item.repo.id for item in batch.inputs]
        self.assertEqual(ids[0], 2)
        self.assertLessEqual(len(ids), 20)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue({1, 3, 4, 5, 6}.isdisjoint(ids))
        self.assertEqual(ids[1:], list(range(7, 26)))
        self.assertEqual([item.candidate_rank for item in batch.inputs[1:]], list(range(6, 25)))
        self.assertTrue(all(item.source_limited for item in batch.inputs))
        self.assertEqual(client.search_calls[0][1:], (1, 100, "stars"))
        self.assertIn("local ai", client.search_calls[0][0])

    def test_already_checked_cards_are_skipped_and_readme_failure_is_limited(self):
        store = FakeStore()
        store.checked[2] = RelevanceVerdict(2, "relevant", "yes")
        client = FakeClient([repo(7), repo(8)])
        client.readme_failure = True
        batch = AIService(client, store, object()).candidate_batch(DAY, 10, None)
        self.assertEqual([item.repo.id for item in batch.inputs], [7, 8])
        self.assertTrue(all(item.source_limited and item.readme_excerpt is None
                            for item in batch.inputs))

    def test_search_failure_does_not_return_partial_batch(self):
        class FailingClient(FakeClient):
            def search(self, *args, **kwargs):
                raise GitHubRequestError("search quota")

        with self.assertRaises(GitHubRequestError):
            AIService(FailingClient([]), FakeStore(), object()).candidate_batch(DAY, 10, None)


class AIServicePersistenceTests(unittest.TestCase):
    def test_manual_analysis_supports_saved_history_and_follow_only_projects(self):
        class Provider:
            def explain(inner,source,keyword,model_id):
                facts=InsightText('Summary','Purpose','Scenario','Users',('Core',),problem='Problem',prerequisites='Python')
                return ProjectExplanation(facts,facts,None,(),True,schema_version=2)
        self.store.commit_daily('2026-09-28',[repo(7)],[],[pick(7,'growth',day='2026-09-28')])
        service=AIService(FakeClient([]),self.store,Provider())
        self.assertEqual(service.explain_project(2,None,self.at,context_date=DAY).schema_version,2)
        self.store.set_followed(7,True,self.at)
        self.assertEqual(service.explain_project(7,None,self.at).schema_version,2)

    def test_manual_analysis_receives_complete_cached_readme_and_source_link(self):
        from tests.test_readme_content import document
        doc = document('# Project\n' + 'Usage details. ' * 1000 + '\nTAIL: reusable agent skill', 2)
        self.store.save_readme(doc)
        class Provider:
            def explain(inner, source, keyword, model_id):
                self.assertEqual(source.readme_excerpt, doc.text)
                self.assertEqual(source.readme_source_url, doc.source_url)
                self.assertFalse(source.source_limited)
                content = InsightText('Summary', 'Purpose', 'Scenario', 'Users', ())
                return ProjectExplanation(content, content, 'relevant', (), False)
        client = FakeClient([])
        client.readme_failure = True
        result = AIService(client, self.store, Provider()).explain_project(2, None, self.at)
        self.assertEqual(result.readme_hash, doc.content_hash)

    def test_manual_analysis_readme_failure_never_calls_ai_or_replaces_old_explanation(self):
        from unittest.mock import Mock
        client = FakeClient([])
        client.readme_failure = True
        old_text = InsightText('Saved summary', 'Saved purpose', 'Scenario', 'Users', ())
        old = ProjectExplanation(old_text, old_text, 'relevant', (), True)
        self.store.save_explanation(2, self.rule.id, None, 'old-input-version', old)
        provider = Mock()
        content = InsightText('Summary', 'Purpose', 'Scenario', 'Users', ())
        provider.explain.return_value = ProjectExplanation(content, content, 'relevant', (), False)
        with self.assertRaisesRegex(GitHubRequestError, 'README'):
            AIService(client, self.store, provider).explain_project(2, None, self.at, force=True)
        provider.explain.assert_not_called()
        self.assertEqual(self.store.latest_explanation_any_model(2, self.rule.id)[0], old)

    def test_manual_analysis_fetches_full_readme_once_and_reuses_it_after_quota_failure(self):
        from github_radar.readme_types import ReadmeFetch
        class Client(FakeClient):
            calls = 0
            def fetch_readme_by_id(inner, repo_id, etag=None):
                inner.calls += 1
                if inner.readme_failure:
                    raise GitHubRequestError('GitHub README 请求达到限流')
                return ReadmeFetch('# Skill\n' + 'Instructions ' * 1000 + '\nTail usage.', None, False, False,
                                   'https://github.com/owner/repo-2/blob/main/README.md')
        class Provider:
            calls = 0
            def explain(inner, source, keyword, model_id):
                inner.calls += 1
                self.assertTrue(source.readme_excerpt.endswith('Tail usage.'))
                content = InsightText('Summary', 'Purpose', 'Scenario', 'Users', ())
                return ProjectExplanation(content, content, 'relevant', (), False)
        client, provider = Client([]), Provider()
        service = AIService(client, self.store, provider)
        first = service.explain_project(2, None, self.at)
        self.assertIsNotNone(self.store.load_readme(2))
        client.readme_failure = True
        self.assertEqual(service.explain_project(2, None, self.at), first)
        service.explain_project(2, None, self.at, force=True)
        self.assertEqual(client.calls, 1)
        self.assertEqual(provider.calls, 2)

    def test_real_client_exhausted_quota_uses_complete_local_document_without_network(self):
        from unittest.mock import Mock
        from github_radar.github_client import GitHubClient
        from tests.test_readme_content import document
        import time
        opener = Mock(side_effect=AssertionError('No network with complete cache'))
        client = GitHubClient(opener=opener)
        client.core_remaining = 0
        client.core_reset_at = int(time.time()) + 3600
        content = InsightText('Summary', 'Purpose', 'Scenario', 'Users', ())
        provider = Mock()
        provider.explain.return_value = ProjectExplanation(content, content, 'relevant', (), False)
        service = AIService(client, self.store, provider)
        with self.assertRaisesRegex(GitHubRequestError, '限流'):
            service.explain_project(2, None, self.at)
        provider.explain.assert_not_called()
        self.store.save_readme(document('# Complete author README', 2))
        service.explain_project(2, None, self.at)
        provider.explain.assert_called_once()
        opener.assert_not_called()

    def test_manual_analysis_renamed_repository_uses_verified_document_identity(self):
        from dataclasses import replace
        from github_radar.readme_types import ReadmeFetch
        class Client(FakeClient):
            def get_repository_by_id(inner, repo_id):
                return replace(repo(repo_id), full_name='owner/renamed', html_url='https://github.com/owner/renamed')
            def fetch_readme_by_id(inner, repo_id, etag=None):
                return ReadmeFetch('# Renamed skill', None, False, False,
                                   'https://github.com/owner/renamed/blob/main/README.md')
        class Provider:
            def explain(inner, source, keyword, model_id):
                self.assertEqual(source.repo.full_name, 'owner/renamed')
                self.assertEqual(source.repo.html_url, 'https://github.com/owner/renamed')
                self.assertEqual(source.repo.id, 2)
                content = InsightText('Summary', 'Purpose', 'Scenario', 'Users', ())
                return ProjectExplanation(content, content, 'relevant', (), False)
        AIService(Client([]), self.store, Provider()).explain_project(2, None, self.at)
        self.assertEqual(self.store.repositories_for_ids([2])[2].full_name, 'owner/repo-2')

    def test_manual_analysis_missing_or_empty_readme_does_not_generate(self):
        from unittest.mock import Mock
        from github_radar.readme_types import ReadmeFetch
        class Client(FakeClient):
            def fetch_readme_by_id(inner, repo_id, etag=None):
                return ReadmeFetch(inner.text, None, False, False)
        for text in (None, '   '):
            with self.subTest(text=text):
                client = Client([])
                client.text = text
                provider = Mock()
                with self.assertRaisesRegex(GitHubRequestError, 'README'):
                    AIService(client, self.store, provider).explain_project(2, None, self.at)
                provider.explain.assert_not_called()

    def test_manual_analysis_rejects_truncated_readme_before_ai(self):
        from dataclasses import replace
        from unittest.mock import Mock
        from tests.test_readme_content import document
        self.store.save_readme(replace(document('# Project incomplete', 2), truncated=True))
        provider = Mock()
        content = InsightText('Summary', 'Purpose', 'Scenario', 'Users', ())
        provider.explain.return_value = ProjectExplanation(content, content, 'relevant', (), False)
        with self.assertRaisesRegex(GitHubRequestError, 'README'):
            AIService(FakeClient([]), self.store, provider).explain_project(2, None, self.at)
        provider.explain.assert_not_called()

    def test_analysis_cache_tracks_full_readme_hash_including_changed_tail(self):
        from tests.test_readme_content import document
        class Provider:
            calls=0
            def explain(inner,source,keyword,model_id):
                inner.calls+=1
                facts=InsightText('Summary','Purpose','Scenario','Users',('Core',),
                                  problem='Problem',prerequisites='Python')
                return ProjectExplanation(facts,facts,'relevant',(),True,schema_version=2)
        provider=Provider()
        first_doc=document('# Project\n\n'+'English description. '*500+'\n\nFirst tail.',2)
        self.store.save_readme(first_doc)
        service=AIService(FakeClient([]),self.store,provider)
        first=service.explain_project(2,None,self.at)
        self.assertEqual(first.readme_hash,first_doc.content_hash)
        self.assertEqual(service.explain_project(2,None,self.at),first)
        self.assertEqual(provider.calls,1)
        second_doc=document(first_doc.text.replace('First tail.','Changed tail.'),2)
        self.store.save_readme(second_doc)
        second=service.explain_project(2,None,self.at)
        self.assertEqual(second.readme_hash,second_doc.content_hash)
        self.assertEqual(provider.calls,2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = RadarStore(self.temp.name)
        self.rule = self.store.add_keyword("local ai")
        self.at = f"{DAY}T12:00:00Z"
        self.store.commit_daily(DAY, [repo(2)], [], [
            pick(2, "keyword", self.rule.id)], completed_at=self.at,
            completed_sections=[("keyword", self.rule.id)])

    def test_refine_commits_only_after_complete_provider_result(self):
        class Provider:
            calls = 0

            def judge_batch(self, inputs, keyword, model_id):
                self.calls += 1
                return tuple(RelevanceVerdict(item.repo.id,
                                              "irrelevant" if item.repo.id == 2 else "relevant",
                                              "Evidence") for item in inputs)

        provider = Provider()
        service = AIService(FakeClient([repo(7, stars=4000)]), self.store, provider)
        service.refine_keyword(DAY, self.rule.id, None, self.at)
        picks = [item.repo_id for item in self.store.daily_recommendations(DAY)]
        self.assertEqual(picks, [7])
        self.assertEqual(provider.calls, 1)
        self.assertIn(2, self.store.seen_repo_ids())

    def test_refine_next_day_keeps_previous_growth_record_for_same_repository(self):
        previous = "2026-09-26"
        self.store.commit_daily(previous, [repo(2)], [], [
            pick(2, "growth", day=previous)], completed_at=f"{previous}T12:00:00Z")

        class Provider:
            def judge_batch(self, inputs, keyword, model_id):
                return tuple(RelevanceVerdict(item.repo.id,
                                              "irrelevant" if item.repo.id == 2 else "relevant",
                                              "Matches keyword" if item.repo.id != 2 else "Unrelated")
                             for item in inputs)

        AIService(FakeClient([repo(7)]), self.store, Provider()).refine_keyword(
            DAY, self.rule.id, None, self.at)
        self.assertEqual([item.repo_id for item in self.store.daily_recommendations(previous)],
                         [2])
        self.assertEqual([item.repo_id for item in self.store.daily_recommendations(DAY)], [7])

    def test_refine_provider_failure_preserves_old_list(self):
        class Provider:
            def judge_batch(self, inputs, keyword, model_id):
                raise RuntimeError("model failed")

        with self.assertRaises(RuntimeError):
            AIService(FakeClient([repo(7)]), self.store, Provider()).refine_keyword(
                DAY, self.rule.id, None, self.at)
        self.assertEqual([item.repo_id for item in self.store.daily_recommendations(DAY)], [2])
        self.assertEqual(self.store.ai_progress(DAY, self.rule.id, None).checked_count, 0)

    def test_delete_during_ai_judgement_keeps_old_history_without_new_result(self):
        owner = self

        class Provider:
            def judge_batch(self, inputs, keyword, model_id):
                owner.store.soft_delete_keyword(owner.rule.id, owner.at)
                return tuple(RelevanceVerdict(item.repo.id, "relevant", "Matches")
                             for item in inputs)

        service = AIService(FakeClient([repo(7)]), self.store, Provider())
        with self.assertRaises(ValueError):
            service.refine_keyword(DAY, self.rule.id, None, self.at)
        self.assertEqual([item.repo_id for item in self.store.daily_recommendations(DAY)], [2])
        self.assertEqual(self.store.ai_progress(DAY, self.rule.id, None).checked_count, 0)

    def test_deleted_keyword_cannot_generate_new_ai_explanation(self):
        class Provider:
            def explain(self, *args):
                raise AssertionError("Deleted keyword must not reach Codex")

        self.store.soft_delete_keyword(self.rule.id, self.at)
        with self.assertRaises(ValueError):
            AIService(FakeClient([]), self.store, Provider()).explain_project(2, None, self.at)

    def test_delete_during_ai_explanation_cannot_save_new_result(self):
        owner = self

        class Provider:
            def explain(self, source, keyword, model_id):
                owner.store.soft_delete_keyword(owner.rule.id, owner.at)
                text = InsightText("Summary", "Purpose", "Scenario", "Users", ())
                return ProjectExplanation(text, text, "relevant", (), False)

        with self.assertRaises(ValueError):
            AIService(FakeClient([]), self.store, Provider()).explain_project(2, None, self.at)
        self.assertIsNone(self.store.latest_explanation_any_model(2, self.rule.id))

    def test_repeated_refine_with_five_verified_cards_does_not_call_model(self):
        class Provider:
            calls = 0

            def judge_batch(self, inputs, keyword, model_id):
                self.calls += 1
                return tuple(RelevanceVerdict(item.repo.id, "relevant", "Matches")
                             for item in inputs)

        provider = Provider()
        client = FakeClient([repo(n) for n in range(7, 55)])
        service = AIService(client, self.store, provider)
        service.refine_keyword(DAY, self.rule.id, None, self.at)
        before = [item.repo_id for item in self.store.daily_recommendations(DAY)]
        service.refine_keyword(DAY, self.rule.id, None, self.at)
        self.assertEqual(provider.calls, 1)
        self.assertEqual([item.repo_id for item in self.store.daily_recommendations(DAY)], before)

    def test_explanation_cache_survives_reopen_and_model_switch(self):
        class Provider:
            calls = 0

            def explain(self, source, keyword, model_id):
                self.calls += 1
                content = InsightText("Summary", "Purpose", "Scenario", "Users", ("Feature",))
                return ProjectExplanation(content, content, "relevant", ("Description",), False)

        provider = Provider()
        client = FakeClient([])
        service = AIService(client, self.store, provider)
        first = service.explain_project(2, None, self.at)
        second = service.explain_project(2, None, self.at)
        self.assertEqual(first, second)
        self.assertEqual(provider.calls, 1)
        reopened = AIService(client, RadarStore(self.temp.name), provider)
        self.assertEqual(reopened.explain_project(2, None, self.at), first)
        self.assertEqual(provider.calls, 1)
        reopened.explain_project(2, "gpt-6-sol", self.at)
        self.assertEqual(provider.calls, 2)

    def test_explicit_regeneration_replaces_cache_only_after_success(self):
        class Provider:
            calls = 0
            fail = False

            def explain(self, source, keyword, model_id):
                self.calls += 1
                if self.fail:
                    raise RuntimeError("model unavailable")
                content = InsightText(f"Summary {self.calls}", "Purpose", "Scenario",
                                      "Users", ("Feature",))
                return ProjectExplanation(content, content, "relevant", ("Description",), False)

        provider = Provider()
        service = AIService(FakeClient([]), self.store, provider)
        first = service.explain_project(2, None, self.at)
        second = service.explain_project(2, None, self.at, force=True)
        self.assertEqual(second.zh.summary, "Summary 2")
        self.assertEqual(provider.calls, 2)
        provider.fail = True
        with self.assertRaises(RuntimeError):
            service.explain_project(2, None, self.at, force=True)
        self.assertEqual(service.explain_project(2, None, self.at), second)
        self.assertNotEqual(first, second)

    def test_cancel_during_candidate_download_never_calls_model_or_commits(self):
        entered, release = threading.Event(), threading.Event()

        class SlowClient(FakeClient):
            def readme_excerpt(self, full_name, max_chars=2400):
                entered.set()
                release.wait(3)
                return "README evidence"

        class Provider:
            calls = 0

            def judge_batch(self, inputs, keyword, model_id):
                self.calls += 1
                return tuple(RelevanceVerdict(item.repo.id, "relevant", "yes")
                             for item in inputs)

            def cancel(self):
                pass

        provider = Provider()
        service = AIService(SlowClient([repo(7)]), self.store, provider)
        errors = []

        def run():
            try:
                service.refine_keyword(DAY, self.rule.id, None, self.at)
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(entered.wait(2))
        try:
            service.cancel()
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(errors)
        self.assertEqual(provider.calls, 0)
        self.assertEqual([item.repo_id for item in self.store.daily_recommendations(DAY)], [2])

    def test_cancel_after_model_answer_never_commits_batch(self):
        entered, release = threading.Event(), threading.Event()

        class Provider:
            def judge_batch(self, inputs, keyword, model_id):
                entered.set()
                release.wait(3)
                return tuple(RelevanceVerdict(item.repo.id, "relevant", "yes")
                             for item in inputs)

            def cancel(self):
                pass

        service = AIService(FakeClient([repo(7)]), self.store, Provider())
        errors = []

        def run():
            try:
                service.refine_keyword(DAY, self.rule.id, None, self.at)
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(entered.wait(2))
        try:
            service.cancel()
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(errors)
        self.assertEqual([item.repo_id for item in self.store.daily_recommendations(DAY)], [2])


if __name__ == "__main__":
    unittest.main()
