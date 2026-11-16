from monospace.core import compose, mime
from tests.fakes import make_raw, parse


def test_parse_attachment_and_text():
    pm = mime.parse(make_raw("Hi =?utf-8?q?caf=C3=A9?=", body="body text", attachment=("a.bin", b"xyz")))
    assert pm.subject == "Hi café"
    assert pm.text.strip() == "body text"
    assert [a.filename for a in pm.attachments] == ["a.bin"] and pm.attachments[0].data == b"xyz"


def test_parse_html_only_falls_back_to_text():
    raw = (b"From: a@b\r\nTo: c@d\r\nSubject: s\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
           b"<html><style>x{}</style><p>Hello <b>world</b></p><p>Line&nbsp;2</p></html>")
    pm = mime.parse(raw)
    assert pm.html and "Hello world" in pm.best_text() and "x{}" not in pm.best_text()


def test_inline_cid_image():
    from email.message import EmailMessage

    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "a@b", "c@d", "img"
    m.set_content("plain")
    m.add_alternative('<img src="cid:logo1">', subtype="html")
    m.get_payload()[1].add_related(b"GIF89a", maintype="image", subtype="gif", cid="<logo1>")
    pm = mime.parse(m.as_bytes())
    assert "logo1" in pm.inline and pm.inline["logo1"].data == b"GIF89a"
    assert pm.attachments == []


def test_build_multipart_alternative(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("n")
    d = compose.Draft(to=["x@y.com"], bcc=["hidden@y.com"], subject="S", body="**bold**", attachments=[str(f)])
    msg = parse(compose.build(d, "Me <me@example.com>").as_bytes())
    assert msg["Bcc"] is None
    assert msg.get_body(("html",)).get_content().count("<strong>bold</strong>") == 1
    assert [p.get_filename() for p in msg.iter_attachments()] == ["notes.txt"]
    assert d.recipients() == ["x@y.com", "hidden@y.com"]


def test_reply_all_excludes_self_and_threads():
    orig = mime.parse(make_raw("Plan", sender="Ana <ana@x.com>", to="me@example.com, bo@x.com", msgid="<p@x>"))
    d = compose.reply(orig, "Sounds good", "me@example.com", reply_all=True)
    assert d.to == ["Ana <ana@x.com>"] and d.cc == ["bo@x.com"]
    assert d.subject == "Re: Plan" and d.in_reply_to == "<p@x>"
    assert "> Hi there" in d.body
    assert compose.reply(mime.parse(make_raw("Re: Plan")), "", "me@x").subject == "Re: Plan"
