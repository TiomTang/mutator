from pathlib import Path

from mutator.coverage import covered_lines
from mutator.functions import sites_in_file
from mutator import runner
from mutator.sites import discover_raw
from mutator.workers import create_workers, worker_count

SAMPLE = """namespace Demo.Core;

public class Sample
{
    private int _count;

    public bool Check(int a, int b, string? name, object? o)
    {
        bool positive = a > 0 && b <= 1;
        bool flag = true;
        int total = a + b * 2;
        int negative = -a;
        bool missing = !positive;
        var length = name?.Length;
        var value = name ?? "fallback";
        if (o is string)
        {
            return positive || flag;
        }
        return missing && length > 0 && value.Length > 0 && negative < total;
    }

    private int Hidden(int a)
    {
        return a + 1;
    }
}
"""

COBERTURA = """<?xml version="1.0" encoding="utf-8"?>
<coverage line-rate="0.5" version="1.9">
  <packages>
    <package name="App">
      <classes>
        <class name="Demo.Core.Sample" filename="App/Sample.cs">
          <methods>
            <method name="Check"><lines><line number="9" hits="2"/><line number="10" hits="0"/></lines></method>
          </methods>
          <lines>
            <line number="9" hits="2"/>
            <line number="10" hits="0"/>
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
"""


def _written(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_csharp_mutations_mirror_the_java_decisions(tmp_path):
    path = _written(tmp_path, "src/Sample.cs", SAMPLE)

    found = discover_raw(SAMPLE, "csharp", path.as_posix())
    described = {(site.original, site.mutant) for site in found}

    assert ("+", "-") in described
    assert ("*", "/") in described
    assert (">", ">=") in described
    assert ("<=", "<") in described
    assert ("&&", "||") in described
    assert ("||", "&&") in described
    assert ("true", "false") in described
    assert ("0", "1") in described
    # prefix_unary_expression: both `!` and a leading `-` are deleted
    assert ("!", "") in described
    assert ("-", "") in described
    # `a?.b` is a `?` token inside conditional_access_expression
    assert ("?", "") in described
    # `x is Foo` -> `x is not Foo`
    assert ("is", "is not") in described
    # `??` is rarely boolean in C#; `||` there would not compile, and a mutant
    # that fails to build is scored as killed.
    assert not any(site.original == "??" for site in found)


def test_csharp_sites_belong_to_the_method_that_covers_them(tmp_path):
    path = _written(tmp_path, "src/Sample.cs", SAMPLE)

    sites = sites_in_file(SAMPLE, path, tmp_path, "src/Sample.cs")

    assert {site.namespace for site in sites} == {"Demo.Core.Sample"}
    forms = {site.form_id for site in sites}
    assert "defn/Check" in forms
    assert "defn-/Hidden" in forms
    assert all(site.anchor_line <= site.line for site in sites)
    assert all(site.line >= 8 for site in sites)


def test_csharp_plan_prefers_the_nearest_solution(tmp_path):
    (tmp_path / "App.sln").write_text("", encoding="utf-8")
    source = _written(tmp_path, "src/Deep/Sample.cs", SAMPLE)

    command, cwd = runner.test_plan(tmp_path, source, "csharp", None)

    assert command[:3] == ["dotnet", "test", "App.sln"]
    assert cwd == tmp_path


def test_csharp_plan_falls_back_to_the_nearest_project(tmp_path):
    (tmp_path / "App").mkdir()
    (tmp_path / "App" / "App.csproj").write_text("<Project/>", encoding="utf-8")
    source = _written(tmp_path, "App/Sample.cs", SAMPLE)

    command, cwd = runner.test_plan(tmp_path, source, "csharp", None)

    assert command[:3] == ["dotnet", "test", "App.csproj"]
    assert cwd == tmp_path / "App"


def test_csharp_workers_build_in_their_own_obj_and_bin(tmp_path):
    root = tmp_path
    (root / "App.sln").write_text("", encoding="utf-8")
    (root / "App").mkdir()
    (root / "App" / "App.csproj").write_text("<Project/>", encoding="utf-8")
    _written(root, "App/obj/project.assets.json", "{}")
    _written(root, "App/bin/App.dll", "binary")
    source = _written(root, "App/Sample.cs", SAMPLE)
    original = source.read_bytes()
    base = root / "target" / "mutation-workers" / "run-test"
    base.mkdir(parents=True)

    worker = create_workers(base, root, "App/Sample.cs", original, 1, "csharp")[0]

    project = worker / "App"
    assert project.is_dir() and not project.is_symlink()
    assert (project / "Sample.cs").read_bytes() == original
    assert (project / "App.csproj").is_symlink()
    # obj/ and bin/ are never linked: a shared build directory makes two
    # parallel MSBuild runs fight over the same files.
    assert not (project / "obj").exists()
    assert not (project / "bin").exists()
    assert (worker / "App.sln").exists()


def test_csharp_worker_count_stops_at_four(tmp_path):
    assert worker_count(100, None, "csharp") <= 4
    assert worker_count(2, None, "csharp") == 2
    assert worker_count(100, 1, "csharp") == 1
    assert worker_count(100, 8, "csharp") == 8


def test_csharp_coverage_answers_per_file(tmp_path):
    report = tmp_path / "TestResults" / "abc" / "coverage.cobertura.xml"
    report.parent.mkdir(parents=True)
    report.write_text(COBERTURA, encoding="utf-8")

    assert covered_lines(tmp_path, tmp_path / "App" / "Sample.cs", "csharp") == {9}
    assert covered_lines(tmp_path, tmp_path / "App" / "Missing.cs", "csharp") is None
