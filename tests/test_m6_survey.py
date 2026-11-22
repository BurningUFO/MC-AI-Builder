"""Guard the typed survey proposal and explicit image handling boundary."""

import json
import unittest

from src.architect.codex_provider import WALL_FIELDS
from src.architect.design import DesignError
from src.architect.providers import InvalidOutput
from src.architect.survey import decode, suggest


class SurveyDecisionTest(unittest.TestCase):
    def decision(self, **fields):
        data = {"action": "design", "reason": "平缓地基", "offset": None, "pad": 0,
                "changes": {"width": 6, "glass": None, "porch": False}}
        return decode(json.dumps(data | fields), WALL_FIELDS)

    def test_normalizes_only_existing_wall_fields(self):
        self.assertEqual(self.decision()["changes"], {"width": 6, "porch": False})
        for changes in ({"command": "anything"}, {"supply": "electric"}, [], None):
            with self.subTest(changes=changes), self.assertRaises(InvalidOutput):
                self.decision(changes=changes)

    def test_cannot_smuggle_a_movement_or_approval_into_a_design(self):
        for fields in ({"action": "approve"}, {"offset": [1, 0, 0]}, {"pad": True}, {"reason": ""}):
            with self.subTest(fields=fields), self.assertRaises(InvalidOutput):
                self.decision(**fields)

    def test_aim_overflow_and_non_integer_offsets_are_rejected(self):
        fields = {"action": "move_to_observation_point", "pad": None, "changes": None}
        self.assertEqual(self.decision(offset=[2, 1, 0], **fields)["offset"], [2, 1, 0])
        for offset in ([100, 0, 0], [True, 0, 0], [0, 0, 0.5], [2, 0]):
            with self.subTest(offset=offset), self.assertRaises(InvalidOutput):
                self.decision(offset=offset, **fields)

    def test_survey_with_image_sharing_disabled_does_not_call_bridge_or_model(self):
        class OfflineRuntime:
            share_observations = False
            provider = object()

        with self.assertRaises(DesignError):
            suggest(OfflineRuntime(), {}, "现场设计")
