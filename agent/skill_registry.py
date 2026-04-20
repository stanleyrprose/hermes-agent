"""Central registry for all Hermes Agent skills.

Mirrors the ``ToolRegistry`` pattern (tools/registry.py) but for skills,
which are agent-level resources rather than tool-level ones.

Architecture
============
``SkillRegistry`` is a **process-global in-memory cache** populated at startup
by registration calls from three sources::

    bundled sync  → register_bundled_skills()   [source="bundled"]
    hub install   → register_hub_skills()       [source="hub"]
    user created  → register_user_skills()       [source="user"]
    external dirs → register_external_skills()    [source="external"]

Local (user) skills take precedence over same-named bundled/hub skills.
External-dir skills have lowest priority.

The registry does NOT own the filesystem scan logic — that lives in
``agent/skill_utils`` (snapshot cache) and ``tools/skills_tool``
(user-facing tools).  The registry is populated by those modules when
they add/remove skills, and is queried by prompt_builder and the CLI.

Import invariant: this module intentionally avoids importing the tool registry,
CLI config, or any heavy dependency chain.  It is safe to import at module
level without triggering tool registration or provider resolution.
"""

from __future__ import annotations

import logging
import threading
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ── Enum ──────────────────────────────────────────────────────────────────────


class SkillReadinessStatus(str, Enum):
    """Skill readiness states — mirrors tools/skills_tool.SkillReadinessStatus."""

    AVAILABLE = "available"
    SETUP_NEEDED = "setup_needed"
    UNSUPPORTED = "unsupported"


# ── Data class ────────────────────────────────────────────────────────────────


