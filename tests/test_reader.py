from textual.app import App, ComposeResult
from textual.widgets import Static

from monospace.ui.app import MonospaceApp  # noqa: F401  initializes terminal image support
from monospace.ui.widgets.reader import Reader


class ReaderApp(App):
    remote_images_default = False

    def compose(self) -> ComposeResult:
        yield Reader()


async def test_repeated_clear_keeps_latest_note():
    app = ReaderApp()
    async with app.run_test() as pilot:
        reader = app.query_one(Reader)
        reader.clear_message("First folder")
        reader.clear_message("Second folder")
        await pilot.pause()
        assert len(reader.children) == 1
        assert str(reader.query_one("#empty", Static).render()) == "Second folder"
        assert reader.envelope is None and reader.parsed is None


async def test_rapid_message_changes_and_toggles(svc):
    svc.sync()
    messages = [svc.read(env.id, mark_read=False) for env in svc.list("work", "INBOX")]
    app = ReaderApp()
    async with app.run_test() as pilot:
        reader = app.query_one(Reader)
        reader.show(*messages[0])
        await pilot.pause()
        reader.toggle_html()
        reader.toggle_remote()
        reader.show(*messages[1])
        await pilot.pause()
        assert len(reader.query("#headers")) == 1
        assert messages[1][1].subject in str(reader.query_one("#headers", Static).render())
        assert reader.envelope == messages[1][0]
        assert reader.parsed == messages[1][1]
        reader.clear_message()
        reader.show(*messages[0])
        reader.clear_message("Empty folder")
        await pilot.pause()
        assert len(reader.children) == 1
        assert str(reader.query_one("#empty", Static).render()) == "Empty folder"
        assert reader.envelope is None and reader.parsed is None
