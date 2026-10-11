"""Read-only metadata from actual Stagehand cross-frame accessibility snapshots."""

import re


def frame_control(selector, snapshot):
    path = selector.removeprefix("xpath=")
    if "/iframe[" not in path:
        raise ValueError("Cross-frame fallback accepts only SDK-observed absolute iframe XPath.")
    ids = [identifier for identifier, value in snapshot["xpath_map"].items() if value == path]
    lines = snapshot["formatted_tree"].splitlines()
    matches = [
        re.search(r"\[" + re.escape(identifier) + r"\]\s+([^:]+):\s*(.+)$", line)
        for identifier in ids
        for line in lines
    ]
    matches = [match for match in matches if match]
    if len(matches) != 1:
        raise ValueError("Cross-frame control has no unique fresh accessibility label.")
    role, label = matches[0].groups()
    tag = re.search(r"/(input|textarea|select|button|a)\[\d+\]$", path)
    if not tag or role not in ("textbox", "combobox", "button", "link"):
        raise ValueError("Cross-frame control is outside the bounded input/click contract.")
    parents = [
        (snapshot["xpath_map"].get(key, ""), url)
        for key, url in snapshot["url_map"].items()
        if path.startswith(snapshot["xpath_map"].get(key, "") + "/")
    ]
    if not parents:
        raise ValueError("Cross-frame control has no observed frame URL.")
    frame_url = max(parents, key=lambda pair: len(pair[0]))[1]
    return {
        "tag": tag.group(1).upper(),
        "type": "text",
        "label": label,
        "destination": frame_url,
        "frame_url": frame_url,
        "required": True,
        "metadata_source": "stagehand_accessibility_snapshot",
    }
