from __future__ import annotations

from pathlib import Path


bot = Path("bot_v3.py")
source = bot.read_text(encoding="utf-8")
old = '''            except CalibrationError as exc:
                _legacy.skip(f"{loc['name']} {horizon}: no eligible calibration group: {exc}")
                continue
'''
new = '''            except (CalibrationError, CalibrationRuntimeError) as exc:
                _legacy.skip(f"{loc['name']} {horizon}: calibration rejected candidate: {exc}")
                continue
'''
if old not in source:
    raise SystemExit("candidate calibration exception anchor not found")
bot.write_text(source.replace(old, new, 1), encoding="utf-8")

source_test = Path("tests/forecasting/test_bot_source.py")
test_source = source_test.read_text(encoding="utf-8")
test_source += '''


def test_candidate_runtime_calibration_rejections_are_local() -> None:
    source = Path("bot_v3.py").read_text(encoding="utf-8")

    assert "except (CalibrationError, CalibrationRuntimeError) as exc:" in source
    assert "calibration rejected candidate" in source
'''
source_test.write_text(test_source, encoding="utf-8")
