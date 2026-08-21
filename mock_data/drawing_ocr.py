"""Mocked drawing store: OCR text for each part's engineering drawing.

Stands in for retrieving the drawing PDF through KVS and running OCR over it.
The text below is fabricated, but it is the ONLY place a part's soft foot is
visible -- the KVS catalog deliberately carries no feature data, so the feature
can only be established by processing the drawing.

PRODUCTION SWAP-IN: replace `get_drawing_ocr` with a real KVS document fetch
plus an OCR pass (Azure Document Intelligence, Tesseract, ...). In production
the extracted text would then be handed to a general-purpose LLM together with
a description of how to infer a soft foot. In THIS demo no LLM is involved in
that step -- the rule is applied deterministically in
`tools/drawing_analysis.py` -- so the demo stays reproducible.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any

# The one part in the whole catalog whose drawing shows a tailored hardness
# profile (two different HV callouts), i.e. a soft foot.
SOFT_FOOT_PARTS = frozenset({"10A.507.109"})

_TOLERANCE_NOTES = [
    "GENERAL TOL. ISO 2768-mK",
    "GENERAL TOL. ISO 2768-fH",
    "UNSPECIFIED RADII R3",
    "EDGE BREAK 0.3 x 45 DEG",
]

_PROCESS_NOTES = [
    "HOT FORMING - DIRECT PROCESS",
    "PRESS HARDENED, AlSi COATED",
    "LASER TRIM AFTER FORMING",
    "SPRINGBACK COMPENSATED DIE",
]

_INSPECTION_NOTES = [
    "CMM INSPECTION PER MEASURING PLAN",
    "SURFACE CLASS B, NO VISIBLE CRACKS",
    "COATING THICKNESS 20-35 um",
]

# Light, deterministic OCR artefacts -- a scan is never perfectly clean. These
# are applied only to prose lines, never to a hardness callout, so the
# soft-foot rule stays reproducible.
_OCR_NOISE = {"O": "0", "I": "1", "S": "5"}


def _degrade(line: str, rng: random.Random) -> str:
    """Introduce a plausible OCR misread into a line of prose."""
    if rng.random() > 0.25:
        return line
    chars = list(line)
    positions = [i for i, c in enumerate(chars) if c in _OCR_NOISE]
    if not positions:
        return line
    i = rng.choice(positions)
    chars[i] = _OCR_NOISE[chars[i]]
    return "".join(chars)


def get_drawing_ocr(part_number: str, drawing_ref: str | None = None) -> dict[str, Any]:
    """Return the OCR text for one part's drawing.

    Args:
        part_number: KVS part number, e.g. "10A.507.109".
        drawing_ref: Optional drawing reference from the KVS record.

    Returns:
        The OCR result: engine, page count, a scan-quality score, and the
        extracted text as a list of lines.
    """
    pn = part_number.strip().upper()
    # Stable across processes: Python's str hash is salted per run, which
    # would make the OCR text differ between demo runs.
    seed = int.from_bytes(hashlib.sha256(pn.encode()).digest()[:4], "big")
    rng = random.Random(seed)

    model, component, part_id = (pn.split(".") + ["", "", ""])[:3]

    lines: list[str] = [
        f"DRAWING {drawing_ref or 'DRW-' + pn.replace('.', '-')}",
        f"PART NO. {pn}",
        "B-PILLAR REINFORCEMENT" if component == "507" else "STRUCTURAL COMPONENT",
        f"VEHICLE MODEL {model}",
        "SCALE 1:2   SHEET 1 OF 2   THIRD ANGLE PROJECTION",
        "MATERIAL: 22MnB5 t=1.8mm",
        rng.choice(_TOLERANCE_NOTES),
        rng.choice(_PROCESS_NOTES),
    ]

    # --- Hardness callouts: the load-bearing content for the soft-foot rule ---
    if pn in SOFT_FOOT_PARTS:
        # Tailored hardness profile: hardened upper section, soft foot zone.
        lines += [
            "HARDNESS ZONE A (UPPER SECTION): 480 +/- 30 HV10",
            "HARDNESS ZONE C (FOOT AREA): 200 +/- 20 HV10",
            "TRANSITION ZONE B: GRADUAL, LENGTH 60mm",
            "NOTE: TAILORED TEMPERING - SEE PROCESS SHEET PS-4471",
        ]
    else:
        # Uniform hardness through the whole part: no soft foot.
        lines += [
            f"HARDNESS (ALL ZONES): {rng.choice([450, 470, 480, 495, 505])} +/- 30 HV10",
            "NOTE: UNIFORM PRESS HARDENING",
        ]

    # --- Feature callouts ---
    hole_d = rng.choice([10.5, 12.0, 13.5, 14.0])
    lines += [
        f"HOLE D{hole_d} THRU - 4 PLACES - POSITION TOL 0.5",
        f"HOLE D{rng.choice([8.0, 9.0])} THRU - 2 PLACES - DRAIN",
        f"WELD FLANGE WIDTH {rng.choice([14, 16, 18])}mm",
        f"DRAW DEPTH {rng.choice([42, 48, 55, 61])}mm MAX",
        rng.choice(_INSPECTION_NOTES),
        "RELEASED FOR PRODUCTION",
    ]

    text = [_degrade(line, rng) if "HV" not in line else line for line in lines]

    return {
        "partNumber": pn,
        "drawingRef": drawing_ref or f"DRW-{pn.replace('.', '-')}",
        "source": "KVS document store (mock) + OCR (mock)",
        "engine": "mock-ocr-v1",
        "pageCount": 2,
        "scanQualityPct": round(88.0 + rng.random() * 10.0, 1),
        "ocrText": text,
    }
