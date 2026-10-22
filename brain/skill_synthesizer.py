"""
Autonomous Skill & Task Recipe Synthesizer (S107, Milestone 3).

Monitors interaction logs, app execution histories, and multi-turn debugging success
to automatically synthesize and promote recurring successful action sequences into 
structured Obsidian Vault skills under <vault>/MBUDDY/Skills/.
"""

from __future__ import annotations

import re
from typing import Any, Iterable
import utils.logger as log
from brain import brain_store

class SkillSynthesizer:
    def __init__(self, vault=None) -> None:
        self._vault = vault

    def _get_vault(self):
        if self._vault is not None:
            return self._vault
        return brain_store.get_vault()

    def synthesize_from_execution(self, app_name: str, task_description: str,
                                   script_code: str, steps: list[dict | str] | None = None,
                                   user_confirmed: bool = True) -> str | None:
        """Synthesize a new Skill note in Obsidian Vault from a successful execution sequence."""
        vault = self._get_vault()
        if vault is None:
            log.warn("[SkillSynthesizer] Vault unavailable, skipping synthesis.")
            return None

        # Clean title
        clean_title = re.sub(r"[^\w\s-]", "", task_description.strip()).title()
        if not clean_title:
            clean_title = f"{app_name} Task"
        title = f"{app_name} - {clean_title}"[:70]

        when_clause = f"When asked to perform '{task_description}' in {app_name}."

        recipe_steps = steps or []
        if not recipe_steps and script_code:
            recipe_steps = [
                f"Run the following validated script in {app_name}:\n```\n{script_code.strip()}\n```"
            ]

        try:
            skill_path = vault.export_skill(
                title=title,
                steps=recipe_steps,
                when=when_clause,
                confirmed_by_user=user_confirmed,
                tags=["mbuddy/skill", f"mbuddy/app/{app_name.lower().replace(' ', '-')}"]
            )
            log.info(f"[SkillSynthesizer] Synthesized skill note: {skill_path}")
            return str(skill_path)
        except Exception as exc:
            log.error(f"[SkillSynthesizer] Failed to synthesize skill note: {exc}")
            return None


_synthesizer_instance = None

def get_synthesizer() -> SkillSynthesizer:
    global _synthesizer_instance
    if _synthesizer_instance is None:
        _synthesizer_instance = SkillSynthesizer()
    return _synthesizer_instance
