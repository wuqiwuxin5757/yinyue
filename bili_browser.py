"""Read Bilibili video cards from the user's Chrome window using Windows accessibility."""
import re
import time
from contextlib import contextmanager
from urllib.parse import parse_qs, urlencode, urlsplit


_PLAYABLE = re.compile(
    r"https://www\.bilibili\.com/(?:[\w-]+/)?"
    r"(video/(?:BV[\w]+|av\d+)|(?:cheese|bangumi)/play/(?:ep|ss)[1-9]\d*)(?:[/?#]|$)", re.I
)


@contextmanager
def _window_accessibility(hwnd):
    import comtypes
    import comtypes.client

    comtypes.CoInitialize()
    try:
        uia = comtypes.client.GetModule("UIAutomationCore.dll")
        automation = comtypes.client.CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation)
        yield uia, automation, automation.ElementFromHandle(hwnd)
    finally:
        comtypes.CoUninitialize()


class BilibiliBrowser:
    def __init__(self, open_tab, foreground):
        self.open_tab = open_tab
        self.foreground = foreground
        self.tab_id = None
        self.search_tab = None
        self.query = ""

    def _matches(self, url):
        if not isinstance(url, str):
            return False
        parsed = urlsplit(url)
        return (parsed.hostname == "search.bilibili.com" and parsed.path.rstrip("/").rsplit("/", 1)[-1] in ("video", "all")
                and parse_qs(parsed.query).get("keyword") == [self.query])

    def search(self, query):
        self.query = query
        self.tab_id = None
        self.search_tab = None
        previous_tabs = set()

        def remember_existing(hwnd):
            # An identical search may already be open in this window.
            with _window_accessibility(hwnd) as (uia, automation, root):
                previous_tabs.update(tuple(tab.GetRuntimeId()) for tab in self._tabs(uia, automation, root))

        url = "https://search.bilibili.com/video?" + urlencode({"keyword": query})
        hwnd = self.open_tab(url, before_open=remember_existing)
        if not hwnd or not (tab := self._read_page(hwnd, self._matches, previous_tabs=previous_tabs)):
            return False
        self.tab_id = hwnd
        self.search_tab = tab
        return True

    def results(self, wait=4.5):
        if not self.tab_id or not self.foreground(self.tab_id):
            return []
        return self._read_page(self.tab_id, self._matches, read_cards=True, wait=wait) or []

    def _read_page(self, hwnd, matches, read_cards=False, wait=9, previous_tabs=None):
        import comtypes

        try:
            with _window_accessibility(hwnd) as (uia, automation, root):
                deadline = time.monotonic() + wait
                while time.monotonic() < deadline:
                    try:
                        selected_tab = None
                        if read_cards or previous_tabs is not None:
                            selected_tab = self._selected_tab(uia, automation, root)
                        if read_cards and selected_tab != self.search_tab:
                            self._select_search_tab(uia, automation, root)
                        elif previous_tabs is None or (selected_tab and selected_tab not in previous_tabs):
                            documents = root.FindAll(uia.TreeScope_Descendants, automation.CreatePropertyCondition(30003, 50030))
                            for index in range(documents.Length if documents else 0):
                                document = documents.GetElement(index)
                                if document.CurrentIsOffscreen or not matches(document.GetCurrentPropertyValue(30045)):
                                    continue
                                if not read_cards:
                                    return selected_tab if previous_tabs is not None else True
                                if items := self._cards(uia, automation, document):
                                    return items
                    except (comtypes.COMError, ValueError):
                        # Chrome can replace the accessible document while navigation is in flight.
                        pass
                    time.sleep(0.2)
            return None
        except (comtypes.COMError, OSError):
            return None

    @staticmethod
    def _tabs(uia, automation, root):
        tabs = root.FindAll(uia.TreeScope_Descendants, automation.CreatePropertyCondition(30003, 50019))
        for index in range(tabs.Length if tabs else 0):
            yield tabs.GetElement(index)

    def _selected_tab(self, uia, automation, root):
        for tab in self._tabs(uia, automation, root):
            pattern = tab.GetCurrentPattern(10010)
            if pattern and pattern.QueryInterface(uia.IUIAutomationSelectionItemPattern).CurrentIsSelected:
                return tuple(tab.GetRuntimeId())
        return None

    def _select_search_tab(self, uia, automation, root):
        for tab in self._tabs(uia, automation, root):
            if tuple(tab.GetRuntimeId()) == self.search_tab:
                tab.GetCurrentPattern(10010).QueryInterface(uia.IUIAutomationSelectionItemPattern).Select()
                return True
        return False

    @staticmethod
    def _cards(uia, automation, document):
        slots = {}
        grids = set()
        walker = automation.RawViewWalker
        links = document.FindAll(uia.TreeScope_Descendants, automation.CreatePropertyCondition(30003, 50005))
        for index in range(links.Length if links else 0):
            link = links.GetElement(index)
            url = link.GetCurrentPropertyValue(30045)
            if not isinstance(url, str) or not _PLAYABLE.match(url):
                continue
            title = (link.CurrentName or "").strip()
            node = link
            title_link = False
            for _ in range(8):
                if not node:
                    break
                classes = (node.CurrentClassName or "").split()
                title_link |= "bili-video-card__info--right" in classes
                if "video-list-item" in classes:
                    grid = walker.GetParentElement(node)
                    if grid:
                        grid_id = tuple(grid.GetRuntimeId())
                        if grid_id not in grids:
                            grids.add(grid_id)
                            # Chrome exposes these layout containers in the raw view, not FindAll's control view.
                            sibling = walker.GetFirstChildElement(grid)
                            while sibling:
                                if "video-list-item" in (sibling.CurrentClassName or "").split():
                                    rect = sibling.CurrentBoundingRectangle
                                    if rect.right > rect.left and rect.bottom > rect.top:
                                        slots[tuple(sibling.GetRuntimeId())] = (
                                            rect.top, rect.left, {"title": "", "url": "", "author": ""})
                                sibling = walker.GetNextSiblingElement(sibling)
                    slot = slots.get(tuple(node.GetRuntimeId()))
                    if slot and title and (title_link or not slot[2]["url"]):
                        slot[2].update(title=title, url=url)
                    break
                node = walker.GetParentElement(node)
        if not any(item["url"] for _, _, item in slots.values()):
            return []
        rows = []
        for top, left, item in sorted(slots.values(), key=lambda slot: slot[:2]):
            if not rows or top - rows[-1][0] > 16:
                rows.append((top, []))
            rows[-1][1].append((left, item))
        return [item for _, row in rows for _, item in sorted(row, key=lambda placed: placed[0])]

    def play(self, item):
        requested = _PLAYABLE.match(item["url"])
        if not requested:
            raise RuntimeError("B站结果里没有有效的视频链接。")

        def matches(url):
            opened = _PLAYABLE.match(url) if isinstance(url, str) else None
            return opened is not None and opened[1] == requested[1]

        hwnd = self.open_tab(item["url"], self.tab_id)
        if not hwnd or not self._read_page(hwnd, matches):
            raise RuntimeError("没有确认已在原来的 Chrome 窗口打开视频。")

