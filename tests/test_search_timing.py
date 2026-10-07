"""Real waiting is measured; suspended modules must not charge each other."""
import unittest
from github_radar.search_timing import SearchTiming

class TimingTests(unittest.TestCase):
 def test_suspended_module_does_not_include_another_modules_ai_wait(self):
  now=[0.0];timing=SearchTiming(lambda:now[0])
  timing.resume();timing.switch('collecting');now[0]=2
  timing.pause();now[0]=122
  timing.resume();timing.switch('checking');now[0]=127
  timing.pause()
  self.assertEqual(timing.snapshot(),{'collecting':2.0,'checking':5.0})
 def test_snapshot_during_call_includes_current_wait_without_double_counting(self):
  now=[0.0];timing=SearchTiming(lambda:now[0]);timing.resume();timing.switch('checking')
  now[0]=17;self.assertEqual(timing.snapshot()['checking'],17)
  self.assertEqual(timing.snapshot()['checking'],17)
  timing.switch('ranking');now[0]=18;timing.pause()
  self.assertEqual(timing.snapshot(),{'checking':17.0,'ranking':1.0})
 def test_resuming_is_idempotent_and_negative_clock_delta_is_not_reported(self):
  now=[2.0];timing=SearchTiming(lambda:now[0]);timing.resume();timing.switch('preparing')
  now[0]=3;timing.resume();now[0]=4;timing.pause()
  self.assertEqual(timing.snapshot()['preparing'],2)
  timing.resume();now[0]=3;timing.pause();self.assertEqual(timing.snapshot()['preparing'],2)

class CoordinatorTimingTests(unittest.TestCase):
 def fixture(self):
  from tests.test_search_coordinator import SearchCoordinatorTests
  f=SearchCoordinatorTests();f.setUp();self.addCleanup(f.doCleanups);return f
 def test_real_ai_wait_is_persisted_even_when_the_model_fails(self):
  from github_radar.ai_provider import AIOutputError
  from github_radar.search_storage import SearchStore
  f=self.fixture();now=[0.0];f.engine.clock=lambda:now[0]
  def fail(*args,**kwargs):
   self.assertTrue(SearchStore(f.store).progress(f.engine.search.claim_run(f.scope,'observer',now=f.now.timestamp()).job_id).ai_started_at)
   now[0]+=41;raise AIOutputError('invalid verdict')
  f.ai.filter_batch=fail
  result=f.engine.run(f.scope)
  self.assertEqual(result.status,'paused')
  self.assertEqual(result.ai_seconds,41)
  self.assertFalse(result.ai_started_at)
  self.assertEqual(result.stage_seconds['checking'],41)
  self.assertEqual(result.elapsed_seconds,41)
  self.assertEqual(SearchStore(f.store).progress(result.job_id).ai_seconds,41)
 def test_same_day_retry_resets_timings_but_preserves_paid_usage(self):
  f=self.fixture();first=f.engine.run(f.scope);second=f.engine.run(f.scope)
  self.assertEqual(first.newly_checked,second.newly_checked)
  self.assertEqual(second.ai_seconds,0)
  self.assertIsInstance(second.stage_seconds,dict)
  self.assertTrue(second.started_at)
