"""Format display text without changing inputs, scripts or evidence output."""

import re
from html.parser import HTMLParser

from quant_studio.presentation import STATUS, display_time

_STAMP = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_RAW = {"script", "style", "textarea", "pre", "code"}


class DisplayHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out = []
        self.raw = []
        self.details = 0
        self.wrapped = []

    def handle_starttag(self, tag, attrs):
        if (
            tag in {"pre", "code"}
            and not self.raw
            and self.details == 0
            and "hidden" not in dict(attrs)
        ):
            self.out.append(
                '<details class="technical-details"><summary>技术详情</summary>'
            )
            self.wrapped.append(tag)
        if tag in _RAW:
            self.raw.append(tag)
        if tag == "details":
            self.details += 1
        self.out.append(self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        self.out.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        self.out.append(f"</{tag}>")
        if self.raw and self.raw[-1] == tag:
            self.raw.pop()
        if self.wrapped and self.wrapped[-1] == tag and not self.raw:
            self.out.append("</details>")
            self.wrapped.pop()
        if tag == "details":
            self.details = max(0, self.details - 1)

    def handle_data(self, data):
        if self.raw or self.details:
            self.out.append(data)
            return
        changed = _STAMP.sub(lambda m: display_time(m[0]), data)
        plain = changed.strip()
        for code, label in STATUS.items():
            if plain == code or plain in {"状态：" + code, "状态: " + code}:
                changed = changed.replace(code, label)
                break
        self.out.append(changed)

    def handle_entityref(self, name):
        self.out.append(f"&{name};")

    def handle_charref(self, name):
        self.out.append(f"&#{name};")

    def handle_comment(self, data):
        self.out.append(f"<!--{data}-->")


def display_html(body):
    parser = DisplayHTML()
    parser.feed(body)
    parser.close()
    return "".join(parser.out)
