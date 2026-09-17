"""Virtual, read-only hex rendering over a local buffer or completed read file."""

from collections import OrderedDict
from pathlib import Path

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widgets import Static


class MemoryView(ScrollView):
    """Expose the entire buffer without allocating a widget or string per byte.

    Only visible rows are rendered. File-backed buffers use a bounded block cache;
    scrolling never calls minipro. Eight-byte rows fit compact terminals, while
    wider terminals show sixteen bytes per row.
    """

    BLOCK_SIZE = 65536
    CACHE_BLOCKS = 8

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.data: bytes | None = None
        self.path: Path | None = None
        self.byte_count = 0
        self.bytes_per_row = 8
        self.blocks: OrderedDict[int, bytes] = OrderedDict()

    def set_source(self, data: bytes | None, path: Path | None) -> None:
        self.data, self.path = data, path
        self.byte_count = (
            len(data) if data is not None else path.stat().st_size if path else 0
        )
        self.blocks.clear()
        self.scroll_home(animate=False)
        self.layout_buffer()

    def on_resize(self) -> None:
        self.layout_buffer()

    def layout_buffer(self) -> None:
        self.bytes_per_row = 16 if self.size.width >= 84 else 8
        rows = (self.byte_count + self.bytes_per_row - 1) // self.bytes_per_row
        self.virtual_size = Size(self.size.width, rows)
        if self.is_mounted:
            headings = self.screen.query("#memory-columns")
            if headings:
                headings.first(Static).update(
                    Text(
                        f" {'ADDRESS':8}  │  {'HEX BYTES':<{self.hex_width}}  │  TEXT",
                        style="#8495a5",
                    )
                )
        self.refresh()

    @property
    def hex_width(self) -> int:
        """Width shared by the header and the padded final data row."""
        return self.bytes_per_row * 3 - 1 + (self.bytes_per_row // 8 - 1)

    def read_row(self, offset: int) -> bytes:
        if self.data is not None:
            return self.data[offset : offset + self.bytes_per_row]
        if self.path is None:
            return b""
        block = offset // self.BLOCK_SIZE
        if block not in self.blocks:
            with self.path.open("rb") as source:
                source.seek(block * self.BLOCK_SIZE)
                self.blocks[block] = source.read(self.BLOCK_SIZE)
            if len(self.blocks) > self.CACHE_BLOCKS:
                self.blocks.popitem(last=False)
        self.blocks.move_to_end(block)
        start = offset % self.BLOCK_SIZE
        return self.blocks[block][start : start + self.bytes_per_row]

    def render_line(self, y: int) -> Strip:
        line = y + self.scroll_offset.y
        style = Style(bgcolor="#10171f")
        offset = line * self.bytes_per_row
        if offset >= self.byte_count:
            return Strip.blank(self.size.width, style)
        try:
            data = self.read_row(offset)
        except OSError as error:
            return Strip(
                [Segment(f"Buffer unavailable: {error}", Style(color="red"))]
            ).crop_extend(0, self.size.width, style)
        groups = [
            " ".join(f"{value:02X}" for value in data[i : i + 8])
            for i in range(0, len(data), 8)
        ]
        hex_text = "  ".join(groups).ljust(self.hex_width)
        ascii_text = "".join(chr(value) if 32 <= value < 127 else "." for value in data)
        return Strip(
            [
                Segment(f" {offset:08X}  ", Style(color="#8495a5")),
                Segment("│  ", Style(color="#304453")),
                Segment(hex_text, Style(color="#dce5ee")),
                Segment("  │  ", Style(color="#304453")),
                Segment(ascii_text, Style(color="#afbdca")),
            ]
        ).crop_extend(0, self.size.width, style)
