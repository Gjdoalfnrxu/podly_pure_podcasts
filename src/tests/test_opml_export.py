from __future__ import annotations

from collections.abc import Generator
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest
from flask import Flask

from app.auth import AuthSettings
from app.auth.middleware import init_auth_middleware
from app.auth.state import failure_rate_limiter
from app.extensions import db
from app.models import Feed, FeedAccessToken, User, UserFeed
from app.opml import parse_opml
from app.routes.auth_routes import auth_bp
from app.routes.feed_routes import feed_bp
from app.routes.opml_routes import opml_bp
from app.writer.actions.feeds import create_feed_access_token_action

EXPORT = "/api/feeds/export-opml"
NASTY_TITLE = 'Tom & Jerry <Live> "Uncut"'


def _real_token_writer(name: str, params: dict, wait: bool = True):
    """Run the production writer action in-process (the writer is a separate
    process in prod), so token reuse is the real logic, not a fake."""
    from types import SimpleNamespace

    if name == "create_feed_access_token":
        data = create_feed_access_token_action(params)
        db.session.commit()
        return SimpleNamespace(success=True, data=data)
    return SimpleNamespace(success=True, data={})


@pytest.fixture
def writer():
    with mock.patch(
        "app.writer.client.writer_client.action", side_effect=_real_token_writer
    ) as action:
        yield action


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
        users = {}
        for name, role in (("admin", "admin"), ("bob", "user")):
            user = User(username=name, role=role)
            user.set_password("password")
            db.session.add(user)
            users[name] = user
        feeds = {
            "nasty": Feed(
                title=NASTY_TITLE, rss_url="https://src.example.com/a?x=1&y=2"
            ),
            "shared": Feed(title="Shared Show", rss_url="https://src.example.com/s"),
            "bobs": Feed(title="Bob Only", rss_url="https://src.example.com/b"),
        }
        db.session.add_all(feeds.values())
        db.session.commit()
        for user, feed in (
            ("admin", "nasty"),
            ("admin", "shared"),
            ("bob", "shared"),
            ("bob", "bobs"),
        ):
            db.session.add(UserFeed(user_id=users[user].id, feed_id=feeds[feed].id))
        db.session.commit()
    failure_rate_limiter._storage.clear()
    init_auth_middleware(app)
    for bp in (auth_bp, feed_bp, opml_bp):
        app.register_blueprint(bp)
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


def _login(app: Flask, username: str):
    client = app.test_client()
    resp = client.post(
        "/api/auth/login", json={"username": username, "password": "password"}
    )
    assert resp.status_code == 200
    return client


def _feed_id(app: Flask, title: str) -> int:
    with app.app_context():
        return Feed.query.filter_by(title=title).one().id


def _export(client, **headers) -> dict[str, str | None]:
    resp = client.get(EXPORT, headers=headers)
    assert resp.status_code == 200, resp.data
    assert resp.headers["Content-Disposition"] == (
        'attachment; filename="podly-feeds.opml"'
    )
    return {f.url: f.title for f in parse_opml(resp.data)}


def test_unauthenticated_export_rejected(auth_app, writer):
    resp = auth_app.test_client().get(EXPORT)
    assert resp.status_code == 401
    writer.assert_not_called()


def test_export_urls_match_copy_protected_feed_and_round_trip(auth_app, writer):
    client = _login(auth_app, "admin")
    exported = _export(client)

    copied = {}
    for title in (NASTY_TITLE, "Shared Show"):
        resp = client.post(f"/api/feeds/{_feed_id(auth_app, title)}/share-link")
        assert resp.status_code == 201
        copied[resp.get_json()["url"]] = title

    # Export -> our own OPML parser -> exactly the copy-button URLs and titles.
    assert exported == copied


def test_export_escapes_titles_and_urls(auth_app, writer):
    client = _login(auth_app, "admin")
    raw = client.get(EXPORT).data

    assert b"Tom &amp; Jerry &lt;Live&gt; &quot;Uncut&quot;" in raw
    assert b"<Live>" not in raw
    # Every '&' between query params is escaped in the attribute.
    assert b"&amp;feed_secret=" in raw
    assert b"&feed_secret=" not in raw.replace(b"&amp;feed_secret=", b"")
    assert NASTY_TITLE in _export(client).values()


def test_export_lists_only_own_subscriptions_with_own_tokens(auth_app, writer):
    admin_urls = _export(_login(auth_app, "admin"))
    bob_urls = _export(_login(auth_app, "bob"))

    def feed_ids(urls):
        return {int(urlparse(u).path.rsplit("/", 1)[1]) for u in urls}

    # Admin role sees every feed in /feeds, but export is subscriptions only.
    assert feed_ids(admin_urls) == {
        _feed_id(auth_app, NASTY_TITLE),
        _feed_id(auth_app, "Shared Show"),
    }
    assert feed_ids(bob_urls) == {
        _feed_id(auth_app, "Shared Show"),
        _feed_id(auth_app, "Bob Only"),
    }

    with auth_app.app_context():
        ids = {u.username: u.id for u in User.query.all()}
        for owner, urls in (("admin", admin_urls), ("bob", bob_urls)):
            for url in urls:
                token_id = parse_qs(urlparse(url).query)["feed_token"][0]
                token = FeedAccessToken.query.filter_by(token_id=token_id).one()
                assert token.user_id == ids[owner]
    # The shared feed gets a different token per user.
    assert not set(admin_urls) & set(bob_urls)


def test_repeat_export_reuses_tokens(auth_app, writer):
    client = _login(auth_app, "admin")
    first = _export(client)
    with auth_app.app_context():
        count = FeedAccessToken.query.count()
    second = _export(client)
    with auth_app.app_context():
        assert FeedAccessToken.query.count() == count == 2
    assert first == second


def test_export_honours_forwarded_https(auth_app, writer):
    client = _login(auth_app, "admin")
    urls = _export(client, **{"X-Forwarded-Proto": "https"})
    assert urls
    assert all(u.startswith("https://localhost/feed/") for u in urls)


def test_no_auth_export_lists_all_feeds_with_plain_urls(app, writer):
    with app.app_context():
        db.session.add_all(
            [
                Feed(title="One & Two", rss_url="https://src.example.com/1"),
                Feed(title="Three", rss_url="https://src.example.com/3"),
            ]
        )
        db.session.commit()
        ids = {f.title: f.id for f in Feed.query.all()}
    app.register_blueprint(opml_bp)
    with app.app_context():
        exported = _export(app.test_client())
    assert exported == {
        f"http://localhost/feed/{ids['One & Two']}": "One & Two",
        f"http://localhost/feed/{ids['Three']}": "Three",
    }
    writer.assert_not_called()
