"""Tests for core.skills — .md frontmatter discovery for skills and agents."""
from __future__ import annotations

import textwrap

from core.skills import discover_agents, discover_skills


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


def test_discover_skills_none_dir_returns_empty(tmp_path):
    assert discover_skills(None) == []
    assert discover_agents(None) == []


def test_discover_skills_missing_dir_returns_empty(tmp_path):
    assert discover_skills(tmp_path / "does-not-exist") == []


def test_discover_skills_parses_frontmatter(tmp_path):
    _write(
        tmp_path / "one.md",
        """\
        ---
        name: my-skill
        description: A handy skill
        ---
        body content here
        """,
    )
    skills = discover_skills(tmp_path)
    assert len(skills) == 1
    s = skills[0]
    assert s.name == "my_skill"  # sanitised: hyphen -> underscore
    assert s.description == "A handy skill"
    assert "body content here" in s.body
    assert s.kind == "skill"


def test_discover_skills_requires_name_and_description(tmp_path):
    _write(
        tmp_path / "nameless.md",
        """\
        ---
        description: no name here
        ---
        body
        """,
    )
    _write(
        tmp_path / "descless.md",
        """\
        ---
        name: foo
        ---
        body
        """,
    )
    _write(
        tmp_path / "good.md",
        """\
        ---
        name: good
        description: yes
        ---
        ok
        """,
    )
    skills = discover_skills(tmp_path)
    names = sorted(s.name for s in skills)
    assert names == ["good"]


def test_discover_skills_ignores_no_frontmatter(tmp_path):
    (tmp_path / "raw.md").write_text("just markdown, no yaml", encoding="utf-8")
    assert discover_skills(tmp_path) == []


def test_discover_skills_malformed_yaml_ignored(tmp_path):
    _write(
        tmp_path / "bad.md",
        """\
        ---
        name: x
        description: [unclosed
        ---
        body
        """,
    )
    assert discover_skills(tmp_path) == []


def test_discover_skills_dedup_by_name_last_wins(tmp_path):
    _write(
        tmp_path / "a.md",
        """\
        ---
        name: dup
        description: first
        ---
        A
        """,
    )
    _write(
        tmp_path / "z.md",
        """\
        ---
        name: dup
        description: second
        ---
        Z
        """,
    )
    skills = discover_skills(tmp_path)
    assert len(skills) == 1
    # sorted scan => "z.md" after "a.md"; last wins
    assert skills[0].description == "second"


def test_discover_agents_kind_field(tmp_path):
    _write(
        tmp_path / "an.md",
        """\
        ---
        name: agent1
        description: a
        ---
        x
        """,
    )
    agents = discover_agents(tmp_path)
    assert len(agents) == 1
    assert agents[0].kind == "agent"


def test_skill_name_sanitised_to_mcp_safe(tmp_path):
    _write(
        tmp_path / "s.md",
        """\
        ---
        name: "My Fancy/Skill!"
        description: d
        ---
        b
        """,
    )
    skills = discover_skills(tmp_path)
    # lowercase, non-alnum -> underscore, no leading/trailing underscores
    assert skills[0].name == "my_fancy_skill"


def test_skill_name_unnamed_fallback(tmp_path):
    _write(
        tmp_path / "s.md",
        """\
        ---
        name: "!!!"
        description: d
        ---
        b
        """,
    )
    skills = discover_skills(tmp_path)
    assert skills[0].name == "unnamed"