class SkillEntry:
    """Metadata for a single registered skill.

    Attributes
    ----------
    name : str
        Unique skill identifier (slug, e.g. ``"axolotl"``).
    source : str
        Provenance: ``"bundled"``, ``"hub"``, ``"user"``, or ``"external"``.
    path : Path
        Absolute path to the skill's ``SKILL.md`` file.
    category : str | None
        Category inferred from directory path (e.g. ``"mlops"``).
    description : str
        Human-readable short description.
    frontmatter : dict
        Raw parsed YAML frontmatter from the SKILL.md file.
    check_fn : Callable | None
        Runtime availability check.  Returns True when the skill can be used.
        ``None`` means always available.
    requires_tools : list[str]
        Tool names this skill requires to be useful
        (from ``metadata.hermes.requires_tools``).
    fallback_for_tools : list[str]
        Tool names this skill serves as a fallback for
        (from ``metadata.hermes.fallback_for_tools``).
    parameters_schema : dict | None
        Optional OpenAI-compatible ``parameters`` object describing the skill's
        accepted arguments (from ``parameters`` frontmatter field).
    readiness : SkillReadinessStatus
        Current readiness: AVAILABLE, SETUP_NEEDED, or UNSUPPORTED.
    metadata : dict
        Arbitrary additional metadata from frontmatter (tags, related_skills,
        hermes config vars, etc.).
    """

    __slots__ = (
        "name",
        "source",
        "path",
        "category",
        "description",
        "frontmatter",
        "check_fn",
        "requires_tools",
        "fallback_for_tools",
        "requires_toolsets",
        "fallback_for_toolsets",
        "skillsets",
        "parameters_schema",
        "readiness",
        "metadata",
    )

    def __init__(
        self,
        name: str,
        *,
        source: str,
        path: Path,
        category: Optional[str] = None,
        description: str = "",
        frontmatter: Optional[dict] = None,
        check_fn: Optional[Callable] = None,
        requires_tools: Optional[List[str]] = None,
        fallback_for_tools: Optional[List[str]] = None,
        requires_toolsets: Optional[List[str]] = None,
        fallback_for_toolsets: Optional[List[str]] = None,
        skillsets: Optional[List[str]] = None,
        parameters_schema: Optional[dict] = None,
        readiness: SkillReadinessStatus = SkillReadinessStatus.AVAILABLE,
        metadata: Optional[dict] = None,
    ):
        self.name = name
        self.source = source
        self.path = Path(path)
        self.category = category
        self.description = description
        self.frontmatter = frontmatter or {}
        self.check_fn = check_fn
        self.requires_tools = requires_tools or []
        self.fallback_for_tools = fallback_for_tools or []
        self.requires_toolsets = requires_toolsets or []
        self.fallback_for_toolsets = fallback_for_toolsets or []
        self.skillsets = skillsets or []
        self.parameters_schema = parameters_schema
        self.readiness = readiness
        self.metadata = metadata or {}

    # ── Convenience constructors ───────────────────────────────────────────────

    @classmethod
    def from_frontmatter(
        cls,
        name: str,
        path: Path,
        frontmatter: dict,
        source: str,
        description: str = "",
        category: Optional[str] = None,
    ) -> "SkillEntry":
        """Build a SkillEntry from parsed frontmatter.

        Extracts requires_tools / fallback_for_tools / parameters_schema /
        check_fn / metadata from the frontmatter dict.
        """
        import os
        import re

        # ── check_fn ──────────────────────────────────────────────────────
        check_fn: Optional[Callable] = None
        check_block = frontmatter.get("check") or frontmatter.get("checks")
        if check_block and isinstance(check_block, dict):
            env_vars = check_block.get("env_vars") or []
            commands = check_block.get("commands") or []

            def _make_check(
                _env_vars: list = env_vars,
                _commands: list = commands,
            ) -> bool:
                # env var check
                for var in _env_vars:
                    if not os.getenv(str(var)):
                        return False
                # command check
                for cmd in _commands:
                    import shutil

                    if not shutil.which(cmd):
                        return False
                return True

            check_fn = _make_check

        # ── dependencies ───────────────────────────────────────────────────
        hermes_meta = frontmatter.get("metadata", {}).get("hermes", {}) or {}
        requires_tools = hermes_meta.get("requires_tools", []) or []
        fallback_for_tools = hermes_meta.get("fallback_for_tools", []) or []
        requires_toolsets = hermes_meta.get("requires_toolsets", []) or []
        fallback_for_toolsets = hermes_meta.get("fallback_for_toolsets", []) or []

        # ── skillsets ─────────────────────────────────────────────────────
        # Skillsets are groups of related skills (analogous to toolsets for tools)
        # Can be declared in metadata.hermes.skillsets or at top level
        skillsets = hermes_meta.get("skillsets", []) or frontmatter.get("skillsets", []) or []

        # Legacy top-level prerequisites.env_vars
        legacy_env_vars: List[str] = []
        prereqs = frontmatter.get("prerequisites")
        if prereqs and isinstance(prereqs, dict):
            raw = prereqs.get("env_vars")
            if isinstance(raw, list):
                legacy_env_vars = [str(v) for v in raw if v]
            elif isinstance(raw, str):
                legacy_env_vars = [raw]

        # If check_fn is set via check.env_vars, also require those env vars
        # (these are the same thing expressed two ways)
        if check_fn is None and legacy_env_vars:
            _env_vars = legacy_env_vars

            def _check_from_prereqs(_ev: list = _env_vars) -> bool:
                return all(os.getenv(str(v)) for v in _ev)

            check_fn = _check_from_prereqs

        # ── parameters schema ───────────────────────────────────────────────
        parameters_schema: Optional[dict] = None
        raw_params = frontmatter.get("parameters")
        if raw_params and isinstance(raw_params, list):
            properties: Dict[str, Any] = {}
            required: List[str] = []
            for p in raw_params:
                if not isinstance(p, dict):
                    continue
                p_name = str(p.get("name") or "")
                if not p_name:
                    continue
                p_type = str(p.get("type", "string"))
                p_desc = str(p.get("description") or "")
                default = p.get("default")
                properties[p_name] = {"type": p_type, "description": p_desc}
                if default is None:
                    required.append(p_name)
                else:
                    properties[p_name]["default"] = default
            if properties:
                parameters_schema = {
                    "type": "object",
                    "properties": properties,
                    "required": required if required else None,
                }

        # ── metadata ───────────────────────────────────────────────────────
        # Pull out tags, related_skills, hermes config into metadata
        metadata: Dict[str, Any] = {}
        hermes = dict(hermes_meta)
        if hermes:
            metadata["hermes"] = hermes
        tags = frontmatter.get("tags")
        if tags:
            metadata["tags"] = tags
        related = frontmatter.get("related_skills")
        if related:
            metadata["related_skills"] = related

        # ── category ───────────────────────────────────────────────────────
        if category is None:
            category = hermes_meta.get("category")

        # ── readiness ──────────────────────────────────────────────────────
        # SkillReadinessStatus is an enum; use it directly
        readiness = SkillReadinessStatus.AVAILABLE

        return cls(
            name=name,
            source=source,
            path=path,
            category=category,
            description=description,
            frontmatter=frontmatter,
            check_fn=check_fn,
            requires_tools=requires_tools,
            fallback_for_tools=fallback_for_tools,
            requires_toolsets=requires_toolsets,
            fallback_for_toolsets=fallback_for_toolsets,
            skillsets=skillsets,
            parameters_schema=parameters_schema,
            readiness=readiness,
            metadata=metadata,
        )

    def to_dict(self) -> dict:
        """Serialize to a plain dict (used by snapshot)."""
        return {
            "name": self.name,
            "source": self.source,
            "category": self.category,
            "description": self.description,
            "requires_tools": self.requires_tools,
            "fallback_for_tools": self.fallback_for_tools,
            "requires_toolsets": self.requires_toolsets,
            "fallback_for_toolsets": self.fallback_for_toolsets,
            "skillsets": self.skillsets,
            "readiness": self.readiness.value,
            "metadata": self.metadata,
        }

    def to_openai_schema(self) -> dict:
        """Return an OpenAI-compatible function-call schema.

        Only includes a schema when ``parameters_schema`` is set.
        """
        base = {
            "name": self.name,
            "description": self.description,
        }
        if self.parameters_schema:
            base["parameters"] = self.parameters_schema
        return base


