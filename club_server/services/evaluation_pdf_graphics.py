"""SVG sources of the PDF's rating graphics (#535, R63).

Ported from the prototype's ``survey_pdf_graphics.dart``: each graphic is
drawn in a 100 × 100 box and scaled onto the page.
"""

import math

STAR_POINTS = 5
# Inner radius of a star as a share of its outer radius.
STAR_INNER = 0.45
# Distance of a star's points from the box edge.
STAR_EDGE = 6
# Outline width of an empty star.
STAR_STROKE = 7
SVG_BOX = 100
SVG_OPEN = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
SVG_CLOSE = "</svg>"


def _n(value: float) -> str:
    """A coordinate, two decimals."""
    return f"{value:.2f}"


def star_svg(*, filled: bool, colour: str) -> str:
    """A five-pointed star, filled with ``colour`` or outlined in it."""
    centre = SVG_BOX / 2
    outer = centre - STAR_EDGE
    points = []
    for i in range(STAR_POINTS * 2):
        radius = outer if i % 2 == 0 else outer * STAR_INNER
        angle = -math.pi / 2 + i * math.pi / STAR_POINTS
        x = centre + radius * math.cos(angle)
        y = centre + radius * math.sin(angle)
        points.append(f"{_n(x)},{_n(y)}")
    paint = (
        f'fill="{colour}"'
        if filled
        else f'fill="none" stroke="{colour}" stroke-width="{STAR_STROKE}"'
    )
    star = f'<polygon points="{" ".join(points)}" {paint} stroke-linejoin="round"/>'
    return f"{SVG_OPEN}{star}{SVG_CLOSE}"


def pie_svg(*, fraction: float, fill: str, track: str, ring: float) -> str:
    """A ring ``fraction`` (0..1) of the way round in ``fill`` over a ``track``.

    Drawn from twelve o'clock clockwise; ``ring`` is the ring's width as a
    share of the box.
    """
    centre = SVG_BOX / 2
    width = SVG_BOX * ring
    radius = centre - width / 2
    share = min(max(fraction, 0.0), 1.0)

    def circle(colour: str) -> str:
        return (
            f'<circle cx="{_n(centre)}" cy="{_n(centre)}" r="{_n(radius)}" '
            f'fill="none" stroke="{colour}" stroke-width="{_n(width)}"/>'
        )

    if share >= 1:
        arc = circle(fill)
    elif share <= 0:
        arc = ""
    else:
        angle = -math.pi / 2 + share * 2 * math.pi
        x = centre + radius * math.cos(angle)
        y = centre + radius * math.sin(angle)
        large = 1 if share > 0.5 else 0
        arc = (
            f'<path d="M {_n(centre)} {_n(centre - radius)} '
            f'A {_n(radius)} {_n(radius)} 0 {large} 1 {_n(x)} {_n(y)}" '
            f'fill="none" stroke="{fill}" stroke-width="{_n(width)}" '
            'stroke-linecap="butt"/>'
        )
    return f"{SVG_OPEN}{circle(track)}{arc}{SVG_CLOSE}"
