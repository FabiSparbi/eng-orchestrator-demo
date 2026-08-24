"""KI-Protokoll: from captured notes, photos and voice notes to the customer's table.

The one call the app's "KI-Protokoll" button makes is
`ki_protokoll.service.ki_protokoll_erzeugen`. Everything else here supports it:

    schema.py        the customer's table format, loaded from JSON
    extraction.py    notes/photos/voice -> cells, each with confidence + evidence
    llm.py           the same step via the shared Foundry deployment
    completeness.py  which mandatory cells are still open, and why
    notification.py  the message and the to-do list the user gets back
    render.py        the table (Markdown/CSV) and the optional prose protocol
    store.py         the inspection export and the run history
"""

from ki_protokoll.service import ki_protokoll_button, ki_protokoll_erzeugen

__all__ = ["ki_protokoll_erzeugen", "ki_protokoll_button"]
