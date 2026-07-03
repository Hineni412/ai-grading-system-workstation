from io import BytesIO

from PIL import Image

from reference_image_preparation import prepare_reference_image


def png(size: tuple[int, int], mode: str = "RGB") -> bytes:
    color = (255, 255, 255, 128) if mode == "RGBA" else "white"
    image = Image.new(mode, size, color)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_reference_image_is_downscaled_without_upscaling() -> None:
    large = prepare_reference_image(png((1191, 2645)))
    small = prepare_reference_image(png((600, 400)))

    assert (large.width, large.height) == (922, 2048)
    assert (small.width, small.height) == (600, 400)
    assert (large.original_width, large.original_height) == (1191, 2645)
    with Image.open(BytesIO(large.blob)) as decoded:
        assert decoded.mode == "RGB"
        assert decoded.format == "JPEG"


def test_rgba_is_flattened_onto_white() -> None:
    prepared = prepare_reference_image(png((100, 100), mode="RGBA"))
    with Image.open(BytesIO(prepared.blob)) as decoded:
        assert decoded.mode == "RGB"


def test_output_is_smaller_for_large_source() -> None:
    source = png((1600, 2400))
    prepared = prepare_reference_image(source)
    assert len(prepared.blob) < len(source)
