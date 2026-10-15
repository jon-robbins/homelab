from pathlib import Path

from scripts.ci.complexity_budget import violations


def budget(tmp_path: Path, limit: int) -> dict:
    return {"budgets": [{"paths": ["src"], "max_lines": limit}]}


def test_under_budget(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/code.py").write_text("x = 1\n\n# comment\ny = 2\n")
    assert violations(tmp_path, budget(tmp_path, 2)) == []


def test_over_budget(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/code.py").write_text("one = 1\ntwo = 2\n")
    assert violations(tmp_path, budget(tmp_path, 1)) == ["src: 2 > 1"]


def test_multiline_strings_count_each_physical_line(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/code.py").write_text('value = """one\ntwo\nthree\n"""\n')
    assert violations(tmp_path, budget(tmp_path, 4)) == []
    assert violations(tmp_path, budget(tmp_path, 3)) == ["src: 4 > 3"]


def test_comments_and_tests_are_excluded(tmp_path):
    (tmp_path / "src/tests").mkdir(parents=True)
    (tmp_path / "src/code.py").write_text("# comment\nvalue = 1\n")
    (tmp_path / "src/tests/test_code.py").write_text("value = 1\nvalue = 2\n")
    assert violations(tmp_path, budget(tmp_path, 1)) == []
