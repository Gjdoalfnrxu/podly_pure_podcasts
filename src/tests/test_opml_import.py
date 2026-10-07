from __future__ import annotations

import io
from collections.abc import Generator
from types import SimpleNamespace
from unittest import mock

import pytest
from flask import Blueprint, Flask

from app.auth import AuthSettings
from app.auth.middleware import init_auth_middleware
from app.auth.state import failure_rate_limiter
from app.extensions import db
from app.models import Feed, User, UserFeed
from app.opml import OpmlParseError, parse_opml
from app.routes.auth_routes import auth_bp
from app.routes.feed_routes import feed_bp
from app.routes.opml_routes import MAX_OPML_BYTES, opml_bp

ENDPOINT = "/api/feeds/import-opml"

NESTED_OPML = b"""<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>subs</title></head>
  <body>
    <outline text="Top" type="rss" xmlUrl="https://a.example.com/feed.xml"/>
    <outline text="News">
      <outline text="B" type="rss" xmlUrl="https://b.example.com/rss"/>
      <outline text="Deeper">
        <outline title="C" type="rss" xmlurl="https://c.example.com/podcast"/>
      </outline>
      <outline text="A again" type="rss" xmlUrl="https://a.example.com/feed.xml"/>
    </outline>
    <outline text="No url here"/>
    <outline text="B no scheme" xmlUrl="b.example.com/rss"/>
  </body>
</opml>
"""

XXE_OPML = b"""<?xml version="1.0"?>
<!DOCTYPE opml [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<opml version="2.0"><body>
  <outline text="&xxe;" xmlUrl="https://x.example.com/feed"/>
</body></opml>
"""

BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
]>
<opml><body><outline text="&lol3;" xmlUrl="https://x.example.com/feed"/></body></opml>
"""


def _opml(*urls: str) -> bytes:
    outlines = "".join(f'<outline text="f" xmlUrl="{u}"/>' for u in urls)
    return f"<opml version='2.0'><body>{outlines}</body></opml>".encode()


def _fake_add_or_refresh(fail_urls: frozenset[str] = frozenset()):
    def _impl(url: str, *, whitelist_archive: bool = True) -> Feed:
        if url in fail_urls:
            raise ValueError(f"Invalid feed URL: {url}")
        feed = Feed.query.filter_by(rss_url=url).first()
        if feed is None:
            feed = Feed(title=f"Feed {url}", rss_url=url)
            db.session.add(feed)
            db.session.commit()
        return feed

    return _impl


def _fake_writer_action(name: str, params: dict, wait: bool = True):
    if name == "ensure_user_feed_membership":
        previous = UserFeed.query.filter_by(feed_id=params["feed_id"]).count()
        existing = UserFeed.query.filter_by(
            feed_id=params["feed_id"], user_id=params["user_id"]
        ).first()
        if existing is None:
            db.session.add(
                UserFeed(feed_id=params["feed_id"], user_id=params["user_id"])
            )
            db.session.commit()
        return SimpleNamespace(
            success=True,
            data={"created": existing is None, "previous_count": previous},
        )
    if name == "toggle_whitelist_all_for_feed":
        return SimpleNamespace(success=True, data={"updated_count": 0})
    # Login bookkeeping (update_user_last_active) also goes through the writer.
    return SimpleNamespace(success=True, data={})


@pytest.fixture
def patched():
    """Patch the I/O edges of the shared subscribe path (network fetch, writer, threads)."""
    with (
        mock.patch(
            "app.routes.feed_subscribe.add_or_refresh_feed",
            side_effect=_fake_add_or_refresh(),
        ) as add,
        mock.patch("app.routes.feed_subscribe.whitelist_latest_for_first_member") as wl,
        mock.patch("app.routes.feed_subscribe.Thread") as thread,
        # writer_client is one shared singleton across modules.
        mock.patch(
            "app.writer.client.writer_client.action",
            side_effect=_fake_writer_action,
        ) as writer,
    ):
        yield SimpleNamespace(add=add, whitelist=wl, thread=thread, writer=writer)


def _client(app: Flask):
    app.testing = True
    app.register_blueprint(feed_bp)
    app.register_blueprint(opml_bp)
    if "main" not in app.blueprints:
        main_bp = Blueprint("main", __name__)

        @main_bp.route("/", endpoint="index")
        def _index():
            return "ok"

        app.register_blueprint(main_bp)
    return app.test_client()


def _upload(client, data: bytes, **form):
    return client.post(
        ENDPOINT,
        data={"file": (io.BytesIO(data), "subs.opml"), **form},
        content_type="multipart/form-data",
    )


# ---------------------------------------------------------------- parser


def test_parse_opml_collects_nested_outlines_in_order():
    urls = [f.url for f in parse_opml(NESTED_OPML)]
    assert urls == [
        "https://a.example.com/feed.xml",
        "https://b.example.com/rss",
        "https://c.example.com/podcast",
        "https://a.example.com/feed.xml",
        "b.example.com/rss",
    ]


def test_parse_opml_rejects_external_entity():
    with pytest.raises(OpmlParseError, match="DOCTYPE"):
        parse_opml(XXE_OPML)


def test_parse_opml_rejects_entity_expansion():
    with pytest.raises(OpmlParseError, match="DOCTYPE"):
        parse_opml(BILLION_LAUGHS)


def test_parse_opml_rejects_non_opml_root():
    with pytest.raises(OpmlParseError, match="not OPML"):
        parse_opml(b"<rss><outline xmlUrl='https://x.example.com/f'/></rss>")


# ---------------------------------------------------------------- route, auth disabled


def test_import_nested_and_dedupes(app, patched):
    with app.app_context():
        resp = _upload(_client(app), NESTED_OPML)

        assert resp.status_code == 200, resp.data
        body = resp.get_json()
        # a and b appear twice (b once without scheme -> fix_url normalises it)
        assert body["added"] == [
            "https://a.example.com/feed.xml",
            "https://b.example.com/rss",
            "https://c.example.com/podcast",
        ]
        assert body["skipped_existing"] == []
        assert body["failed"] == []
        assert [c.kwargs for c in patched.add.call_args_list] == [
            {"whitelist_archive": True}
        ] * 3
        assert {f.rss_url for f in Feed.query.all()} == set(body["added"])
        # Same post-add behaviour as POST /feed: latest episode queued per new feed,
        # one enqueue kick for the whole batch.
        assert patched.whitelist.call_count == 3
        patched.thread.assert_called_once()


def test_import_skips_already_subscribed(app, patched):
    with app.app_context():
        db.session.add(Feed(title="old", rss_url="https://a.example.com/feed.xml"))
        db.session.commit()

        resp = _upload(
            _client(app),
            _opml("https://a.example.com/feed.xml", "https://new.example.com/f"),
        )

        body = resp.get_json()
        assert body["skipped_existing"] == ["https://a.example.com/feed.xml"]
        assert body["added"] == ["https://new.example.com/f"]
        assert [c.args[0] for c in patched.add.call_args_list] == [
            "https://new.example.com/f"
        ]


def test_per_feed_failure_does_not_abort_batch(app, patched):
    patched.add.side_effect = _fake_add_or_refresh(
        frozenset({"https://broken.example.com/f"})
    )
    with app.app_context():
        resp = _upload(
            _client(app),
            _opml(
                "https://ok1.example.com/f",
                "https://broken.example.com/f",
                "not a url at all",
                "https://ok2.example.com/f",
            ),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["added"] == [
            "https://ok1.example.com/f",
            "https://ok2.example.com/f",
        ]
        failed = {f["url"]: f["error"] for f in body["failed"]}
        assert "Invalid feed URL" in failed["https://broken.example.com/f"]
        assert failed["https://not a url at all"] == "Invalid URL"


def test_malformed_xml_returns_400(app, patched):
    with app.app_context():
        resp = _upload(_client(app), b"<opml><body><outline xmlUrl='x'></body>")
        assert resp.status_code == 400
        assert "Malformed XML" in resp.get_json()["error"]
        patched.add.assert_not_called()


@pytest.mark.parametrize("payload", [XXE_OPML, BILLION_LAUGHS])
def test_entity_payloads_rejected(app, patched, payload):
    with app.app_context():
        resp = _upload(_client(app), payload)
        assert resp.status_code == 400
        assert "DOCTYPE" in resp.get_json()["error"]
        patched.add.assert_not_called()


def test_oversize_upload_rejected(app, patched):
    padding = b"<!--" + b"x" * MAX_OPML_BYTES + b"-->"
    with app.app_context():
        resp = _upload(_client(app), _opml("https://a.example.com/f") + padding)
        assert resp.status_code == 413
        patched.add.assert_not_called()


def test_oversize_raw_body_without_content_length_rejected(app, patched):
    # Chunked-style body: no Content-Length, so the streaming cap must catch it.
    padding = b"<!--" + b"x" * MAX_OPML_BYTES + b"-->"
    with app.app_context():
        client = _client(app)
        resp = client.post(
            ENDPOINT,
            input_stream=io.BytesIO(_opml("https://a.example.com/f") + padding),
            content_type="text/x-opml",
            headers={"Transfer-Encoding": "chunked"},
            environ_overrides={"wsgi.input_terminated": True},
        )
        assert resp.status_code == 413
        patched.add.assert_not_called()


def test_raw_xml_body_accepted(app, patched):
    with app.app_context():
        resp = _client(app).post(
            ENDPOINT, data=_opml("https://raw.example.com/f"), content_type="text/xml"
        )
        assert resp.status_code == 200, resp.data
        assert resp.get_json()["added"] == ["https://raw.example.com/f"]


def test_missing_file_returns_400(app, patched):
    with app.app_context():
        resp = _client(app).post(ENDPOINT, data={}, content_type="multipart/form-data")
        assert resp.status_code == 400


def test_process_latest_false_queues_nothing(app, patched):
    with app.app_context():
        resp = _upload(
            _client(app),
            _opml("https://n1.example.com/f", "https://n2.example.com/f"),
            process_latest="false",
        )

        assert resp.status_code == 200, resp.data
        body = resp.get_json()
        assert body["process_latest"] is False
        assert body["added"] == ["https://n1.example.com/f", "https://n2.example.com/f"]
        # Backlog stored un-whitelisted in the same writer call that creates jobs.
        assert [c.kwargs for c in patched.add.call_args_list] == [
            {"whitelist_archive": False},
            {"whitelist_archive": False},
        ]
        patched.whitelist.assert_not_called()
        patched.thread.assert_not_called()


def test_invalid_process_latest_returns_400(app, patched):
    with app.app_context():
        resp = _upload(
            _client(app), _opml("https://a.example.com/f"), process_latest="maybe"
        )
        assert resp.status_code == 400
        patched.add.assert_not_called()


def test_single_add_feed_uses_shared_subscribe_path(app, patched):
    """POST /feed and the importer must share subscribe_to_feed."""
    with app.app_context():
        client = _client(app)
        resp = client.post("/feed", data={"url": "https://single.example.com/f"})
        assert resp.status_code == 302
        patched.add.assert_called_once_with(
            "https://single.example.com/f", whitelist_archive=True
        )
        patched.whitelist.assert_called_once()
        patched.thread.assert_called_once()

        bad = client.post("/feed", data={"url": "https://exa mple"})
        assert bad.status_code == 400
        assert bad.data == b"Invalid URL"


# ---------------------------------------------------------------- route, auth enabled


@pytest.fixture
def auth_app() -> Generator[Flask, None, None]:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="test-secret",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        AUTH_SETTINGS=AuthSettings(
            require_auth=True, admin_username="admin", admin_password="password"
        ),
        REQUIRE_AUTH=True,
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        for name, role in (("admin", "admin"), ("bob", "user")):
            user = User(username=name, role=role)
            user.set_password("password")
            db.session.add(user)
        db.session.commit()
    failure_rate_limiter._storage.clear()
    init_auth_middleware(app)
    app.register_blueprint(auth_bp)
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


def _login(client, username: str = "admin") -> None:
    resp = client.post(
        "/api/auth/login", json={"username": username, "password": "password"}
    )
    assert resp.status_code == 200


def test_unauthenticated_import_rejected(auth_app, patched):
    client = _client(auth_app)
    resp = _upload(client, _opml("https://a.example.com/f"))
    assert resp.status_code == 401
    patched.add.assert_not_called()


def test_authenticated_import_skips_only_own_subscriptions(auth_app, patched):
    client = _client(auth_app)
    with auth_app.app_context():
        mine = Feed(title="mine", rss_url="https://mine.example.com/f")
        theirs = Feed(title="theirs", rss_url="https://theirs.example.com/f")
        db.session.add_all([mine, theirs])
        db.session.commit()
        admin = User.query.filter_by(username="admin").one()
        bob = User.query.filter_by(username="bob").one()
        db.session.add_all(
            [
                UserFeed(feed_id=mine.id, user_id=admin.id),
                UserFeed(feed_id=theirs.id, user_id=bob.id),
            ]
        )
        db.session.commit()
        admin_id = admin.id

    _login(client)
    resp = _upload(
        client,
        _opml(
            "https://mine.example.com/f",
            "https://theirs.example.com/f",
            "https://fresh.example.com/f",
        ),
    )

    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body["skipped_existing"] == ["https://mine.example.com/f"]
    assert body["added"] == [
        "https://theirs.example.com/f",
        "https://fresh.example.com/f",
    ]
    with auth_app.app_context():
        subscribed = {
            uf.feed.rss_url for uf in UserFeed.query.filter_by(user_id=admin_id).all()
        }
    assert subscribed == {
        "https://mine.example.com/f",
        "https://theirs.example.com/f",
        "https://fresh.example.com/f",
    }


def test_authenticated_import_reports_allowance_per_feed(auth_app, patched):
    client = _client(auth_app)
    with auth_app.app_context():
        bob = User.query.filter_by(username="bob").one()
        bob.manual_feed_allowance = 1
        db.session.commit()

    _login(client, "bob")
    resp = _upload(
        client, _opml("https://one.example.com/f", "https://two.example.com/f")
    )

    body = resp.get_json()
    assert body["added"] == ["https://one.example.com/f"]
    assert body["failed"][0]["url"] == "https://two.example.com/f"
    assert "allows 1 feeds" in body["failed"][0]["error"]
