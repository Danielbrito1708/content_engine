"""Tests for the standalone silence-cutting CLI (scripts/cut_silence.py).

The script is loaded by path (scripts/ is not a package), same convention as
blender_worker/tests/test_subtitles.py. Requires ffmpeg/ffprobe installed.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from tests.test_postprocess import _make_mp3

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "cut_silence.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("cut_silence", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves the owning module through sys.modules at decoration time,
    # so the module must be registered before exec_module runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cut_silence = _load_script()


def _write_audio(tmp_path: Path, name: str = "narracao.mp3") -> Path:
    path = tmp_path / name
    path.write_bytes(_make_mp3(("silence", 800), ("tone", 400), ("silence", 900), ("tone", 400)))
    return path


# --- pure helpers ---

def test_default_output_appends_trimmed_suffix():
    assert cut_silence.default_output_for(Path("/a/b/narracao.mp3")).name == "narracao.trimmed.mp3"


def test_default_output_keeps_directory():
    assert cut_silence.default_output_for(Path("/a/b/narracao.wav")).parent == Path("/a/b")


def test_probe_duration_reads_real_audio():
    duration = cut_silence.probe_duration_ms(_make_mp3(("tone", 500)))
    assert 400 < duration < 700


def test_probe_duration_returns_zero_on_garbage():
    assert cut_silence.probe_duration_ms(b"not audio at all") == 0


def test_removed_pct_is_zero_when_source_has_no_duration():
    result = cut_silence.CutResult(source=Path("x"), output=None, before_ms=0, after_ms=0)
    assert result.removed_pct == 0.0


def test_removed_ms_is_the_difference():
    result = cut_silence.CutResult(source=Path("x"), output=None, before_ms=2000, after_ms=1200)
    assert result.removed_ms == 800
    assert result.removed_pct == 40.0


# --- cut_file ---

def test_cut_file_writes_shorter_audio(tmp_path):
    source = _write_audio(tmp_path)
    output = tmp_path / "out.mp3"

    result = cut_silence.cut_file(source, output, 200, -40)

    assert output.exists()
    assert result.after_ms < result.before_ms


def test_cut_file_dry_run_writes_nothing(tmp_path):
    source = _write_audio(tmp_path)

    result = cut_silence.cut_file(source, None, 200, -40)

    assert list(tmp_path.iterdir()) == [source]
    assert result.after_ms < result.before_ms


def test_cut_file_leaves_source_untouched(tmp_path):
    source = _write_audio(tmp_path)
    original = source.read_bytes()

    cut_silence.cut_file(source, tmp_path / "out.mp3", 200, -40)

    assert source.read_bytes() == original


# --- main / CLI ---

def test_main_creates_default_output(tmp_path):
    source = _write_audio(tmp_path)

    assert cut_silence.main([str(source)]) == 0
    assert (tmp_path / "narracao.trimmed.mp3").exists()


def test_main_honours_explicit_output(tmp_path):
    source = _write_audio(tmp_path)
    output = tmp_path / "custom.mp3"

    assert cut_silence.main([str(source), "-o", str(output)]) == 0
    assert output.exists()


def test_main_reports_durations(tmp_path, capsys):
    source = _write_audio(tmp_path)

    cut_silence.main([str(source)])

    out = capsys.readouterr().out
    assert "->" in out and "narracao.trimmed.mp3" in out
    # no non-ASCII in the happy-path report: cp1252 consoles raise on encode
    out.encode("cp1252")


def test_main_dry_run_creates_no_file(tmp_path, capsys):
    source = _write_audio(tmp_path)

    assert cut_silence.main([str(source), "--dry-run"]) == 0
    assert list(tmp_path.iterdir()) == [source]
    assert "(dry-run)" in capsys.readouterr().out


def test_main_missing_input_is_usage_error(tmp_path, capsys):
    assert cut_silence.main([str(tmp_path / "nope.mp3")]) == 2
    assert "não encontrado" in capsys.readouterr().err


def test_main_rejects_output_with_multiple_inputs(tmp_path, capsys):
    a = _write_audio(tmp_path, "a.mp3")
    b = _write_audio(tmp_path, "b.mp3")

    assert cut_silence.main([str(a), str(b), "-o", str(tmp_path / "o.mp3")]) == 2
    assert "único input" in capsys.readouterr().err


def test_main_refuses_to_overwrite_existing_output(tmp_path, capsys):
    source = _write_audio(tmp_path)
    existing = tmp_path / "narracao.trimmed.mp3"
    existing.write_bytes(b"preexisting")

    assert cut_silence.main([str(source)]) == 1
    assert existing.read_bytes() == b"preexisting"
    assert "--force" in capsys.readouterr().err


def test_main_force_overwrites(tmp_path):
    source = _write_audio(tmp_path)
    existing = tmp_path / "narracao.trimmed.mp3"
    existing.write_bytes(b"preexisting")

    assert cut_silence.main([str(source), "--force"]) == 0
    assert existing.read_bytes() != b"preexisting"


def test_main_refuses_output_equal_to_input(tmp_path, capsys):
    source = _write_audio(tmp_path)
    original = source.read_bytes()

    assert cut_silence.main([str(source), "-o", str(source)]) == 1
    assert source.read_bytes() == original
    assert "sobrescreveria" in capsys.readouterr().err


def test_main_processes_every_input(tmp_path):
    a = _write_audio(tmp_path, "a.mp3")
    b = _write_audio(tmp_path, "b.mp3")

    assert cut_silence.main([str(a), str(b)]) == 0
    assert (tmp_path / "a.trimmed.mp3").exists()
    assert (tmp_path / "b.trimmed.mp3").exists()


def test_main_passes_tuning_flags_through(tmp_path):
    source = _write_audio(tmp_path)

    with patch.object(cut_silence, "process_audio", side_effect=lambda b, **kw: b) as mock_process:
        cut_silence.main([str(source), "--max-pause-ms", "150", "--thresh-db", "-35"])

    kwargs = mock_process.call_args.kwargs
    assert (kwargs["max_pause_ms"], kwargs["silence_thresh_db"]) == (150, -35)


def test_main_reports_missing_ffmpeg(tmp_path, capsys):
    source = _write_audio(tmp_path)

    with patch.object(cut_silence, "process_audio", side_effect=FileNotFoundError):
        assert cut_silence.main([str(source)]) == 1

    assert "ffmpeg não encontrado" in capsys.readouterr().err


def test_main_reports_ffmpeg_failure_and_continues(tmp_path, capsys):
    a = _write_audio(tmp_path, "a.mp3")
    b = _write_audio(tmp_path, "b.mp3")
    error = subprocess.CalledProcessError(1, "ffmpeg", stderr=b"Invalid data found")

    with patch.object(cut_silence, "process_audio", side_effect=[error, b"ok-audio"]):
        assert cut_silence.main([str(a), str(b)]) == 1

    assert "Invalid data found" in capsys.readouterr().err
    assert not (tmp_path / "a.trimmed.mp3").exists()
    assert (tmp_path / "b.trimmed.mp3").exists()