# ── Registry ───────────────────────────────────────────────────────────────────


class SkillRegistry:
    """Process-global singleton that holds all registered skill metadata.

    Thread-safe via an internal ``_lock`` (shared-namespace ``threading.RLock``).

    The registry is **not** responsible for filesystem scanning — that is done
    by ``agent.skill_utils`` and ``tools.skills_tool``.  This class is an
    in-memory index that those modules populate at startup and when skills are
    installed / uninstalled / edited.
    """

    __slots__ = ("_skills", "_by_category", "_by_source", "_by_skillset", "_lock")

    def __init__(self):
        self._skills: Dict[str, SkillEntry] = {}
        # Secondary indices (maintained in sync with _skills)
        self._by_category: Dict[str, Set[str]] = {}  # category → {skill_names}
        self._by_source: Dict[str, Set[str]] = {}  # source → {skill_names}
        self._by_skillset: Dict[str, Set[str]] = {}  # skillset → {skill_names}
        self._lock = threading.RLock()

    # ── Registration ─────────────────────────────────────────────────────────

    def register(self, entry: SkillEntry) -> None:
        """Add / overwrite a skill entry.  Thread-safe."""
        if not isinstance(entry, SkillEntry):
            raise TypeError(f"expected SkillEntry, got {type(entry).__name__}")
        with self._lock:
            old = self._skills.get(entry.name)
            self._skills[entry.name] = entry
            self._reindex(entry, remove_old=old)

    def deregister(self, name: str) -> None:
        """Remove a skill by name.  Thread-safe.  Silently succeeds if absent."""
        with self._lock:
            old = self._skills.pop(name, None)
            if old is not None:
                self._deindex(old)

    def _reindex(self, entry: SkillEntry, remove_old: Optional[SkillEntry] = None) -> None:
        """Update secondary indices after a register."""
        if remove_old is not None:
            self._deindex(remove_old)
        # by category
        cat = entry.category
        if cat:
            if cat not in self._by_category:
                self._by_category[cat] = set()
            self._by_category[cat].add(entry.name)
        # by source
        src = entry.source
        if src:
            if src not in self._by_source:
                self._by_source[src] = set()
            self._by_source[src].add(entry.name)
        # by skillset
        for ss in entry.skillsets:
            if ss not in self._by_skillset:
                self._by_skillset[ss] = set()
            self._by_skillset[ss].add(entry.name)

    def _deindex(self, entry: SkillEntry) -> None:
        """Remove an entry from secondary indices."""
        cat = entry.category
        if cat and cat in self._by_category:
            self._by_category[cat].discard(entry.name)
            if not self._by_category[cat]:
                del self._by_category[cat]
        src = entry.source
        if src and src in self._by_source:
            self._by_source[src].discard(entry.name)
            if not self._by_source[src]:
                del self._by_source[src]
        # by skillset
        for ss in entry.skillsets:
            if ss in self._by_skillset:
                self._by_skillset[ss].discard(entry.name)
                if not self._by_skillset[ss]:
                    del self._by_skillset[ss]

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_skill(self, name: str) -> Optional[SkillEntry]:
        """Return the entry for *name*, or None."""
        return self._skills.get(name)

    def get_all_skills(self) -> List[SkillEntry]:
        """Return all entries sorted by name."""
        return sorted(self._skills.values(), key=lambda e: e.name)

    def get_skill_names(self) -> List[str]:
        """Return sorted list of all skill names."""
        return sorted(self._skills.keys())

    def get_categories(self) -> List[str]:
        """Return sorted list of known categories."""
        return sorted(self._by_category.keys())

    def get_skills_by_category(self, category: str) -> List[SkillEntry]:
        """Return skills in *category*, sorted by name."""
        names = self._by_category.get(category, set())
        return sorted(
            (self._skills[n] for n in names if n in self._skills),
            key=lambda e: e.name,
        )

    def get_skills_by_source(self, source: str) -> List[SkillEntry]:
        """Return skills from *source*, sorted by name."""
        names = self._by_source.get(source, set())
        return sorted(
            (self._skills[n] for n in names if n in self._skills),
            key=lambda e: e.name,
        )

    def get_source(self, name: str) -> Optional[str]:
        """Return the source of a skill, or None."""
        entry = self._skills.get(name)
        return entry.source if entry else None

    def get_categories_with_counts(self) -> Dict[str, int]:
        """Return {category: skill_count}, sorted by category name."""
        return {cat: len(names) for cat, names in sorted(self._by_category.items())}

    # ── Skillsets ─────────────────────────────────────────────────────────────

    def get_skillsets(self) -> List[str]:
        """Return sorted list of known skillsets."""
        return sorted(self._by_skillset.keys())

    def get_skills_by_skillset(self, skillset: str) -> List[SkillEntry]:
        """Return skills in *skillset*, sorted by name."""
        names = self._by_skillset.get(skillset, set())
        return sorted(
            (self._skills[n] for n in names if n in self._skills),
            key=lambda e: e.name,
        )

    def get_skillsets_with_counts(self) -> Dict[str, int]:
        """Return {skillset: skill_count}, sorted by skillset name."""
        return {ss: len(names) for ss, names in sorted(self._by_skillset.items())}

    # ── Availability ─────────────────────────────────────────────────────────

    def is_skill_available(self, name: str) -> bool:
        """Return True if the skill passes its check_fn (or has none).

        Returns False if the skill is not registered at all.
        """
        entry = self._skills.get(name)
        if entry is None:
            return False
        if entry.check_fn is None:
            return True
        try:
            return bool(entry.check_fn())
        except Exception:
            logger.debug("Skill %s check_fn raised; marking unavailable", name)
            return False

    def check_fn_for_skill(self, name: str) -> Optional[Callable]:
        """Return the check_fn for a skill, or None."""
        entry = self._skills.get(name)
        return entry.check_fn if entry else None

    def check_all_availability(self) -> Dict[str, SkillReadinessStatus]:
        """Return {skill_name: readiness} for every registered skill."""
        result: Dict[str, SkillReadinessStatus] = {}
        for name in self._skills:
            result[name] = self._compute_readiness(name)
        return result

    def _compute_readiness(self, name: str) -> SkillReadinessStatus:
        """Return the real-time readiness for a skill, honouring check_fn."""
        entry = self._skills.get(name)
        if entry is None:
            return SkillReadinessStatus.AVAILABLE  # vacuous — caller checks get_skill first
        if entry.readiness == SkillReadinessStatus.UNSUPPORTED:
            return SkillReadinessStatus.UNSUPPORTED
        return SkillReadinessStatus.AVAILABLE if self.is_skill_available(name) else SkillReadinessStatus.SETUP_NEEDED

    # ── Dependencies ─────────────────────────────────────────────────────────

    def get_skill_dependencies(self, name: str) -> dict:
        """Return {requires_tools, fallback_for_tools, requires_toolsets, fallback_for_toolsets}."""
        entry = self._skills.get(name)
        if entry is None:
            return {
                "requires_tools": [],
                "fallback_for_tools": [],
                "requires_toolsets": [],
                "fallback_for_toolsets": [],
            }
        return {
            "requires_tools": list(entry.requires_tools),
            "fallback_for_tools": list(entry.fallback_for_tools),
            "requires_toolsets": list(entry.requires_toolsets),
            "fallback_for_toolsets": list(entry.fallback_for_toolsets),
        }

    def get_skills_requiring_tool(self, tool_name: str) -> List[SkillEntry]:
        """Return all skills that list *tool_name* in requires_tools."""
        return sorted(
            (e for e in self._skills.values() if tool_name in e.requires_tools),
            key=lambda e: e.name,
        )

    def get_skills_fallback_for_tool(self, tool_name: str) -> List[SkillEntry]:
        """Return all skills that list *tool_name* in fallback_for_tools."""
        return sorted(
            (e for e in self._skills.values() if tool_name in e.fallback_for_tools),
            key=lambda e: e.name,
        )

    # ── OpenAI schema ────────────────────────────────────────────────────────

    def get_skill_definitions(
        self,
        names: Optional[Set[str]] = None,
    ) -> List[dict]:
        """Return OpenAI-compatible function schemas for named skills.

        If *names* is None, returns schemas for all skills that have a
        ``parameters_schema`` (i.e. skills that accept arguments).
        """
        if names is not None:
            entries = (self._skills[n] for n in names if n in self._skills)
        else:
            entries = (e for e in self._skills.values() if e.parameters_schema)
        return [
            {"type": "function", "function": e.to_openai_schema()}
            for e in sorted(entries, key=lambda x: x.name)
        ]

    # ── Dispatch ─────────────────────────────────────────────────────────────

    def dispatch(self, name: str, task_id: Optional[str] = None) -> dict:
        """Return the skill payload dict for *name* (for skill_view integration).

        Returns ``{"error": "...}`` if not found.
        """
        entry = self._skills.get(name)
        if entry is None:
            return {"error": f"No skill registered as '{name}'"}
        # Load the content from disk (the actual skill_view logic)
        try:
            content = entry.path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to read skill file %s: %s", entry.path, exc)
            return {"error": f"Could not read skill file: {exc}"}
        return {
            "name": entry.name,
            "source": entry.source,
            "path": str(entry.path),
            "category": entry.category,
            "description": entry.description,
            "frontmatter": entry.frontmatter,
            "content": content,
            "readiness": self._compute_readiness(name).value,
            "metadata": entry.metadata,
        }

    # ── Snapshot (compatible with prompt_builder) ───────────────────────────

    def build_snapshot(self) -> dict:
        """Build a snapshot dict compatible with prompt_builder's _load_skills_snapshot.

        The snapshot contains ``skills`` (list of skill entry dicts) and
        ``category_descriptions`` (empty here — filled by prompt_builder).
        """
        skills: List[dict] = []
        for entry in self.get_all_skills():
            # conditions block for _skill_should_show filtering
            conditions = {
                "requires_tools": entry.requires_tools,
                "fallback_for_tools": entry.fallback_for_tools,
                "requires_toolsets": entry.requires_toolsets,
                "fallback_for_toolsets": entry.fallback_for_toolsets,
                "skillsets": entry.skillsets,
            }
            skills.append(
                {
                    "skill_name": entry.name,
                    "frontmatter_name": entry.name,
                    "description": entry.description,
                    "category": entry.category or "general",
                    "platforms": entry.frontmatter.get("platforms", []),
                    "conditions": conditions,
                    "source": entry.source,
                    "readiness": self._compute_readiness(entry.name).value,
                }
            )
        return {
            "skills": skills,
            "category_descriptions": {},  # filled by prompt_builder
        }


# ── Module-level singleton ─────────────────────────────────────────────────────

registry = SkillRegistry()
