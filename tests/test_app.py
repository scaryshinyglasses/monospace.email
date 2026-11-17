import io
from email.message import EmailMessage

from PIL import Image

from monospace.core.compose import Draft
from monospace.ui.app import MonospaceApp
from monospace.ui.screens.account_add import AccountAddScreen
from monospace.ui.screens.compose import ComposeScreen
from monospace.ui.widgets.attachment_bar import AttachmentBar
from monospace.ui.widgets.folder_tree import FolderTree
from monospace.ui.widgets.image_view import ImageView
from monospace.ui.widgets.message_list import MessageList
from monospace.ui.widgets.reader import Reader


def _gif_mail() -> bytes:
    frames = [Image.new("RGB", (40, 20), (i * 100, 50, 50)) for i in range(3)]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=40, loop=0)
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = "News <n@x>", "me@example.com", "Animated", "<g@x>"
    m["Date"] = "Wed, 18 Nov 2026 10:00:00 +0000"
    m.set_content("plain")
    m.add_alternative('<h1>Hi</h1><p>look <a href="https://ex.com">here</a></p><img src="cid:anim" alt="spin">',
                      subtype="html")
    m.get_payload()[1].add_related(buf.getvalue(), maintype="image", subtype="gif", cid="<anim>")
    return m.as_bytes()


async def _open(pilot, app, subject):
    table = app.screen.query_one(MessageList)
    env = next(e for e in table.envelopes.values() if e.subject == subject)
    table.move_cursor(row=table.get_row_index(str(env.id)))
    await pilot.pause(0.4)
    await app.workers.wait_for_complete()
    await pilot.pause()
    return env


async def test_browse_and_read(svc):
    svc.sync()
    app = MonospaceApp(svc, watch=False)
    async with app.run_test(size=(140, 45)) as pilot:
        tree = app.screen.query_one(FolderTree)
        labels = [str(n.label) for n in tree.root.children[0].children]
        assert any("Inbox" in lbl for lbl in labels)
        table = app.screen.query_one(MessageList)
        assert table.row_count == 2
        await _open(pilot, app, "Invoice")
        reader = app.screen.query_one(Reader)
        assert reader.parsed and reader.parsed.subject == "Invoice"
        assert len(reader.query(AttachmentBar)) == 1


async def test_gif_renders_and_animates(svc, server):
    server.deliver("INBOX", _gif_mail())
    svc.sync()
    app = MonospaceApp(svc, watch=False)
    async with app.run_test(size=(140, 50)) as pilot:
        await _open(pilot, app, "Animated")
        view = app.screen.query_one(ImageView)
        await app.workers.wait_for_complete()
        await pilot.pause(0.1)
        assert view._anim is not None and view._anim.animated
        before = view._frame
        await pilot.pause(0.3)
        assert view._frame != before  # timer advanced the animation


async def test_compose_and_send(svc, sent):
    svc.sync()
    app = MonospaceApp(svc, watch=False)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, ComposeScreen)
        await pilot.click("#to")
        await pilot.press(*"bob@example.com")
        app.screen.query_one("#subject").value = "Lunch"
        app.screen.query_one("#body").text = "Tomorrow at **noon**?"
        await pilot.press("ctrl+s")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert len(sent) == 1
        msg, rcpts = sent[0]
        assert rcpts == ["bob@example.com"] and msg["Subject"] == "Lunch"


async def test_reply_prefills(svc):
    svc.sync()
    app = MonospaceApp(svc, watch=False)
    async with app.run_test(size=(140, 45)) as pilot:
        await _open(pilot, app, "Welcome")
        await pilot.press("r")
        await pilot.pause()
        assert isinstance(app.screen, ComposeScreen)
        assert app.screen.query_one("#subject").value == "Re: Welcome"
        assert "> first message" in app.screen.query_one("#body").text


async def test_outbox_approve(svc, sent):
    svc.account().ai_send = "confirm"
    svc.submit(None, Draft(to=["x@y.com"], subject="AI wrote this", body="hi"), "ai")
    app = MonospaceApp(svc, watch=False)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("O")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert len(sent) == 1 and svc.outbox() == []


async def test_wizard_when_no_accounts(svc):
    svc.cfg.accounts.clear()
    app = MonospaceApp(svc, watch=False)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, AccountAddScreen)
        await pilot.click("#p-custom")
        assert app.screen.query_one("#servers").display


async def test_ctrl_c_copies_selection_instead_of_quitting(svc, monkeypatch):
    """ctrl+c used to be a priority quit binding, so copy never ran."""
    from textual.widgets import Input

    from monospace.core import clipboard
    from monospace.ui.app import MonospaceApp
    from monospace.ui.screens.compose import ComposeScreen
    from monospace.core.compose import Draft

    monkeypatch.setattr(clipboard, "native_copy", lambda text: False)
    monkeypatch.setattr(clipboard, "native_paste", lambda: None)
    app = MonospaceApp(svc, images=False, watch=False)
    async with app.run_test() as pilot:
        app.push_screen(ComposeScreen(Draft(subject="hello"), "work", [("work", "me@example.com")]))
        await pilot.pause()
        inp = next(w for w in app.screen.query(Input) if w.value == "hello")
        inp.focus()
        inp.select_all()
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert app.is_running
        assert app.clipboard == "hello"
        inp.value = ""
        await pilot.press("ctrl+v")
        assert inp.value == "hello"


async def test_ctrl_v_pastes_system_clipboard(svc, monkeypatch):
    from textual.widgets import Input

    from monospace.core import clipboard
    from monospace.core.compose import Draft
    from monospace.ui.app import MonospaceApp
    from monospace.ui.screens.compose import ComposeScreen

    monkeypatch.setattr(clipboard, "native_paste", lambda: "from-system")
    app = MonospaceApp(svc, images=False, watch=False)
    async with app.run_test() as pilot:
        app.push_screen(ComposeScreen(Draft(), "work", [("work", "me@example.com")]))
        await pilot.pause()
        inp = app.screen.query(Input).first()
        inp.focus()
        await pilot.press("ctrl+v")
        assert "from-system" in inp.value


def _png_mail() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (300, 150), (20, 120, 200)).save(buf, format="PNG")
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = "News <n@x>", "me@example.com", "Static", "<s@x>"
    m["Date"] = "Wed, 18 Nov 2026 11:00:00 +0000"
    m.set_content("plain")
    m.add_alternative('<p>chart:</p><img src="cid:chart" alt="chart">', subtype="html")
    m.get_payload()[1].add_related(buf.getvalue(), maintype="image", subtype="png", cid="<chart>")
    return m.as_bytes()


async def test_static_image_gets_real_height(svc, server):
    """textual_image ≥0.14 computed `height: auto` as 0 rows for static images (only GIFs,
    which re-layout every frame, ever showed up)."""
    server.deliver("INBOX", _png_mail())
    svc.sync()
    app = MonospaceApp(svc, watch=False)
    async with app.run_test(size=(140, 50)) as pilot:
        await _open(pilot, app, "Static")
        await app.workers.wait_for_complete()
        await pilot.pause(0.2)
        view = app.screen.query_one(ImageView)
        assert view._img is not None
        assert view._img.region.width >= 6
        assert view._img.region.height >= 2  # 300×150 px → roughly half as tall as wide in pixels
