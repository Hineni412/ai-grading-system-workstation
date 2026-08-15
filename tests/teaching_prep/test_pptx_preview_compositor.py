from __future__ import annotations

import io
import zipfile

import pytest
from PIL import Image

from backend.teaching_prep.infrastructure.materials.pptx_preview import (
    PREVIEW_HEIGHT,
    PREVIEW_WIDTH,
    render_pptx_slide_preview,
)


_SLIDE_W = 12_192_000
_SLIDE_H = 6_858_000
_NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
}


def _px(x_emu: int, y_emu: int) -> tuple[int, int]:
    return (
        round(x_emu / _SLIDE_W * PREVIEW_WIDTH),
        round(y_emu / _SLIDE_H * PREVIEW_HEIGHT),
    )


def _dark(pixel: tuple[int, int, int], limit: int = 90) -> bool:
    return pixel[0] < limit and pixel[1] < limit and pixel[2] < limit


def _ink_count(image: Image.Image, box: tuple[int, int, int, int]) -> int:
    x0, y0, x1, y1 = box
    count = 0
    for x in range(max(0, x0), min(image.width, x1)):
        for y in range(max(0, y0), min(image.height, y1)):
            if _dark(image.getpixel((x, y))):
                count += 1
    return count


def _shape(
    *,
    tag: str = "p:sp",
    x: int,
    y: int,
    cx: int,
    cy: int,
    body: str,
    extra_sp_pr: str = "",
) -> str:
    return f"""
    <{tag}>
      <p:nvSpPr>
        <p:cNvPr id="2" name="Shape"/>
        <p:cNvSpPr/>
        <p:nvPr/>
      </p:nvSpPr>
      <p:spPr>
        <a:xfrm>
          <a:off x="{x}" y="{y}"/>
          <a:ext cx="{cx}" cy="{cy}"/>
        </a:xfrm>
        {extra_sp_pr}
      </p:spPr>
      {body}
    </{tag}>
    """


def _render(shapes: str, *, media: dict[str, bytes] | None = None, rels: str = "") -> Image.Image:
    slide = f"""
    <p:sld
      xmlns:p="{_NS['p']}"
      xmlns:a="{_NS['a']}"
      xmlns:r="{_NS['r']}"
      xmlns:m="{_NS['m']}">
      <p:cSld>
        <p:bg>
          <p:bgPr>
            <a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill>
          </p:bgPr>
        </p:bg>
        <p:spTree>
          {shapes}
        </p:spTree>
      </p:cSld>
    </p:sld>
    """.strip()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", slide)
        if rels:
            archive.writestr("ppt/slides/_rels/slide1.xml.rels", rels)
        for name, payload in (media or {}).items():
            archive.writestr(name, payload)
    buffer.seek(0)
    with zipfile.ZipFile(buffer) as archive:
        png = render_pptx_slide_preview(
            archive.read("ppt/slides/slide1.xml"),
            archive=archive,
            slide_name="ppt/slides/slide1.xml",
            slide_width=_SLIDE_W,
            slide_height=_SLIDE_H,
        )
    return Image.open(io.BytesIO(png)).convert("RGB")


def test_vertical_cjk_stacks_characters_instead_of_a_horizontal_line() -> None:
    x, y, cx, cy = 800_000, 400_000, 7_000_000, 5_000_000
    image = _render(
        _shape(
            x=x,
            y=y,
            cx=cx,
            cy=cy,
            extra_sp_pr='<a:noFill/>',
            body="""
            <p:txBody>
              <a:bodyPr vert="eaVert"/>
              <a:p><a:r><a:t>勾股</a:t></a:r></a:p>
            </p:txBody>
            """,
        )
    )
    left, top = _px(x, y)
    char = 42
    col = left + 8
    below = _ink_count(image, (col, top + char + 6, col + char, top + char * 2 + 12))
    beside = _ink_count(
        image,
        (col + char + 8, top + 6, col + char * 2 + 16, top + char + 4),
    )

    assert below > 20
    assert below > beside * 2


