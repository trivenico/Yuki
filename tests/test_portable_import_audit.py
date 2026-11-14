import json
from pathlib import Path

import pytest

from scripts.portable_import_audit import audit_imports, main


def test_audit_classifies_stdlib_local_and_external_imports(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "agent.py").write_text(
        "import json\nfrom pathlib import Path\nimport yaml\nfrom providers.factory import create\n",
        encoding="utf-8",
    )
    (tmp_path / "providers").mkdir()
    (tmp_path / "providers" / "factory.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("import core.agent\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "ignored.py").write_text("import pytest\n", encoding="utf-8")

    report = audit_imports(tmp_path)

    assert "json" in report["stdlib"]
    assert "pathlib" in report["stdlib"]
    assert "core" in report["local"]
    assert "providers" in report["local"]
    assert "yaml" in report["third_party"]
    assert "pytest" not in report["third_party"]


def test_audit_reports_parse_errors_with_relative_path(tmp_path):
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(ValueError, match="broken.py"):
        audit_imports(tmp_path)


def test_cli_emits_valid_json(tmp_path, capsys):
    (tmp_path / "main.py").write_text("import json\n", encoding="utf-8")
    assert main(["--root", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "json" in payload["stdlib"]
