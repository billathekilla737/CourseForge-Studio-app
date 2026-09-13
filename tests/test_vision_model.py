"""Which model actually grades, and whether the screen says so.

`vision_model` substitutes a cheaper model for a submission carrying images.
In a course where every submission is renders that means the model chosen in
the toolbar never runs at all, so the one thing this must never do is claim
otherwise.
"""
import unittest
from pathlib import Path

from courseforge import grader

WEB = Path(__file__).resolve().parent.parent / "courseforge" / "web"


class Cfg:
    def __init__(self, model, vision=""):
        self.model = model
        self.vision_model = vision


class TheSubstitutionOnlyEverGoesDown(unittest.TestCase):
    """It exists to stop an expensive default being spent on pictures, not to
    overrule a deliberately cheaper choice."""

    def test_opus_with_a_sonnet_vision_setting_reads_images_with_sonnet(self):
        self.assertEqual(grader.vision_model_for(Cfg("opus", "sonnet")), "sonnet")

    def test_haiku_is_not_quietly_upgraded_to_sonnet(self):
        self.assertEqual(grader.vision_model_for(Cfg("haiku", "sonnet")), "haiku")

    def test_an_empty_setting_means_the_chosen_model(self):
        self.assertEqual(grader.vision_model_for(Cfg("opus", "")), "opus")

    def test_the_same_model_twice_is_no_substitution(self):
        self.assertEqual(grader.vision_model_for(Cfg("sonnet", "sonnet")), "sonnet")

    def test_a_pinned_id_of_an_unknown_tier_keeps_the_choice(self):
        self.assertEqual(grader.vision_model_for(Cfg("some-new-model", "sonnet")),
                         "some-new-model")

    def test_a_full_model_id_is_read_as_its_tier(self):
        self.assertEqual(grader.vision_model_for(Cfg("claude-opus-5", "claude-sonnet-5")),
                         "claude-sonnet-5")


class TheScreenSaysWhichModelWillRun(unittest.TestCase):
    """The dialog title read "Auto-grading 23 students with opus" while every
    row underneath said sonnet was thinking. Both came from the same run."""

    def setUp(self):
        self.src = (WEB / "js" / "grade.js").read_text(encoding="utf-8")

    def test_the_job_title_does_not_name_the_grading_model_alone(self):
        self.assertNotIn("Auto-grading ${n} students with ${S.health.model}", self.src,
                         "the title claimed a model that may never be used")

    def test_the_job_title_names_both_when_they_differ(self):
        self.assertIn("for work with images", self.src)
        self.assertIn("function visionModel()", self.src)

    def test_the_toolbar_offers_the_vision_model_too(self):
        """A setting that can decide who grades a whole class does not belong
        in config.json with no way to see it."""
        self.assertIn("id=\"selVision\"", self.src)
        self.assertIn("setModel(ev.target.value, 'vision_model')", self.src)

    def test_switching_either_one_repaints_the_toolbar(self):
        self.assertIn("renderHeaderActions();", self.src.split("async function setModel")[1][:600])


class TheServerSendsAndAcceptsIt(unittest.TestCase):
    def setUp(self):
        self.src = (Path(__file__).resolve().parent.parent / "courseforge"
                    / "server.py").read_text(encoding="utf-8")

    def test_health_reports_the_vision_model(self):
        self.assertIn('"vision_model": getattr(self.cfg, "vision_model", "")', self.src)

    def test_settings_accepts_the_vision_model(self):
        self.assertIn('allowed = {"model", "vision_model", "grading_concurrency", "pseudonymize"}',
                      self.src)

    def test_both_models_are_checked_against_the_known_list(self):
        self.assertIn('if key in ("model", "vision_model"):', self.src)


if __name__ == "__main__":
    unittest.main()
