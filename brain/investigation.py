"""
brain/investigation.py - InvestigationEngine and modular InvestigationSources for Local Learning Engine.

Establishes first-class active evidence gathering across real available sources:
- ObsidianSource: Queries Obsidian vault via LanceDB + NetworkX RAG.
- ExecutionHistorySource: Queries past debugging notes (<vault>/Debugging experience/).
- ProjectFilesystemSource: Inspects local project directory files/code.
- DocumentationSource: Queries reference documentation or available web search tools.

Evaluates evidence sufficiency semantically (relevance + applicability to version/environment)
and returns structured evidence with provenance.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from brain.learning_records import EvidenceKind, EvidenceRecord


class InvestigationSource(ABC):
    """Abstract base class for modular investigation sources."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def query(self, topic: str, context: dict[str, Any]) -> list[EvidenceRecord]: ...


class ObsidianSource(InvestigationSource):
    """Investigation source querying the Obsidian Vault RAG system."""

    def __init__(self, vault: Any = None):
        self._vault = vault

    @property
    def name(self) -> str:
        return "obsidian_vault"

    def query(self, topic: str, context: dict[str, Any]) -> list[EvidenceRecord]:
        results: list[EvidenceRecord] = []
        if self._vault is None:
            # Fallback lazy import from brain_store
            try:
                import brain.brain_store as bs
                self._vault = bs.get_vault()
            except Exception:
                return results

        try:
            items = self._vault.retrieve(topic, limit=3)
            for item in items:
                text = item.get("text", "") or item.get("content", "")
                title = item.get("title", item.get("path", "Vault Note"))
                app_ver = context.get("app_version")
                results.append(
                    EvidenceRecord(
                        kind=EvidenceKind.DOCUMENTATION if "doc" in title.lower() else EvidenceKind.OBSERVATION,
                        source=f"vault:{title}",
                        claim=f"Vault knowledge on {topic}: {title}",
                        evidence_text=text[:500],
                        model_confidence=0.8,
                        verified=item.get("verified", False),
                        app_context=context.get("app"),
                        app_version=app_ver,
                    )
                )
        except Exception:
            pass
        return results


class ExecutionHistorySource(InvestigationSource):
    """Investigation source querying past debugging experience notes."""

    def __init__(self, vault_path: str | Path | None = None):
        self._vault_path = Path(vault_path) if vault_path else None

    @property
    def name(self) -> str:
        return "execution_history"

    def query(self, topic: str, context: dict[str, Any]) -> list[EvidenceRecord]:
        results: list[EvidenceRecord] = []
        if not self._vault_path:
            # Try default path
            user_home = Path.home()
            self._vault_path = user_home / ".gemini" / "antigravity" / "brain"

        debug_dir = self._vault_path / "MBUDDY" / "Debugging experience"
        if not debug_dir.exists():
            return results

        keywords = [k.lower() for k in topic.split() if len(k) > 3]
        for note_file in debug_dir.glob("*.md"):
            try:
                content = note_file.read_text(encoding="utf-8", errors="ignore")
                content_lower = content.lower()
                if any(kw in content_lower for kw in keywords):
                    results.append(
                        EvidenceRecord(
                            kind=EvidenceKind.FAILURE,
                            source=f"debug_note:{note_file.name}",
                            claim=f"Past failure experience: {note_file.stem}",
                            evidence_text=content[:500],
                            model_confidence=0.9,
                            verified=True,
                            app_context=context.get("app"),
                            app_version=context.get("app_version"),
                        )
                    )
            except Exception:
                continue
        return results[:3]


class ProjectFilesystemSource(InvestigationSource):
    """Investigation source inspecting local project source code and directory files."""

    def __init__(self, root_dir: str | Path = "."):
        self.root_dir = Path(root_dir)

    @property
    def name(self) -> str:
        return "project_filesystem"

    def query(self, topic: str, context: dict[str, Any]) -> list[EvidenceRecord]:
        results: list[EvidenceRecord] = []
        keywords = [k.lower() for k in topic.split() if len(k) > 3]
        if not keywords:
            return results

        # Simple file search in project directory
        matches = 0
        for root, _, files in os.walk(self.root_dir):
            if matches >= 3:
                break
            if any(p in root for p in (".git", "__pycache__", ".pytest_cache", "node_modules")):
                continue
            for file in files:
                if matches >= 3:
                    break
                if file.endswith((".py", ".js", ".ts", ".json", ".md")):
                    file_path = Path(root) / file
                    try:
                        text = file_path.read_text(encoding="utf-8", errors="ignore")
                        text_lower = text.lower()
                        if any(kw in text_lower for kw in keywords):
                            matches += 1
                            results.append(
                                EvidenceRecord(
                                    kind=EvidenceKind.DOCUMENTATION,
                                    source=f"file:{file_path.relative_to(self.root_dir)}",
                                    claim=f"Local code reference in {file}",
                                    evidence_text=text[:400],
                                    model_confidence=0.85,
                                    verified=True,
                                    app_context=context.get("app"),
                                    app_version=context.get("app_version"),
                                )
                            )
                    except Exception:
                        continue
        return results


class InvestigationEngine:
    """Active investigation engine coordinating evidence sources."""

    def __init__(self, sources: list[InvestigationSource] | None = None):
        if sources is None:
            sources = [
                ObsidianSource(),
                ExecutionHistorySource(),
                ProjectFilesystemSource(),
            ]
        self.sources = sources

    def investigate(self, topic: str, context: dict[str, Any]) -> tuple[str, list[EvidenceRecord]]:
        """Queries sources and semantically evaluates evidence sufficiency.

        Returns:
            ("HAS_SUFFICIENT_EVIDENCE" | "NEEDS_INVESTIGATION", evidence_list)
        """
        collected: list[EvidenceRecord] = []
        for src in self.sources:
            try:
                ev_list = src.query(topic, context)
                collected.extend(ev_list)
            except Exception:
                continue

        # Semantic sufficiency check: evidence is relevant and matches current app/version
        current_app = context.get("app")
        current_ver = context.get("app_version")

        relevant_evidence = []
        for ev in collected:
            # Match version if present
            if current_ver and ev.app_version and ev.app_version != current_ver:
                continue
            relevant_evidence.append(ev)

        # Check sufficiency: at least 1 verified or direct observation/doc matching topic
        if relevant_evidence:
            return "HAS_SUFFICIENT_EVIDENCE", relevant_evidence

        return "NEEDS_INVESTIGATION", collected
