from types import SimpleNamespace

from chatcli.models import Config
from chatcli.ui.menus import base


def flat(buf) -> str:
    return " ".join(buf.getvalue().replace("│", " ").split())


def test_a_new_menu_is_just_a_description(fernet, out, prompts, monkeypatch):
    """The point of CollectionMenu: a fourth collection needs no new loop."""
    cfg = Config()
    cfg.widgets = []
    spec = base.CollectionMenu(
        title="Widgets", attr="widgets", label=lambda w: w.name, delete_title="Delete widget",
        empty_message="No widgets.", columns=("Name",), row=lambda w: (w.name,),
        prompt_new=lambda: SimpleNamespace(name="w1", to_dict=lambda: {}),
        saved_message=lambda w: f"Widget '{w.name}' saved.",
    )
    monkeypatch.setattr(base, "save_config", lambda c, f: None)   # Config has no widgets field
    prompts("3", "1", "3", "2", "1", "3", "0")
    base.run_collection_menu(spec, cfg, fernet)
    text = flat(out)
    assert "No widgets." in text and "Widget 'w1' saved." in text and "Deleted 'w1'." in text
    assert cfg.widgets == []
