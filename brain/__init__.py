"""
brain — Personality, LLM routing, and conversation context.
"""
from .personality import (
    IDENTITY_CORE,
    EMOTION_TAG_TO_STATE, STATE_TO_TAG, EMOTION_CONSTRAINTS,
    WAKE_GREETINGS, SOFT_GOODBYE_TABLE, FINAL_GOODBYE_TABLE,
    get_hard_constraint, strip_emotion_tags, extract_emotion_state,
    strip_for_tts,
)
from .llm_router import ask_mbuddy_stream
from .llm_bridge import llm_chat_create, llm_client
from .brain_adapters import get_brain_adapter
from .companion_director import CompanionCue, choose_inner_life_cue
from .cognitive_state import CognitiveState, BehaviorDecision, build_cognitive_state, decide_behavior
from .capability_registry import Capability, CapabilityRegistry, build_capability_registry
from .emotion_authority import EmotionDecision, validate_emotion_decision
from .memory_context import build_tiered_memory_context, build_conversation_system
from .response_authenticity import build_authenticity_context
from .world_model import WorldModel, build_world_model

__all__ = [
    # Personality / identity
    "IDENTITY_CORE",
    "EMOTION_TAG_TO_STATE", "STATE_TO_TAG", "EMOTION_CONSTRAINTS",
    "WAKE_GREETINGS", "SOFT_GOODBYE_TABLE", "FINAL_GOODBYE_TABLE",
    "get_hard_constraint", "strip_emotion_tags", "extract_emotion_state",
    "strip_for_tts",
    # LLM routing
    "get_brain_adapter", "ask_mbuddy_stream", "llm_chat_create", "llm_client",
    "CompanionCue", "choose_inner_life_cue",
    "CognitiveState", "BehaviorDecision", "build_cognitive_state", "decide_behavior",
    "Capability", "CapabilityRegistry", "build_capability_registry",
    "EmotionDecision", "validate_emotion_decision",
    "WorldModel", "build_world_model", "build_authenticity_context",
    # Conversation context builders
    "build_tiered_memory_context", "build_conversation_system",
]
