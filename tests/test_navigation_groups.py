from html.parser import HTMLParser

from quant_studio.server import _layout, render_home, render_template_page


class Navigation(HTMLParser):
    def __init__(self):
        super().__init__()
        self.groups = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "details" and attrs.get("class") == "nav-group":
            self.groups.append(attrs)
        if tag == "a" and attrs.get("class", "").startswith("nav"):
            self.links.append(attrs)


def test_navigation_preserves_routes_and_opens_active_group():
    page = _layout("Data", "", "catalog")
    nav = Navigation()
    nav.feed(page)
    # Desktop and mobile expose the same five groups and routes.
    assert len(nav.groups) == 10
    assert [g["data-section"] for g in nav.groups if "open" in g] == ["data", "data"]
    expected = {
        "/",
        "/onboarding",
        "/setup",
        "/environment",
        "/platform-status",
        "/data",
        "/datasets",
        "/catalog",
        "/data-center",
        "/intake",
        "/projects",
        "/projects/findings",
        "/today",
        "/history",
        "/research",
        "/batches",
        "/durable-research",
        "/experiments",
        "/strategy",
        "/backtest",
        "/jobs",
        "/results",
        "/fund-reconcile",
        "/accounts",
        "/operations",
        "/delivery",
        "/logout",
    }
    assert {a["href"] for a in nav.links} == expected
    assert len(nav.links) == 2 * len(expected)
    assert [a["href"] for a in nav.links if a.get("aria-current") == "page"] == [
        "/catalog",
        "/catalog",
    ]


def test_fixture_and_unverified_template_inputs_are_visible_before_running():
    page = render_home()
    assert "默认数据：合成样例" in page
    assert "数据性质：按实际输入核验" in page
    assert "默认数据：合成样例" in render_template_page("futures-spread-fixture")
    assert "默认数据：合成样例" in render_template_page("synthetic-demo")
