"""
brain/verification.py - Multi-tier VerificationEngine for Local Learning Engine.

Establishes strict hierarchical verification:
- Tier 1: EXECUTION_SUCCESS (process exit code 0 / script ran without throwing exception)
- Tier 2: APPLICATION_STATE_VERIFIED (objects/state reported by application verified for existence, non-zero volume, and geometry)
- Tier 3: TASK_REQUIREMENTS_VERIFIED (all user task requirements verified against plan & reported state)
- UNKNOWN: returned when observability is insufficient to objectively verify.

CRITICAL RULE: Process exit code 0 is NOT task success. The final VerificationResultRecord
reflects the strongest tier actually verified.
"""
from __future__ import annotations

import re
from typing import Any

from brain.learning_records import (
    VerificationResultRecord,
    VerificationStatus,
    VerificationTier,
)

REPORT_TAG = "MBUDDY_STATE:"
# Not geometry (cameras, lights, empties), or meant to be flat (a floor, a plane, a rug): never "collapsed"
_NOT_SOLID = re.compile(r"camera|light|lamp|sun|empty|speaker|probe|plane|floor|ground|rug|carpet|sheet|paper|text|curve")


class VerificationEngine:
    """Pluggable, multi-tier verification engine for execution attempts."""

    def verify(
        self,
        task: str,
        plan: str,
        execution_data: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> VerificationResultRecord:
        """Verifies task outcome given real execution evidence.

        execution_data dict expected keys:
        - "exit_code": int | None
        - "stdout": str
        - "stderr": str
        - "report": str (optional MBUDDY_STATE output)
        - "artifacts": list[str] (optional created files)
        """
        context = context or {}
        exit_code = execution_data.get("exit_code")
        stdout = execution_data.get("stdout", "")
        stderr = execution_data.get("stderr", "")
        report = execution_data.get("report") or stdout
        artifacts = execution_data.get("artifacts", [])

        # Step 1: Check Tier 1 — Execution Success
        if exit_code is not None and exit_code != 0:
            return VerificationResultRecord(
                status=VerificationStatus.VERIFIED_FAILURE,
                tier=VerificationTier.EXECUTION_SUCCESS,
                details=f"Execution process exited with non-zero code {exit_code}.",
                missing_requirements=["Process exit code 0"],
            )

        if not report.strip() and not artifacts:
            return VerificationResultRecord(
                status=VerificationStatus.UNKNOWN,
                tier=VerificationTier.EXECUTION_SUCCESS,
                details="Process exited with code 0, but no output report or artifacts were produced.",
            )

        # Step 2: Check Tier 2 — Application State Verification
        lines = [ln for ln in report.splitlines() if ln.strip().startswith(REPORT_TAG)]
        names, solid = [], []
        for ln in lines:
            # Format: MBUDDY_STATE: Object [TYPE] (dx, dy, dz) at (x, y, z). The name is the first word, the type the
            # second (live 2026-10-20: "camera CAMERA" was read as one name, so cameras failed as collapsed geometry).
            m = re.match(r"^\s*" + re.escape(REPORT_TAG) + r"\s*([^\s(]+)(?:\s+([A-Z_]+)\b)?", ln)
            name = (m.group(1) if m else "").lower()
            kind = ((m.group(2) or "") if m else "").upper()
            names.append(name)
            solid.append(bool(name) and not _NOT_SOLID.search(name) and kind in ("", "MESH"))

        # Zero-volume check on application state
        collapsed_objects = []
        for n, ln, body in zip(names, lines, solid):
            m_dim = re.search(r"\(([\d.]+),\s*([\d.]+),\s*([\d.]+)\)\s*at", ln)
            if m_dim and body:
                dx, dy, dz = float(m_dim.group(1)), float(m_dim.group(2)), float(m_dim.group(3))
                if dx < 0.01 or dy < 0.01 or dz < 0.01:
                    collapsed_objects.append(f"{n} ({dx:.2f}, {dy:.2f}, {dz:.2f})")

        if collapsed_objects:
            return VerificationResultRecord(
                status=VerificationStatus.VERIFIED_FAILURE,
                tier=VerificationTier.APPLICATION_STATE_VERIFIED,
                details=f"Application state contains collapsed zero-volume geometry: {', '.join(collapsed_objects)}.",
                missing_requirements=["Non-zero volume 3D geometry"],
            )

        # Step 2b: Spatial Bounding Box Checks (Blender specific)
        objects_bbox = {}
        spatial_errors = []
        for n, ln, body in zip(names, lines, solid):
            m_dim = re.search(r"\(([\d.]+),\s*([\d.]+),\s*([\d.]+)\)\s*at\b", ln)
            m_pos = re.search(r"\bat\s*\(([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\)", ln)
            if m_dim and m_pos:
                dx, dy, dz = float(m_dim.group(1)), float(m_dim.group(2)), float(m_dim.group(3))
                x, y, z = float(m_pos.group(1)), float(m_pos.group(2)), float(m_pos.group(3))
                objects_bbox[n] = {'z_min': z - dz / 2, 'z_max': z + dz / 2, 'z': z, 'x': x, 'y': y, 'dx': dx, 'dy': dy}
                
                # Check for flat or collapsed objects
                if body:
                    if dx < 0.01 or dy < 0.01 or dz < 0.01:
                        spatial_errors.append(f"The {n} object is collapsed or flat ({dx:.2f}, {dy:.2f}, {dz:.2f}). Give it real 3D volume")
                
                # Check for untouched default primitives (cube at origin)
                if n == "cube" and dx == 2.0 and dy == 2.0 and dz == 2.0 and x == 0.0 and y == 0.0 and z == 0.0:
                    spatial_errors.append(f"The scene has an uncleaned default primitive: {n}. Remove or transform it")

        legs = {k: v for k, v in objects_bbox.items() if "leg" in k}
        tops = {k: v for k, v in objects_bbox.items() if ("top" in k or "table" in k or "seat" in k) and "leg" not in k}
        
        for name, bbox in legs.items():
            if abs(bbox['z_min']) > 0.15:
                spatial_errors.append(f"{name} is not touching the ground (z_min={bbox['z_min']:.2f})")
                
        if len(legs) > 1:
            leg_positions = set()
            for leg_name, leg_bbox in legs.items():
                pos = (round(leg_bbox['x'], 2), round(leg_bbox['y'], 2))
                if pos in leg_positions:
                    spatial_errors.append(f"Multiple legs are placed at the exact same X/Y position {pos}. They must be spaced out at corners.")
                    break
                leg_positions.add(pos)
                
        if tops and legs:
            for leg_name, leg_bbox in legs.items():
                # Each leg belongs to the top nearest it, measured from that top's centre (live 2026-10-20: with a
                # chair and a table, the table's legs were judged against the chair's seat, as if it sat at 0,0).
                top_name, top_bbox = min(tops.items(), key=lambda t: (t[1]['x'] - leg_bbox['x']) ** 2
                                                                     + (t[1]['y'] - leg_bbox['y']) ** 2)
                if top_bbox['z_min'] > leg_bbox['z_max'] + 0.15 or top_bbox['z_min'] < leg_bbox['z_max'] - 0.15:
                    spatial_errors.append(f"{top_name} (z_min={top_bbox['z_min']:.2f}) is not resting properly on {leg_name} (z_max={leg_bbox['z_max']:.2f})")
                    break
                # Check if legs are completely outside the table top
                if abs(leg_bbox['x'] - top_bbox['x']) > (top_bbox['dx'] / 2 + 0.15) \
                        or abs(leg_bbox['y'] - top_bbox['y']) > (top_bbox['dy'] / 2 + 0.15):
                    spatial_errors.append(f"{leg_name} (x={leg_bbox['x']:.2f}, y={leg_bbox['y']:.2f}) is placed completely outside the bounds of {top_name} (width={top_bbox['dx']:.2f}, depth={top_bbox['dy']:.2f}).")
                    break

        if spatial_errors:
            return VerificationResultRecord(
                status=VerificationStatus.VERIFIED_FAILURE,
                tier=VerificationTier.APPLICATION_STATE_VERIFIED,
                details=f"Spatial structure errors detected: {', '.join(spatial_errors)}.",
                missing_requirements=["Structurally sound spatial arrangement"],
            )

        # Step 3: Check Tier 3 — Task Requirements Verification against Plan
        missing_requirements = []
        verified_elements = []

        if plan and plan.strip():
            # Extract planned items e.g., "- 4 legs", "- 1 backrest"
            pattern = r"-\s*(?:(\d+)\s+)?([a-zA-Z0-9_\s]+)"
            for m in re.finditer(pattern, plan):
                count_str = m.group(1)
                item_name = m.group(2).strip().lower()
                need_count = int(count_str) if count_str else 1
                # A dimension line ("- 600 mm", "- 0.6 m tall") is not a part to find.
                if not re.search(r"[a-z]{3,}", item_name) or need_count > 50:
                    continue

                # Remove basic plural 's' for looser matching
                search_key = item_name[:-1] if item_name.endswith('s') and not item_name.endswith(('ss', 'us', 'is')) else item_name
                
                # Count matches in names or artifacts
                found_count = sum(1 for n in names if search_key in n or n in search_key)
                if not found_count and artifacts:
                    found_count = sum(1 for a in artifacts if search_key in a.lower())

                if found_count >= need_count:
                    verified_elements.append(f"{item_name} ({found_count}/{need_count})")
                else:
                    missing_requirements.append(f"{item_name} (found {found_count}/{need_count})")

        if missing_requirements:
            return VerificationResultRecord(
                status=VerificationStatus.PARTIAL_SUCCESS if verified_elements else VerificationStatus.VERIFIED_FAILURE,
                tier=VerificationTier.TASK_REQUIREMENTS_VERIFIED,
                details=f"Missing requested task items: {', '.join(missing_requirements)}.",
                missing_requirements=missing_requirements,
                verified_elements=verified_elements,
            )

        # All tiers passed
        tier = VerificationTier.TASK_REQUIREMENTS_VERIFIED if plan.strip() else (
            VerificationTier.APPLICATION_STATE_VERIFIED if names else VerificationTier.EXECUTION_SUCCESS
        )

        return VerificationResultRecord(
            status=VerificationStatus.VERIFIED_SUCCESS,
            tier=tier,
            details="All executable, application state, and task plan requirements verified successfully.",
            verified_elements=verified_elements or names or ["Execution finished"],
        )
