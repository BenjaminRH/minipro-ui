"""Prerequisite discovery never downloads or silently selects a private backend."""

from minipro_ui.settings import Settings


def test_preferences_roundtrip(tmp_path):
    path = tmp_path / "config" / "settings.json"
    value = Settings("/some/minipro", "T76", ["chip"])
    value.save(path)
    loaded = Settings.load(path)
    assert loaded == value


def test_image_directory_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    value = Settings(image_directory="/tmp/minipro-images")

    value.save(path)

    assert Settings.load(path).image_directory == value.image_directory


def test_invalid_preferences_use_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("invalid")
    assert Settings.load(path) == Settings()


def test_path_discovery(monkeypatch, fake_executable):
    monkeypatch.setattr(
        "minipro_ui.settings.shutil.which", lambda _: str(fake_executable)
    )
    assert Settings().discover() == fake_executable
    assert Settings(executable="/missing/minipro").discover() is None
    assert Settings(executable=str(fake_executable)).discover() == fake_executable


def test_missing_backend_stays_missing(monkeypatch):
    monkeypatch.setattr("minipro_ui.settings.shutil.which", lambda _: None)
    assert Settings().discover() is None
