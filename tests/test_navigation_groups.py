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


def test_navigation_has_five_destinations_and_contextual_tools():
    page = _layout("Data", "", "catalog")
    nav = Navigation()
    nav.feed(page)
    expected = {
        "/",
        "/projects",
        "/data",
        "/experiments",
        "/accounts",
        "/projects/new",
        "/strategy",
        "/setup",
        "/logout",
    }
    assert {a["href"] for a in nav.links} == expected
    assert len(nav.links) == 2 * len(expected)
    assert [a["href"] for a in nav.links if a.get("aria-current") == "page"] == [
        "/data",
        "/data",
    ]
    for route in ("/datasets", "/catalog", "/data-center", "/intake"):
        assert f'href="{route}"' in page
    assert 'aria-label="相关功能"' in page


def test_fixture_and_unverified_template_inputs_are_visible_before_running():
    page = render_home()
    assert "默认数据：合成样例" in page
    assert "数据性质：按实际输入核验" in page
    assert "默认数据：合成样例" in render_template_page("futures-spread-fixture")
    assert "默认数据：合成样例" in render_template_page("synthetic-demo")