def test_omml_fraction_is_stacked_with_a_bar_not_a_single_line() -> None:
    x, y, cx, cy = 1_200_000, 800_000, 9_000_000, 4_800_000
    image = _render(
        _shape(
            x=x,
            y=y,
            cx=cx,
            cy=cy,
            extra_sp_pr='<a:noFill/>',
            body="""
            <p:txBody>
              <a:bodyPr/>
              <a:p>
                <m:oMath>
                  <m:f>
                    <m:num><m:r><m:t>1</m:t></m:r></m:num>
                    <m:den><m:r><m:t>2</m:t></m:r></m:den>
                  </m:f>
                </m:oMath>
              </a:p>
            </p:txBody>
            """,
        )
    )
    left, top = _px(x, y)
    right, bottom = _px(x + cx, y + cy)
    mid_x = (left + right) // 2
    mid_y = (top + bottom) // 2
    upper = _ink_count(image, (mid_x - 50, mid_y - 90, mid_x + 50, mid_y - 10))
    lower = _ink_count(image, (mid_x - 50, mid_y + 10, mid_x + 50, mid_y + 90))
    bar_pixels = [
        image.getpixel((mid_x + offset, mid_y))
        for offset in range(-36, 37, 2)
    ]

    assert upper > 8
    assert lower > 8
    assert sum(1 for pixel in bar_pixels if _dark(pixel, 120)) >= 8


def test_omml_superscript_draws_base_and_raised_exponent() -> None:
    x, y, cx, cy = 1_200_000, 1_200_000, 8_000_000, 3_600_000
    image = _render(
        _shape(
            x=x,
            y=y,
            cx=cx,
            cy=cy,
            extra_sp_pr='<a:noFill/>',
            body="""
            <p:txBody>
              <a:bodyPr/>
              <a:p>
                <m:oMath>
                  <m:sSup>
                    <m:e><m:r><m:t>a</m:t></m:r></m:e>
                    <m:sup><m:r><m:t>2</m:t></m:r></m:sup>
                  </m:sSup>
                </m:oMath>
              </a:p>
            </p:txBody>
            """,
        )
    )
    left, top = _px(x, y)
    right, bottom = _px(x + cx, y + cy)
    mid_x = (left + right) // 2
    mid_y = (top + bottom) // 2
    base = _ink_count(image, (mid_x - 90, mid_y - 20, mid_x + 10, mid_y + 70))
    raised = _ink_count(image, (mid_x - 10, mid_y - 80, mid_x + 90, mid_y - 5))

    assert base > 8
    assert raised > 5


def test_outline_rect_without_fill_keeps_visible_stroke() -> None:
    x, y, cx, cy = 2_000_000, 1_500_000, 4_000_000, 3_000_000
    image = _render(
        _shape(
            x=x,
            y=y,
            cx=cx,
            cy=cy,
            extra_sp_pr="""
            <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
            <a:noFill/>
            <a:ln w="38100">
              <a:solidFill><a:srgbClr val="1C2733"/></a:solidFill>
            </a:ln>
            """,
            body="",
        )
    )
    left, top = _px(x, y)
    right, bottom = _px(x + cx, y + cy)
    mid_x = (left + right) // 2
    mid_y = (top + bottom) // 2
    edge = image.getpixel((mid_x, top + 1))
    center = image.getpixel((mid_x, mid_y))

    assert _dark(edge, 140)
    assert center[0] > 200 and center[1] > 200 and center[2] > 200


