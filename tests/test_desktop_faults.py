"""Tests of real-window injection timing, using only read callbacks and fake time."""

import unittest
from gui_tasks.desktop_faults import inject_after_submit_acknowledgement


class DesktopFaultTests(unittest.TestCase):
    def setUp(self): self.now=0.
    def sleep(self, seconds): self.now+=seconds
    def test_delayed_callback_is_acknowledged_before_injected_failure(self):
        counts=iter([0,0,1])
        with self.assertRaises(RuntimeError):
            inject_after_submit_acknowledgement(0,lambda:next(counts),clock=lambda:self.now,sleep=self.sleep)
        self.assertEqual(self.now,.05)
    def test_missing_ack_is_fixture_failure_instead_of_fake_post_commit_failure(self):
        with self.assertRaises(AssertionError):
            inject_after_submit_acknowledgement(0,lambda:0,timeout=.1,clock=lambda:self.now,sleep=self.sleep)
        self.assertAlmostEqual(self.now,.1)
    def test_multiple_callbacks_are_rejected(self):
        with self.assertRaises(AssertionError):
            inject_after_submit_acknowledgement(0,lambda:2,clock=lambda:self.now,sleep=self.sleep)


if __name__=='__main__': unittest.main()
