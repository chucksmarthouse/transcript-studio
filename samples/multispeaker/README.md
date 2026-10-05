# Multi-speaker test clips

Excerpts from NASA Johnson Space Center's podcast *Houston We Have a Podcast*.
They are NASA-produced media, generally not subject to US copyright (see
NASA's media usage guidelines), and are used here only as local test data.
The MP3 is an excerpt cut from the published episode audio.

| Clip | Speakers | Length | Source |
|---|---|---|---|
| `nasa-podcast-ep434-2speakers.mp3` | Leah Cheshier (host, F), Debra O’Connell (F) | 4:37 | [ep. 434 “Artemis III Training”](https://www.nasa.gov/podcasts/houston-we-have-a-podcast/artemis-iii-training/), 23:41.6–28:19.0 |

The clip is a hard case on purpose: two women's voices with quick back-and-forth and interjections.

`*.reference.md` holds the ground truth: NASA's official speaker-labelled
transcript, with turn start times aligned automatically to about ±1 s.
`*.format-reference.md` is an export of the clip in the app's markdown format
(made by `tools/make_format_reference.py`); the tests round-trip it.
