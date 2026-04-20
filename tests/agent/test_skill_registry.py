"""Tests for agent/skill_registry.py"""

import os
import tempfile
from pathlib import Path

import pytest

from agent.skill_registry import (
    SkillEntry,
    SkillRegistry,
    SkillReadinessStatus,
    registry as module_registry,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def empty_reg():
    """Fresh SkillRegistry instance per test."""
    return SkillRegistry()


@pytest.fixture
def skill_path(tmp_path):
    """A temporary SKILL.md file."""
    p = tmp_path / "SKILL.md"
    p.write_text("---\nname: test\n---\nTest content", encoding="utf-8")
    return p


# ── SkillEntry tests ───────────────────────────────────────────────────────────

class TestSkillEntryInit:
    def test_minimal_entry(self, skill_path):
        e = SkillEntry(name="x", source="user", path=skill_path)
        assert e.name == "x"
        assert e.source == "user"
        assert e.path == skill_path
        assert e.category is None
        assert e.description == ""
        assert e.check_fn is None
        assert e.requires_tools == []
        assert e.fallback_for_tools == []
        assert e.parameters_schema is None
        assert e.readiness == SkillReadinessStatus.AVAILABLE
        assert e.metadata == {}

    def test_full_entry(self, skill_path):
        e = SkillEntry(
            name="axolotl",
            source="bundled",
            path=skill_path,
            category="mlops",
            description="Fine-tune LLMs",
            frontmatter={"name": "axolotl"},
            check_fn=lambda: True,
            requires_tools=["execute_code"],
            fallback_for_tools=["openai"],
            parameters_schema={"type": "object", "properties": {}},
            readiness=SkillReadinessStatus.SETUP_NEEDED,
            metadata={"tags": ["fine-tuning"]},
        )
        assert e.name == "axolotl"
        assert e.category == "mlops"
        assert e.requires_tools == ["execute_code"]
        assert e.fallback_for_tools == ["openai"]
        assert e.readiness == SkillReadinessStatus.SETUP_NEEDED
        assert e.metadata["tags"] == ["fine-tuning"]


class TestSkillEntryFromFrontmatter:
    def test_requires_tools_extraction(self, skill_path):
        fm = {
            "name": "test",
            "description": "Test",
            "metadata": {
                "hermes": {
                    "requires_tools": ["tool_a", "tool_b"],
                    "fallback_for_tools": ["openai"],
                }
            },
        }
        e = SkillEntry.from_frontmatter(
            name="test",
            path=skill_path,
            frontmatter=fm,
            source="bundled",
        )
        assert e.requires_tools == ["tool_a", "tool_b"]
        assert e.fallback_for_tools == ["openai"]

    def test_parameters_schema(self, skill_path):
        fm = {
            "name": "test",
            "parameters": [
                {"name": "model", "type": "string", "description": "Model name", "default": " llama"},
                {"name": "epochs", "type": "integer", "description": "Epochs"},
            ],
        }
        e = SkillEntry.from_frontmatter(
            name="test",
            path=skill_path,
            frontmatter=fm,
            source="user",
        )
        assert e.parameters_schema is not None
        assert e.parameters_schema["type"] == "object"
        assert "model" in e.parameters_schema["properties"]
        assert "epochs" in e.parameters_schema["properties"]
        # epochs has no default, so should be required
        assert "epochs" in e.parameters_schema["required"]
        # model has default, so should NOT be required
        assert "model" not in e.parameters_schema["required"]

    def test_check_fn_from_prerequisites(self, skill_path):
        fm = {
            "name": "test",
            "prerequisites": {"env_vars": ["MY_API_KEY"]},
        }
        os.environ.pop("MY_API_KEY", None)
        e = SkillEntry.from_frontmatter(
            name="test",
            path=skill_path,
            frontmatter=fm,
            source="user",
        )
        assert e.check_fn is not None
        assert e.check_fn() is False

        os.environ["MY_API_KEY"] = "1"
        assert e.check_fn() is True
        del os.environ["MY_API_KEY"]

    def test_check_fn_from_check_block(self, skill_path):
        fm = {
            "name": "test",
            "check": {"commands": ["ls"]},  # only command check — env_vars tested separately
        }
        e = SkillEntry.from_frontmatter(
            name="test",
            path=skill_path,
            frontmatter=fm,
            source="user",
        )
        assert e.check_fn is not None
        # command 'ls' always exists
        assert e.check_fn() is True

    def test_check_fn_from_check_block_env_var_fails(self, skill_path):
        """Env vars in check block must be set for check_fn to pass."""
        fm = {
            "name": "test",
            "check": {"env_vars": ["MY_MISSING_API_KEY"], "commands": ["ls"]},
        }
        e = SkillEntry.from_frontmatter(
            name="test",
            path=skill_path,
            frontmatter=fm,
            source="user",
        )
        assert e.check_fn is not None
        # env var not set → False
        assert e.check_fn() is False

    def test_platforms_passed_through(self, skill_path):
        fm = {
            "name": "test",
            "platforms": ["linux"],
        }
        e = SkillEntry.from_frontmatter(
            name="test",
            path=skill_path,
            frontmatter=fm,
            source="bundled",
        )
        assert e.frontmatter["platforms"] == ["linux"]

    def test_hermes_metadata_preserved(self, skill_path):
        fm = {
            "name": "test",
            "metadata": {"hermes": {"category": "ml"}},
        }
        e = SkillEntry.from_frontmatter(
            name="test",
            path=skill_path,
            frontmatter=fm,
            source="hub",
        )
        assert e.metadata["hermes"]["category"] == "ml"


class TestSkillEntryToDict:
    def test_round_trip(self, skill_path):
        fm = {
            "name": "test",
            "description": "A test skill",
            "metadata": {"hermes": {"requires_tools": ["execute_code"]}},
        }
        e = SkillEntry.from_frontmatter(
            name="test",
            path=skill_path,
            frontmatter=fm,
            source="user",
            description="A test skill",
            category="testing",
        )
        d = e.to_dict()
        assert d["name"] == "test"
        assert d["source"] == "user"
        assert d["category"] == "testing"
        assert d["requires_tools"] == ["execute_code"]


class TestSkillEntryOpenAICloudSchema:
    def test_no_schema(self, skill_path):
        e = SkillEntry(name="x", source="user", path=skill_path)
        schema = e.to_openai_schema()
        assert schema["name"] == "x"
        assert "parameters" not in schema

    def test_with_schema(self, skill_path):
        e = SkillEntry(
            name="x",
            source="user",
            path=skill_path,
            parameters_schema={"type": "object", "properties": {"a": {"type": "string"}}},
        )
        schema = e.to_openai_schema()
        assert schema["name"] == "x"
        assert "parameters" in schema


# ── SkillRegistry tests ────────────────────────────────────────────────────────

class TestSkillRegistryRegister:
    def test_register_adds_entry(self, empty_reg, skill_path):
        e = SkillEntry(name="x", source="user", path=skill_path)
        empty_reg.register(e)
        assert empty_reg.get_skill("x") is e

    def test_register_overwrites(self, empty_reg, skill_path):
        e1 = SkillEntry(name="x", source="user", path=skill_path)
        e2 = SkillEntry(name="x", source="bundled", path=skill_path)
        empty_reg.register(e1)
        empty_reg.register(e2)
        assert empty_reg.get_skill("x") is e2
        assert empty_reg.get_source("x") == "bundled"

    def test_register_rejects_non_entry(self, empty_reg, skill_path):
        with pytest.raises(TypeError):
            empty_reg.register({"name": "x"})  # type: ignore

    def test_deregister_removes(self, empty_reg, skill_path):
        e = SkillEntry(name="x", source="user", path=skill_path)
        empty_reg.register(e)
        empty_reg.deregister("x")
        assert empty_reg.get_skill("x") is None

    def test_deregister_missing_is_noop(self, empty_reg):
        empty_reg.deregister("does-not-exist")  # no crash


class TestSkillRegistryQueries:
    def test_get_all_skills_sorted(self, empty_reg, skill_path):
        for name in ["z-skill", "a-skill", "m-skill"]:
            empty_reg.register(
                SkillEntry(name=name, source="user", path=skill_path)
            )
        names = [e.name for e in empty_reg.get_all_skills()]
        assert names == ["a-skill", "m-skill", "z-skill"]

    def test_get_skill_names(self, empty_reg, skill_path):
        for name in ["z", "a"]:
            empty_reg.register(SkillEntry(name=name, source="user", path=skill_path))
        assert empty_reg.get_skill_names() == ["a", "z"]

    def test_get_categories(self, empty_reg, skill_path):
        empty_reg.register(
            SkillEntry(name="x", source="user", path=skill_path, category="ml")
        )
        empty_reg.register(
            SkillEntry(name="y", source="user", path=skill_path, category="dev")
        )
        assert empty_reg.get_categories() == ["dev", "ml"]

    def test_get_skills_by_category(self, empty_reg, skill_path):
        for cat, name in [("ml", "a"), ("ml", "b"), ("dev", "c")]:
            empty_reg.register(
                SkillEntry(name=name, source="user", path=skill_path, category=cat)
            )
        ml = empty_reg.get_skills_by_category("ml")
        assert [e.name for e in ml] == ["a", "b"]

    def test_get_skills_by_source(self, empty_reg, skill_path):
        empty_reg.register(
            SkillEntry(name="x", source="bundled", path=skill_path)
        )
        empty_reg.register(
            SkillEntry(name="y", source="user", path=skill_path)
        )
        bundled = empty_reg.get_skills_by_source("bundled")
        assert [e.name for e in bundled] == ["x"]

    def test_get_categories_with_counts(self, empty_reg, skill_path):
        for cat, name in [("ml", "a"), ("ml", "b"), ("dev", "c")]:
            empty_reg.register(
                SkillEntry(name=name, source="user", path=skill_path, category=cat)
            )
        counts = empty_reg.get_categories_with_counts()
        assert counts == {"dev": 1, "ml": 2}


class TestSkillRegistryAvailability:
    def test_available_no_check_fn(self, empty_reg, skill_path):
        e = SkillEntry(name="x", source="user", path=skill_path)
        empty_reg.register(e)
        assert empty_reg.is_skill_available("x") is True

    def test_available_with_check_fn_true(self, empty_reg, skill_path):
        e = SkillEntry(
            name="x",
            source="user",
            path=skill_path,
            check_fn=lambda: True,
        )
        empty_reg.register(e)
        assert empty_reg.is_skill_available("x") is True

    def test_available_with_check_fn_false(self, empty_reg, skill_path):
        e = SkillEntry(
            name="x",
            source="user",
            path=skill_path,
            check_fn=lambda: False,
        )
        empty_reg.register(e)
        assert empty_reg.is_skill_available("x") is False

    def test_available_check_fn_raises(self, empty_reg, skill_path):
        e = SkillEntry(
            name="x",
            source="user",
            path=skill_path,
            check_fn=lambda: 1 / 0,
        )
        empty_reg.register(e)
        assert empty_reg.is_skill_available("x") is False

    def test_available_unknown_skill(self, empty_reg):
        assert empty_reg.is_skill_available("unknown") is False

    def test_check_all_availability(self, empty_reg, skill_path):
        empty_reg.register(
            SkillEntry(name="a", source="user", path=skill_path)
        )
        empty_reg.register(
            SkillEntry(
                name="b",
                source="user",
                path=skill_path,
                check_fn=lambda: False,
            )
        )
        avail = empty_reg.check_all_availability()
        assert avail["a"] == SkillReadinessStatus.AVAILABLE
        assert avail["b"] == SkillReadinessStatus.SETUP_NEEDED


class TestSkillRegistryDependencies:
    def test_get_skill_dependencies(self, empty_reg, skill_path):
        fm = {
            "name": "x",
            "metadata": {
                "hermes": {
                    "requires_tools": ["tool1"],
                    "fallback_for_tools": ["tool2"],
                }
            },
        }
        e = SkillEntry.from_frontmatter(name="x", path=skill_path, frontmatter=fm, source="user")
        empty_reg.register(e)
        deps = empty_reg.get_skill_dependencies("x")
        assert deps["requires_tools"] == ["tool1"]
        assert deps["fallback_for_tools"] == ["tool2"]

    def test_get_skill_dependencies_unknown(self, empty_reg):
        assert empty_reg.get_skill_dependencies("unknown") == {
            "requires_tools": [],
            "fallback_for_tools": [],
            "requires_toolsets": [],
            "fallback_for_toolsets": [],
        }

    def test_get_skills_requiring_tool(self, empty_reg, skill_path):
        fm1 = {"name": "a", "metadata": {"hermes": {"requires_tools": ["execute_code"]}}}
        fm2 = {"name": "b", "metadata": {"hermes": {"requires_tools": ["browser"]}}}
        empty_reg.register(
            SkillEntry.from_frontmatter(name="a", path=skill_path, frontmatter=fm1, source="user")
        )
        empty_reg.register(
            SkillEntry.from_frontmatter(name="b", path=skill_path, frontmatter=fm2, source="user")
        )
        result = empty_reg.get_skills_requiring_tool("execute_code")
        assert [e.name for e in result] == ["a"]

    def test_get_skills_fallback_for_tool(self, empty_reg, skill_path):
        fm1 = {"name": "a", "metadata": {"hermes": {"fallback_for_tools": ["openai"]}}}
        empty_reg.register(
            SkillEntry.from_frontmatter(name="a", path=skill_path, frontmatter=fm1, source="user")
        )
        result = empty_reg.get_skills_fallback_for_tool("openai")
        assert [e.name for e in result] == ["a"]


class TestSkillRegistryDispatch:
    def test_dispatch_loads_file(self, empty_reg, skill_path):
        skill_path.write_text("---\nname: x\n---\n# Hello", encoding="utf-8")
        e = SkillEntry(name="x", source="user", path=skill_path)
        empty_reg.register(e)
        result = empty_reg.dispatch("x")
        assert "content" in result
        assert result["name"] == "x"
        assert result["source"] == "user"

    def test_dispatch_unknown(self, empty_reg):
        result = empty_reg.dispatch("does-not-exist")
        assert "error" in result


class TestSkillRegistrySnapshot:
    def test_build_snapshot(self, empty_reg, skill_path):
        fm = {
            "name": "x",
            "metadata": {"hermes": {"requires_tools": ["tool1"]}},
            "platforms": ["linux"],
        }
        e = SkillEntry.from_frontmatter(
            name="x",
            path=skill_path,
            frontmatter=fm,
            source="bundled",
            description="A skill",
            category="ml",
        )
        empty_reg.register(e)
        snap = empty_reg.build_snapshot()
        assert "skills" in snap
        assert "category_descriptions" in snap
        entry = next(s for s in snap["skills"] if s["skill_name"] == "x")
        assert entry["category"] == "ml"
        assert entry["source"] == "bundled"
        assert entry["conditions"]["requires_tools"] == ["tool1"]


class TestSkillRegistrySecondaryIndexMaintenance:
    def test_category_index_updated_on_deregister(self, empty_reg, skill_path):
        e = SkillEntry(name="x", source="user", path=skill_path, category="ml")
        empty_reg.register(e)
        assert empty_reg.get_categories() == ["ml"]
        empty_reg.deregister("x")
        assert empty_reg.get_categories() == []

    def test_source_index_updated_on_overwrite(self, empty_reg, skill_path):
        e1 = SkillEntry(name="x", source="user", path=skill_path)
        e2 = SkillEntry(name="x", source="bundled", path=skill_path)
        empty_reg.register(e1)
        assert empty_reg.get_skills_by_source("user")[0].name == "x"
        empty_reg.register(e2)
        assert empty_reg.get_skills_by_source("user") == []
        assert empty_reg.get_skills_by_source("bundled")[0].name == "x"


class TestModuleRegistry:
    """Verify the module-level singleton is usable."""

    def test_singleton_type(self):
        assert isinstance(module_registry, SkillRegistry)

    def test_singleton_identity(self):
        import agent.skill_registry as sr

        assert sr.registry is module_registry