def test_connector_line_draws_stroke_across_the_box() -> None:
    x, y, cx, cy = 1_000_000, 1_000_000, 6_000_000, 3_000_000
    image = _render(
        f"""
        <p:cxnSp>
          <p:nvCxnSpPr>
            <p:cNvPr id="8" name="Connector"/>
            <p:cNvCxnSpPr/>
            <p:nvPr/>
          </p:nvCxnSpPr>
          <p:spPr>
            <a:xfrm>
              <a:off x="{x}" y="{y}"/>
              <a:ext cx="{cx}" cy="{cy}"/>
            </a:xfrm>
            <a:prstGeom prst="line"><a:avLst/></a:prstGeom>
            <a:ln w="38100">
              <a:solidFill><a:srgbClr val="1C2733"/></a:solidFill>
            </a:ln>
          </p:spPr>
        </p:cxnSp>
        """
    )
    left, top = _px(x, y)
    right, bottom = _px(x + cx, y + cy)
    mid = image.getpixel(((left + right) // 2, (top + bottom) // 2))
    assert _dark(mid, 150)


def test_graphic_frame_pastes_embedded_preview_image() -> None:
    from PIL import Image as PilImage

    marker = io.BytesIO()
    PilImage.new("RGB", (32, 32), (220, 24, 48)).save(marker, format="PNG")
    x, y, cx, cy = 3_000_000, 2_000_000, 3_000_000, 2_400_000
    image = _render(
        f"""
        <p:graphicFrame>
          <p:nvGraphicFramePr>
            <p:cNvPr id="9" name="Equation"/>
            <p:cNvGraphicFramePr/>
            <p:nvPr/>
          </p:nvGraphicFramePr>
          <p:xfrm>
            <a:off x="{x}" y="{y}"/>
            <a:ext cx="{cx}" cy="{cy}"/>
          </p:xfrm>
          <a:graphic>
            <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/ole">
              <a:blip r:embed="rId2"/>
              <m:oMath><m:r><m:t>XXXX</m:t></m:r></m:oMath>
            </a:graphicData>
          </a:graphic>
        </p:graphicFrame>
        """,
        media={"ppt/media/image1.png": marker.getvalue()},
        rels="""
        <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
          <Relationship
            Id="rId2"
            Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
            Target="../media/image1.png"/>
        </Relationships>
        """,
    )
    left, top = _px(x, y)
    right, bottom = _px(x + cx, y + cy)
    sample = image.getpixel(((left + right) // 2, (top + bottom) // 2))
    assert sample[0] > 180
    assert sample[1] < 80
    assert sample[2] < 90


def _red_emf_bytes() -> bytes | None:
    import ctypes
    from ctypes import wintypes

    handle = ctypes.c_void_p
    gdi32 = ctypes.WinDLL("gdi32")
    user32 = ctypes.WinDLL("user32")
    user32.GetDC.restype = handle
    user32.GetDC.argtypes = [handle]
    user32.ReleaseDC.argtypes = [handle, handle]
    gdi32.CreateEnhMetaFileW.restype = handle
    gdi32.CreateEnhMetaFileW.argtypes = [
        handle,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    gdi32.CreateSolidBrush.restype = handle
    gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
    gdi32.SelectObject.restype = handle
    gdi32.SelectObject.argtypes = [handle, handle]
    gdi32.Rectangle.argtypes = [
        handle,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
    ]
    gdi32.DeleteObject.argtypes = [handle]
    gdi32.CloseEnhMetaFile.restype = handle
    gdi32.CloseEnhMetaFile.argtypes = [handle]
    user32.FillRect.restype = ctypes.c_int
    user32.FillRect.argtypes = [handle, ctypes.c_void_p, handle]
    gdi32.GetEnhMetaFileBits.restype = ctypes.c_uint
    gdi32.GetEnhMetaFileBits.argtypes = [handle, ctypes.c_uint, ctypes.c_void_p]
    gdi32.DeleteEnhMetaFile.argtypes = [handle]

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    hdc_ref = user32.GetDC(None)
    if not hdc_ref:
        return None
    try:
        frame = RECT(0, 0, 2117, 1588)
        hdc = gdi32.CreateEnhMetaFileW(hdc_ref, None, ctypes.byref(frame), None)
        if not hdc:
            return None
        brush = gdi32.CreateSolidBrush(0x000000FF)
        fill_rect = RECT(0, 0, 80, 60)
        user32.FillRect(hdc, ctypes.byref(fill_rect), brush)
        gdi32.DeleteObject(brush)
        hemf = gdi32.CloseEnhMetaFile(hdc)
        if not hemf:
            return None
        size = gdi32.GetEnhMetaFileBits(hemf, 0, None)
        if size <= 0:
            gdi32.DeleteEnhMetaFile(hemf)
            return None
        buf = ctypes.create_string_buffer(size)
        gdi32.GetEnhMetaFileBits(hemf, size, buf)
        gdi32.DeleteEnhMetaFile(hemf)
        return buf.raw
    finally:
        user32.ReleaseDC(None, hdc_ref)


def test_emf_formula_preview_is_rasterized_into_the_slide() -> None:
    payload = _red_emf_bytes()
    if not payload:
        pytest.skip("this computer cannot create an EMF test picture")
    x, y, cx, cy = 2_400_000, 1_800_000, 4_800_000, 3_000_000
    image = _render(
        f"""
        <p:pic>
          <p:nvPicPr>
            <p:cNvPr id="4" name="EquationMetafile"/>
            <p:cNvPicPr/>
            <p:nvPr/>
          </p:nvPicPr>
          <p:blipFill>
            <a:blip r:embed="rId2"/>
            <a:stretch><a:fillRect/></a:stretch>
          </p:blipFill>
          <p:spPr>
            <a:xfrm>
              <a:off x="{x}" y="{y}"/>
              <a:ext cx="{cx}" cy="{cy}"/>
            </a:xfrm>
          </p:spPr>
        </p:pic>
        """,
        media={"ppt/media/image1.emf": payload},
        rels="""
        <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
          <Relationship
            Id="rId2"
            Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
            Target="../media/image1.emf"/>
        </Relationships>
        """,
    )
    left, top = _px(x, y)
    right, bottom = _px(x + cx, y + cy)
    sample = image.getpixel(((left + right) // 2, (top + bottom) // 2))
    assert sample[0] > 140
    assert sample[1] < 120
    assert sample[2] < 120
