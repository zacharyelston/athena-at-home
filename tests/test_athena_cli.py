# SPDX-License-Identifier: GPL-3.0-or-later
import json

from fixtures.make_bundles import make_fixture_bundles

from athena.__main__ import main


def test_validate_ok_and_invalid(tmp_path, capsys):
    good, bad, _ = make_fixture_bundles(tmp_path / "in", tmp_path / "work")
    (bad / "raw/001.jpg").write_bytes(b"changed")
    assert main(["bundle", "validate", str(good)]) == 0
    assert main(["bundle", "validate", str(good), str(bad)]) == 1
    out = capsys.readouterr()
    assert "INVALID" in out.out and "1 valid, 1 invalid" in out.err


def test_validate_json_and_known_series(tmp_path, capsys):
    good = make_fixture_bundles(tmp_path / "in", tmp_path / "work")[0]
    assert main(["bundle", "validate", "--json", "--series", "yearbooks", str(good)]) == 0
    [result] = json.loads(capsys.readouterr().out)
    assert result["ok"] and result["warnings"]
