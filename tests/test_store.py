from monospace.core.store import Envelope, Folder, Store


def _env(uid, subject="s", flags=None, date="2026-11-16T10:00:00"):
    return Envelope(account="a", folder="INBOX", uid=uid, subject=subject, sender="Ana <ana@x>",
                    date=date, flags=flags or [])


def test_upsert_list_search_flags(tmp_path):
    st = Store(tmp_path / "c.db")
    st.upsert_folder(Folder("a", "INBOX"))
    i1 = st.upsert_envelope(_env(1, "Quarterly report", date="2026-11-15T00:00:00"))
    i2 = st.upsert_envelope(_env(2, "Lunch?", flags=["\\Seen"]))
    assert st.upsert_envelope(_env(1, "Quarterly report")) == i1  # idempotent
    assert [e.uid for e in st.list("a", "INBOX")] == [2, 1]
    assert [e.uid for e in st.list("a", "INBOX", unread_only=True)] == [1]
    st.recount("a", "INBOX")
    assert st.folder("a", "INBOX").unread == 1
    assert [e.id for e in st.search("quarter")] == [i1]
    st.put_body(i2, b"raw", "pizza at noon")
    assert [e.id for e in st.search("pizza")] == [i2]
    st.set_flags(i1, ["\\Seen", "\\Flagged"])
    assert st.get(i1).flagged and not st.get(i1).unread
    st.delete_uids("a", "INBOX", [2])
    assert st.get(i2) is None and st.search("pizza") == []


def test_outbox(tmp_path):
    st = Store(tmp_path / "c.db")
    i = st.outbox_add("a", {"to": ["x@y"], "subject": "s"})
    assert st.outbox()[0]["draft"]["to"] == ["x@y"]
    st.outbox_set(i, "sent")
    assert st.outbox() == []
