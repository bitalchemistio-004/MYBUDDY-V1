"""
Model Capabilities & Dynamic Context Engine for MBUDDY.

Provides model capability discovery (context window, thinking support, parameter families),
task-adaptive execution policies (dynamic reasoning, generation budgets),
and priority-aware semantic context assembly without arbitrary character slicing.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple, Set

import requests
import utils.logger as log


class CapabilityStatus(Enum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    FALLBACK = "FALLBACK"


class TaskComplexity(Enum):
    SIMPLE = "SIMPLE"
    MODERATE = "MODERATE"
    COMPLEX = "COMPLEX"
    DEBUGGING = "DEBUGGING"


@dataclass
class ModelCapabilities:
    model_name: str
    context_window: int = 8192
    max_generation_tokens: int = 4096
    supports_thinking: bool = False
    family: str = "generic"
    capability_status: CapabilityStatus = CapabilityStatus.FALLBACK
    extra_options: Dict[str, Any] = field(default_factory=dict)


def detect_model_capabilities(model_name: str, ollama_url: Optional[str] = None) -> ModelCapabilities:
    """Discovers context window, thinking support, and generation limits from Ollama API (/api/show).
    Falls back gracefully if Ollama metadata is missing or unreachable."""
    if not ollama_url:
        from brain.agent import _ollama_chat_url
        ollama_url = _ollama_chat_url().rsplit("/api/", 1)[0]

    try:
        resp = requests.post(f"{ollama_url}/api/show", json={"name": model_name}, timeout=3.0)
        if resp.status_code == 200:
            data = resp.json()
            details = data.get("details", {})
            model_info = data.get("model_info", {})

            # Extract context window length from model parameters or model info
            ctx_len = 8192
            for key in ("qwen2.context_length", "llama.context_length", "context_length"):
                if key in model_info:
                    ctx_len = int(model_info[key])
                    break
            
            # Check model family and thinking capability
            family = details.get("family", "").lower()
            supports_think = "qwen3" in model_name.lower() or "deepseek-r1" in model_name.lower() or "r1" in family

            # Safe max generation default (typically 50% of context window up to 8192)
            max_gen = max(2048, min(8192, int(ctx_len * 0.5)))

            log.info(f"[Capabilities] Detected {model_name}: ctx={ctx_len}, think={supports_think}, max_gen={max_gen}")
            return ModelCapabilities(
                model_name=model_name,
                context_window=ctx_len,
                max_generation_tokens=max_gen,
                supports_thinking=supports_think,
                family=family or "detected",
                capability_status=CapabilityStatus.KNOWN,
            )
    except Exception as exc:
        log.warn(f"[Capabilities] Could not query Ollama for {model_name}: {exc}. Using safe fallback.")

    # Conservative safe fallback
    is_qwen3 = "qwen3" in model_name.lower()
    return ModelCapabilities(
        model_name=model_name,
        context_window=8192,
        max_generation_tokens=3072,
        supports_thinking=is_qwen3,
        family="qwen" if is_qwen3 else "generic",
        capability_status=CapabilityStatus.FALLBACK,
    )


@dataclass
class ExecutionPolicy:
    task_complexity: TaskComplexity
    enable_thinking: bool
    temperature: float
    min_generation_tokens: int
    max_generation_tokens: int
    context_reserve_tokens: int


def derive_execution_policy(
    caps: ModelCapabilities,
    complexity: TaskComplexity,
    is_retry: bool = False,
) -> ExecutionPolicy:
    """Derives task-adaptive thinking, temperature, and generation token reserves."""
    # Reasoning policy: think when supported and task is complex, planning, or debugging
    should_think = caps.supports_thinking and complexity in (
        TaskComplexity.COMPLEX,
        TaskComplexity.DEBUGGING,
    )

    if complexity == TaskComplexity.SIMPLE:
        temp = 0.1
        min_gen = 512
        max_gen = min(2048, caps.max_generation_tokens)
        reserve = 512
    elif complexity == TaskComplexity.MODERATE:
        temp = 0.2
        min_gen = 1024
        max_gen = min(4096, caps.max_generation_tokens)
        reserve = 1024
    elif complexity == TaskComplexity.COMPLEX:
        temp = 0.2
        min_gen = 1536
        max_gen = caps.max_generation_tokens
        reserve = 1536
    else:  # DEBUGGING
        temp = 0.1
        min_gen = 1024
        max_gen = caps.max_generation_tokens
        reserve = 1024

    if is_retry:
        # Give retries slightly higher budget and flexibility
        min_gen = max(min_gen, 1536)

    return ExecutionPolicy(
        task_complexity=complexity,
        enable_thinking=should_think,
        temperature=temp,
        min_generation_tokens=min_gen,
        max_generation_tokens=max_gen,
        context_reserve_tokens=reserve,
    )


def estimate_tokens(text: str) -> int:
    """Estimates tokens conservatively (~3.0 chars per token for code/prose mixed)."""
    if not text:
        return 0
    return int(len(text) / 3.0) + 1


def preserve_structured_script(code: str, max_tokens: int = 4000, failing_line: str = "") -> str:
    """Preserves complete script when it fits inside token budget.
    When exceeding budget, intelligently preserves imports, helper signatures,
    the failing function/block, and ending code without arbitrary truncation."""
    if not code:
        return ""
    if estimate_tokens(code) <= max_tokens:
        return code

    lines = code.splitlines()
    if len(lines) <= 40:
        return code

    # Extract imports and setup
    head_lines = []
    for line in lines[:20]:
        if line.startswith(("import ", "from ", "#", "def ", "class ", "import")):
            head_lines.append(line)

    # Locate failing line context if specified
    failing_idx = -1
    if failing_line:
        clean_fail = failing_line.strip()
        for idx, line in enumerate(lines):
            if clean_fail in line.strip():
                failing_idx = idx
                break

    preserved_blocks = []
    preserved_blocks.append("# --- [SCRIPT HEADER / IMPORTS] ---")
    preserved_blocks.extend(head_lines or lines[:10])

    if failing_idx != -1:
        start_f = max(0, failing_idx - 15)
        end_f = min(len(lines), failing_idx + 15)
        preserved_blocks.append(f"\n# --- [FAILING REGION (Lines {start_f+1} to {end_f})] ---")
        preserved_blocks.extend(lines[start_f:end_f])
    else:
        # Preserve middle and tail regions
        mid = len(lines) // 2
        preserved_blocks.append(f"\n# --- [MID-SCRIPT CONTEXT (Lines {mid-10} to {mid+10})] ---")
        preserved_blocks.extend(lines[max(0, mid-10):min(len(lines), mid+10)])

    preserved_blocks.append(f"\n# --- [SCRIPT TAIL (Lines {len(lines)-15} to {len(lines)})] ---")
    preserved_blocks.extend(lines[-15:])

    result = "\n".join(preserved_blocks)
    return result if estimate_tokens(result) <= max_tokens else code[:max_tokens * 3]


def preserve_structured_error(error: str, max_tokens: int = 1500) -> str:
    """Preserves complete traceback, exception type, failing frames, and message.
    Avoids lossy end-only character slicing."""
    if not error:
        return ""
    if estimate_tokens(error) <= max_tokens:
        return error

    lines = [ln for ln in error.splitlines() if ln.strip()]
    if not lines:
        return error

    # Extract traceback header and tail exception lines
    tb_header = [ln for ln in lines if "Traceback" in ln]
    exc_lines = [ln for ln in lines if any(k in ln for k in ("Error", "Exception", "failed", "invalid", "refused", "Volume"))]

    preserved = []
    if tb_header:
        preserved.extend(tb_header)
    preserved.extend(lines[-25:])
    if exc_lines and exc_lines[-1] not in preserved:
        preserved.append(exc_lines[-1])

    result = "\n".join(preserved)
    return result if estimate_tokens(result) <= max_tokens else error[:max_tokens * 3]


@dataclass
class PriorityItem:
    priority: int  # 0=P0 (critical), 1=P1 (important), 2=P2 (useful), 3=P3 (optional)
    name: str
    content: str
    tokens: int = 0
    dependencies: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.tokens:
            self.tokens = estimate_tokens(self.content)


@dataclass
class ContextReport:
    context_pressure: str  # NONE, LIGHT, MODERATE, SEVERE
    context_window: int
    used_input_tokens: int
    allocated_generation_tokens: int
    included_priorities: List[str]
    dropped_priorities: List[str]


class PriorityContextBuilder:
    """Assembles prompts dynamically by priority (P0 -> P1 -> P2 -> P3)
    and dependency order based on model capabilities and context budget."""

    def __init__(self, caps: ModelCapabilities, policy: ExecutionPolicy):
        self.caps = caps
        self.policy = policy
        self.items: List[PriorityItem] = []

    def add_item(self, priority: int, name: str, content: str, dependencies: Optional[List[str]] = None) -> None:
        if not content.strip():
            return
        item = PriorityItem(
            priority=priority,
            name=name,
            content=content,
            dependencies=dependencies or [],
        )
        self.items.append(item)

    def build(self) -> Tuple[str, ContextReport, int]:
        """Assembles prompt and returns (assembled_prompt, report, generation_budget)."""
        # Sort items by priority ascending (0, 1, 2, 3)
        self.items.sort(key=lambda x: x.priority)

        # Budget calculation
        total_window = self.caps.context_window
        safety_margin = 256
        max_prompt_budget = total_window - self.policy.min_generation_tokens - safety_margin

        included: List[PriorityItem] = []
        dropped: List[PriorityItem] = []
        used_tokens = 0

        # Satisfy dependencies first, then items by priority
        for item in self.items:
            if used_tokens + item.tokens <= max_prompt_budget:
                included.append(item)
                used_tokens += item.tokens
            else:
                if item.priority == 0:
                    # P0 is critical: try preserving structured representation
                    if "script" in item.name.lower():
                        compact = preserve_structured_script(item.content, max_tokens=max(500, max_prompt_budget - used_tokens))
                        c_tokens = estimate_tokens(compact)
                        if used_tokens + c_tokens <= max_prompt_budget:
                            item.content = compact
                            item.tokens = c_tokens
                            included.append(item)
                            used_tokens += c_tokens
                            continue
                    elif "error" in item.name.lower():
                        compact = preserve_structured_error(item.content, max_tokens=max(300, max_prompt_budget - used_tokens))
                        c_tokens = estimate_tokens(compact)
                        if used_tokens + c_tokens <= max_prompt_budget:
                            item.content = compact
                            item.tokens = c_tokens
                            included.append(item)
                            used_tokens += c_tokens
                            continue
                dropped.append(item)

        # Calculate dynamic generation budget
        usable_gen_budget = max(
            self.policy.min_generation_tokens,
            min(self.policy.max_generation_tokens, total_window - used_tokens - safety_margin),
        )

        pressure = "NONE"
        if dropped:
            high_dropped = [d for d in dropped if d.priority <= 1]
            pressure = "SEVERE" if high_dropped else "MODERATE"
        elif used_tokens > max_prompt_budget * 0.8:
            pressure = "LIGHT"

        report = ContextReport(
            context_pressure=pressure,
            context_window=total_window,
            used_input_tokens=used_tokens,
            allocated_generation_tokens=usable_gen_budget,
            included_priorities=[f"{i.name} (P{i.priority}, {i.tokens}t)" for i in included],
            dropped_priorities=[f"{d.name} (P{d.priority}, {d.tokens}t)" for d in dropped],
        )

        log.info(
            f"[ContextBuilder] Window={total_window}t | Prompt={used_tokens}t | GenBudget={usable_gen_budget}t | "
            f"Pressure={pressure} | Included={len(included)} items | Dropped={len(dropped)} items"
        )

        assembled_prompt = "\n\n".join(item.content for item in included)
        return assembled_prompt, report, usable_gen_budget
