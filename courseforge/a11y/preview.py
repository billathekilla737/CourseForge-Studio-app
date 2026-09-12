"""A worked example of each look, rendered by the restyler itself.

The point of these is to answer "what will clean actually do to my pages?"
before someone commits a whole course to it. So they are not drawings of what
the looks are supposed to do. The sample below goes through `transform_components`,
the same function the real run uses, which means a preview cannot drift from the
behaviour, and a change to the brand or to the transform shows up here first.

The sample is authored the way the template authors a page before any pass:
borders and headings, no background fills. That matters, because `classify`
skips a component that already has a `background:` (the rule that makes a second
run a no-op), so a pre-filled sample would render three identical previews.
"""
from __future__ import annotations

from . import restyle

# What each look is, in the words the UI shows. Kept here so the description and
# the render it describes cannot disagree.
LOOK_NOTES = {
    "clean": "No background fills. Navy headings and borders only. "
             "Scores 0 use-of-colour advisories in Ally.",
    "hybrid": "Filled navy hero and footer. Adds advisory colour flags, about 2 per page.",
    "rich": "Every component filled. Adds advisory colour flags, up to 7 per page.",
}
LOOK_LABELS = {"clean": "Clean", "hybrid": "Hybrid", "rich": "Rich"}


def sample_body() -> str:
    """One page holding every component the looks differ on.

    Built from the brand rather than hardcoded, so a school that swaps the
    palette sees its own colours in the preview.
    """
    restyle.configure()
    c, f = restyle.C, restyle.F
    navy, gold = restyle.NAVY, restyle.GOLD
    body, muted, hair = c["body_text"], c["muted_text"], c["hairline"]

    def div(style: str, inner: str) -> str:
        return '<div style="%s">%s</div>' % (style, inner)

    hero = div(
        "padding: 22px 24px; border-radius: 8px; border-top: 5px solid %s; "
        "border-left: 8px solid %s;" % (gold, navy),
        '<div style="font-size: 13px; letter-spacing: 0.06em; text-transform: uppercase; '
        'font-weight: 700; color: %s;">IMT 1213 &middot; Week 3 &middot; Lesson</div>'
        '<h2 style="margin: 6px 0 0; font-size: 26px; font-family: %s; color: %s;">'
        "Mechanics, Dynamics and Aesthetics</h2>" % (gold, f["display"], navy))

    card = div(
        "margin-top: 18px; padding: 18px; border-radius: 8px; border: 1px solid %s; "
        "border-top: 4px solid %s; font-size: 14px; color: %s;" % (hair, gold, body),
        '<h3 style="margin: 0 0 8px; font-family: %s; color: %s;">What you will do</h3>'
        "<p style=\"margin: 0;\">Pick a game you know well and take it apart one layer "
        "at a time, then write up what you found.</p>" % (f["display"], navy))

    goal = div(
        "margin-top: 14px; padding: 14px 16px; border-left: 4px solid %s; "
        "border-radius: 6px; font-size: 14px; color: %s;" % (c["blue"], body),
        '<b style="color: %s;">Goal</b><p style="margin: 6px 0 0;">Tell a mechanic from '
        "a dynamic, and say which one the player actually feels.</p>" % c["blue"])

    alert = div(
        "margin-top: 14px; padding: 14px 16px; border-left: 5px solid %s; "
        "border-radius: 6px; font-size: 14px; color: %s;" % (c["red"], body),
        '<b style="color: %s;">Due Friday</b><p style="margin: 6px 0 0;">Late work loses '
        "a letter grade per day, so hand in what you have.</p>" % c["red"])

    callout = div(
        "margin-top: 14px; padding: 14px 16px; border-left: 4px solid %s; "
        "border-radius: 6px; font-size: 14px; color: %s;" % (gold, body),
        '<b>Worth knowing</b><p style="margin: 6px 0 0;">The reading is short. Do it '
        "before you start, not after you get stuck.</p>")

    footer = div(
        "margin-top: 18px; padding: 16px 18px; border-radius: 8px; "
        "border-top: 5px solid %s; font-size: 13px; color: %s;" % (gold, muted),
        "<p style=\"margin: 0;\">Questions go in the Week 3 discussion so everyone "
        "sees the answer.</p>")

    return (restyle.WRAP_OPEN + hero + card + goal + alert + callout + footer + "</div>")


def _styled(html: str, look: str) -> str:
    """`transform_components` hands back a bare string for clean and a
    (html, fills_added) pair for the other two. Take the html either way."""
    out = restyle.transform_components(html, look)
    return out[0] if isinstance(out, tuple) else out


def render(look: str) -> str:
    """The sample as this look would leave it."""
    if look not in LOOK_NOTES:
        raise ValueError("unknown look %r" % look)
    return _styled(sample_body(), look)


def all_looks() -> list[dict]:
    sample = sample_body()
    out = []
    for look in ("clean", "hybrid", "rich"):
        out.append({
            "id": look,
            "label": LOOK_LABELS[look],
            "hint": LOOK_NOTES[look],
            "default": look == "clean",
            "html": _styled(sample, look),
        })
    return out
