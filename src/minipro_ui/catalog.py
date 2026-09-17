"""Predictable fuzzy matching across the complete minipro device catalog."""

from collections.abc import Iterable


def match_devices(names: Iterable[str], query: str, limit: int = 300) -> list[str]:
    """Rank case-insensitive subsequences, preferring exact and contiguous matches.

    Spaces in the query are ignored, so a part number and package can be entered
    together. The scan is linear in each candidate's length; large catalogs do not
    require an expensive fuzzy alignment for every keystroke. Ties retain catalog
    order, including recent-device ordering supplied by the caller.
    """
    needle = "".join(query.casefold().split())
    if not needle:
        return list(names)[:limit]
    ranked: list[tuple[float, int, str]] = []
    for order, name in enumerate(names):
        candidate = name.casefold()
        position = 0
        first = -1
        for character in needle:
            index = candidate.find(character, position)
            if index < 0:
                break
            if first < 0:
                first = index
            position = index + 1
        else:
            score = len(needle) / max(1, position - first)
            if candidate == needle:
                score += 100
            elif candidate.startswith(needle):
                score += 10
            elif needle in candidate:
                score += 5
            ranked.append((-score, order, name))
    ranked.sort()
    return [name for _, _, name in ranked[:limit]]
