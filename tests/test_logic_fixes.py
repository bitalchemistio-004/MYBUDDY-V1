import sys
import os
import unittest
from unittest.mock import patch

# Ensure project root is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.state_manager import MBUDDYState
from brain.llm_router import determine_search_query

class TestLogicFixes(unittest.IsolatedAsyncioTestCase):
    
    def test_energy_floor_fix(self):
        """Verify that energy can now drop below the critical threshold (12)."""
        state = MBUDDYState()
        state.energy = 15.0
        
        # Patching the specific datetime class imported in core.state_manager
        with patch("random.uniform", return_value=4.0):
            with patch("core.state_manager.datetime") as mock_dt:
                # Mock now() to return a specific time
                from datetime import datetime, timezone, timedelta
                mock_now = datetime(2026, 5, 31, 10, 0, 0, tzinfo=timezone.utc)
                mock_dt.now.return_value = mock_now
                
                # Set last_tick to 1 hour (60 mins) before mock_now
                state.last_tick = mock_now - timedelta(minutes=60)
                
                state.tick()
                
        self.assertEqual(state.energy, 8.0)
        self.assertLess(state.energy, 12.0)
        self.assertIn(state.current, ["Sleepy", "Sleepy", "Furious", "Scared"])

    async def test_pattern_search_triggers_for_live_query(self):
        """Verify pattern routing correctly identifies live-data queries without any LLM call."""
        # Live-data query should trigger (matches LIVE_SEARCH_PATTERNS)
        result_live = await determine_search_query("what's the current bitcoin price?", [])
        self.assertIsNotNone(result_live)
        self.assertIn("bitcoin", result_live.lower())

    async def test_pattern_search_suppressed_for_social_query(self):
        """Verify NEVER_SEARCH_PATTERNS correctly prevents a search call for social/memory queries."""
        result_none = await determine_search_query("how are you feeling today?", [])
        self.assertIsNone(result_none)

    def test_companion_persona_is_specific_not_generic_warmth(self):
        from brain.personality import IDENTITY_CORE

        # The old "You always say you'll document it later" example taught the
        # model to invent habits; the persona now forbids unseen habits instead.
        self.assertIn("Never claim habits you have not seen", IDENTITY_CORE)
        self.assertIn("Memory is background, not a trophy", IDENTITY_CORE)

    def test_energy_changes_reply_budget(self):
        from brain.reply_policy import reply_max_tokens, reply_temperature

        text = "tell me what happened with the system today"
        self.assertLess(reply_max_tokens(text, energy=15), reply_max_tokens(text, energy=85))
        self.assertLess(reply_temperature("normal question", energy=15), reply_temperature("normal question", energy=90))

    def test_conversation_prompt_includes_energy_and_memory_restraint(self):
        from brain.memory_context import build_conversation_system

        state = MBUDDYState()
        state.energy = 18
        prompt = build_conversation_system(
            state,
            {
                "relationship_state": {
                    "current_vibe": "focused",
                    "history_of_mood_patterns": {
                        "tends_to_be_stressed_around": ["hardware bugs"],
                        "responds_well_to": "direct answers first, then jokes",
                        "gets_annoyed_by": "over-explanation",
                    },
                }
            },
            "en",
        )
        self.assertIn("ENERGY BEHAVIOR: very low", prompt)
        self.assertIn("Do not mention a recent memory just because it is present", prompt)
        self.assertIn("RELATIONSHIP READ", prompt)
        self.assertIn("tends to be stressed around: hardware bugs", prompt)
        self.assertIn("responds well to: direct answers first, then jokes", prompt)

if __name__ == "__main__":
    unittest.main()
