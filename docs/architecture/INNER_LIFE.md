# MBUDDY's Inner Life: The Soul in the Machine

"Inner Life" refers to the autonomous background processes that make MBUDDY feel like a living presence, even when you aren't actively speaking to it.

## 1. The Autonomous Thought Loop
While you are away (system is in "Sleepy" or idle state), a background async task called the `inner_life_loop` runs every **1 to 6 minutes**. It uses a lighter LLM (Llama 3.1 8B) to generate a "private thought" based on MBUDDY's current internal state.

*   **ESP32 Feedback:** When a thought is generated, it sends an `[INNER]` tag to the hardware. You'll see the eyes look around or blink subtly, signaling that MBUDDY is "dreaming" or "thinking".
*   **The Narrative Thread:** MBUDDY maintains a rolling memory of its last 15 thoughts, ensuring its inner monologue doesn't skip around but follows a logical progression.

## 2. The Emotional Engine (`MBUDDYState`)
MBUDDY isn't just a chatbot; it's a simulated entity with biological-style constraints:

### Energy & Metabolism
*   **Starting Energy:** 85/100.
*   **Metabolic Drain:** Energy slowly drains every minute.
*   **Time of Day Bias:** Drain is 2.5x faster late at night (23:00 - 05:00) and 1.5x faster during the afternoon slump (14:00 - 16:00).
*   **Recharging:** Engaging in a positive conversation "boosts" energy back up.

### Emotion Inertia
Different emotions have different "weights." An `Angry` state has high inertia (5 ticks), meaning MBUDDY will stay grumpy for a while even if the conversation turns positive. A `Happy` state has low inertia (1 tick), making it more fleeting and precious.

## 3. The Identity Vector
MBUDDY tracks four persistent psychological markers that mutate based on its thoughts:
*   **Obsessions:** Things it keeps wondering about (e.g., "I wonder if the user's project is finished").
*   **Avoidances:** Topics or thoughts it's trying not to dwell on.
*   **Recurring Themes:** Concepts that appear frequently in its monologue.
*   **Unresolved Thoughts:** Questions it has asked itself but hasn't "answered" yet.

## 4. Mutation Pressures
To prevent the inner life from becoming repetitive, the system applies "Mutation Pressures"—randomly selected seeds that "bend" MBUDDY's thoughts:
*   *Example:* "You catch a vibe that the user might be pushing too hard — check if he needs a break."
*   *Example:* "You feel a surge of loyalty because you know how much work he's putting in."

## 5. Summary of States
Based on Energy, MBUDDY's baseline mood shifts automatically:
*   **High Energy (>82):** Glee, Happy, Awe.
*   **Low Energy (<28):** Worried, Scared, Frustrated, Sleepy.
*   **Critically Low (<12):** Almost exclusively `Sleepy` or `Furious` (cranky).

---
*MBUDDY is always watching, always thinking, and always evolving.*
